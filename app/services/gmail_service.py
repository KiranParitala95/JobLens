import base64
import html
import logging
import os
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app import db


GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Gmail leaves Spam and Trash out of searches unless asked. JobLens asks for
# both (includeSpamTrash on every search below), so mail the user deleted
# before it was scanned - still held in Gmail's Trash for 30 days - is
# found too, and so are real replies that landed in Spam.


class GoogleReconnectNeeded(Exception):
    """The saved Google connection can't be used any more. Caught in the
    routes, which send the user back through Google instead of crashing."""


def is_valid_zone(tz_name):
    """True only for a real IANA time zone name such as "Asia/Kolkata"."""
    if not tz_name or not isinstance(tz_name, str):
        return False
    try:
        ZoneInfo(tz_name)
        return True
    except Exception:
        return False


def get_zone(tz_name):
    """The time zone for a saved name. A missing or unusable name gives
    UTC, so a bad value can never crash a page."""
    return ZoneInfo(tz_name) if is_valid_zone(tz_name) else ZoneInfo("UTC")


def _day_boundaries_utc_timestamps(target_date, tz_name):
    """Convert one calendar day, in the GIVEN timezone, into the UTC
    epoch timestamps Gmail's search query needs. Midnight-to-midnight in
    that person's own timezone, not UTC and not a fixed assumption, so
    "Oct 1" means Oct 1 as THEY actually experience it - correct no
    matter where in the world they're using the app from.

    Falls back to UTC if a timezone hasn't been detected for this user
    yet (the brief window between account creation and the browser
    reporting it back).
    """
    tz = get_zone(tz_name)
    local_start = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=tz,
    )
    local_end = local_start + timedelta(days=1)
    return int(local_start.timestamp()), int(local_end.timestamp())


def _build_credentials(user, client_id, client_secret):
    """Turn the tokens saved on a User row into a Credentials object the
    Gmail client library understands, refreshing the access token first
    if it's expired.
    """
    # No Gmail connection saved at all (e.g. an email/password account),
    # or the short-lived token has expired and there is no refresh token
    # to renew it: the user needs to go through Google once more.
    if not user.google_access_token and not user.google_refresh_token:
        raise GoogleReconnectNeeded()
    expired = bool(user.token_expiry and user.token_expiry <= datetime.utcnow())
    if expired and not user.google_refresh_token:
        raise GoogleReconnectNeeded()

    credentials = Credentials(
        token=user.google_access_token,
        refresh_token=user.google_refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
    )

    if expired:
        try:
            credentials.refresh(Request())
        except RefreshError:
            # Token revoked or otherwise no longer valid.
            raise GoogleReconnectNeeded()
        user.google_access_token = credentials.token
        db.session.commit()

    return credentials


def fetch_message_snippet(service, message_id):
    """Fetch one message's snippet by ID. Called only for message IDs
    the caller has already confirmed aren't saved yet."""
    full_message = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata")
        .execute()
    )
    return full_message.get("snippet", "")


def _decode_part(data):
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _html_to_text(markup):
    """Plain text from an HTML email body: drop scripts/styles, turn block
    tags into line breaks, strip the rest of the tags."""
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", markup)
    markup = re.sub(r"(?s)<[^>]+>", " ", markup)
    return html.unescape(markup)


def _collect_body(payload):
    """Walk a message's parts and return (plain_text, html_text)."""
    plain, markup = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            plain.append(_decode_part(data))
        elif mime == "text/html" and data:
            markup.append(_decode_part(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(plain), _html_to_text("\n".join(markup))


def _gmail_auth_results(header_list):
    """Gmail's own verdict on whether the sender is genuine, read from the
    Authentication-Results header that Gmail itself adds (the one starting
    with mx.google.com - any other copy could have been written by the
    sender). Returns e.g. {"spf": "pass", "dkim": "pass", "dmarc": "pass"},
    or {} when Gmail recorded nothing."""
    for header in header_list:
        value = header.get("value", "")
        if (
            header.get("name", "").lower() == "authentication-results"
            and value.strip().lower().startswith("mx.google.com")
        ):
            results = {}
            for check, outcome in re.findall(r"\b(spf|dkim|dmarc)=(\w+)", value.lower()):
                # An email can carry several DKIM signatures; one pass is enough.
                if check not in results or outcome == "pass":
                    results[check] = outcome
            return results
    return {}


def fetch_message_content(service, message_id):
    """Fetch one message in full: sender, recipient, subject, labels, the
    short snippet and the body text. The body is only used for phrase
    matching on the server - the caller never saves it or sends all of it
    to the AI."""
    message = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, markup = _collect_body(payload)
    return {
        "snippet": html.unescape(message.get("snippet", "")),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "reply_to": headers.get("reply-to", ""),
        "auth": _gmail_auth_results(payload.get("headers", [])),
        "labels": message.get("labelIds", []),
        "body": (plain + "\n" + markup).strip(),
    }


def build_gmail_service(credentials):
    """Build a Gmail API client from already-prepared credentials, for
    the caller to use with fetch_message_snippet."""
    return build("gmail", "v1", credentials=credentials)


def count_messages_on_date(service, target_date, tz_name=None):
    """Count how many messages exist on one specific calendar day, in
    the given timezone.

    Uses Gmail's own resultSizeEstimate from a single list() call scoped
    to that one day - no message content is fetched, so this is cheap
    even when called once per day across a whole month for the calendar
    view. This is what keeps building the calendar fast, in contrast to
    fetching full message details for every day's mail up front.
    """
    day_start, day_end = _day_boundaries_utc_timestamps(target_date, tz_name)
    query = f"after:{day_start} before:{day_end}"

    response = service.users().messages().list(
        userId="me", q=query, includeSpamTrash=True
    ).execute()
    return response.get("resultSizeEstimate", 0)


def list_message_ids_on_date(user, client_id, client_secret, target_date):
    """List every Gmail message ID received on one specific calendar day.

    Scoped to a single day so processing "just this date" (per the
    date-picker flow) only ever touches a small, bounded batch of mail,
    instead of everything since signup.
    """
    credentials = _build_credentials(user, client_id, client_secret)
    service = build("gmail", "v1", credentials=credentials)

    day_start, day_end = _day_boundaries_utc_timestamps(target_date, user.timezone)
    query = f"after:{day_start} before:{day_end}"

    message_ids = []
    page_token = None
    while True:
        response = (
            service.users()
            .messages()
            .list(
                userId="me", q=query, pageToken=page_token,
                includeSpamTrash=True,
            )
            .execute()
        )
        message_ids.extend(m["id"] for m in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids, credentials


# ---------------------------------------------------------------------------
# Sending: the password-reset email, through Gmail's SMTP server.
# The sender address and its Gmail app password come from .env only
# (MAIL_USERNAME, MAIL_APP_PASSWORD) - never from the code.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def mail_is_configured():
    return bool(os.getenv("MAIL_USERNAME") and os.getenv("MAIL_APP_PASSWORD"))


def send_reset_email(to_address, name, link, minutes):
    """Returns True if the mail was handed to Gmail, False otherwise."""
    if not mail_is_configured():
        logger.warning("MAIL_USERNAME / MAIL_APP_PASSWORD not set - reset email not sent.")
        return False

    sender = os.getenv("MAIL_USERNAME")
    password = os.getenv("MAIL_APP_PASSWORD").replace(" ", "")

    msg = EmailMessage()
    msg["Subject"] = "Reset your JobLens password"
    msg["From"] = f"JobLens <{sender}>"
    msg["To"] = to_address
    msg.set_content(
        f"Hello {name},\n\n"
        "We received a request to reset your JobLens password.\n"
        f"Open this link to choose a new password (valid for {minutes} minutes, one use only):\n\n"
        f"{link}\n\n"
        "If you did not ask for this, you can ignore this email. "
        "Your password will not change.\n\n"
        "JobLens"
    )

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        return True
    except Exception:
        logger.exception("Could not send the reset email.")
        return False
