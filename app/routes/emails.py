import calendar
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from email.utils import parseaddr

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.agents.classifier import classify_email
from app.agents.extractor import extract_details
from app.agents.rules import match_received, match_sent
from app.models import Email
from app.services.gmail_service import (
    GoogleReconnectNeeded,
    build_gmail_service,
    get_zone,
    count_messages_on_date,
    fetch_message_content,
    list_message_ids_on_date,
)

emails_bp = Blueprint("emails", __name__, url_prefix="/emails")

logger = logging.getLogger(__name__)

# How many emails are read and analysed at the same time.
PARALLEL_EMAILS = 4


# Hiring platforms that send mail on behalf of employers. For an address on
# one of these, the part before the "@" is the useful name; for any other
# address the company name in the domain is.
PLATFORM_DOMAINS = (
    "myworkday.com", "workday.com", "dayforce.com", "icims.com",
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com",
    "workable.com", "smartrecruiters.com", "rippling.com", "adp.com",
    "taleo.net", "oraclecloud.com", "successfactors.com", "jobvite.com",
    "bamboohr.com", "paylocity.com", "ultipro.com", "saashr.com",
)


def _readable_name(value):
    """Turns a sender that is only an email address into a readable name.
    A real name (no "@") is returned unchanged; empty gives "Unknown sender"."""
    value = (value or "").strip()
    if not value:
        return "Unknown sender"
    if "@" not in value:
        return value

    local, _, domain = value.rpartition("@")
    local = local.strip("<> ")
    domain = domain.strip("<> ").lower()
    parts = domain.split(".")
    base = ".".join(parts[-2:]) if len(parts) >= 2 else domain

    if base in PLATFORM_DOMAINS or domain in PLATFORM_DOMAINS:
        name = local
    else:
        # Addresses such as acme.co.uk: the company is before "co".
        if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac"):
            name = parts[-3]
        else:
            name = parts[-2] if len(parts) >= 2 else domain
        if name.islower():
            name = name.capitalize()
    return name.replace("_", " ").replace("-", " ").strip() or "Unknown sender"



def _parse_day(date_str):
    """The day from the address (YYYY-MM-DD) for the signed-in user.
    Anything that is not a real date, a day before the user signed up, or
    a day in the future shows a "not found" page - the same days the
    calendar greys out - instead of crashing or scanning them."""
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        abort(404)
    user_tz = get_zone(current_user.timezone)
    today = datetime.now(user_tz).date()
    signup_date = current_user.created_at.replace(tzinfo=timezone.utc).astimezone(user_tz).date()
    if target < signup_date or target > today:
        abort(404)
    return target


def _analyse_message(credentials, message_id):
    """Fetch one email and work out its category and details. Runs in a
    worker thread, so it never touches the database or the Flask request.
    Returns (message, category, details), or None when the mail is skipped."""
    service = build_gmail_service(credentials)  # one service per thread
    message = fetch_message_content(service, message_id)
    if "DRAFT" in message["labels"]:
        return None

    subject = message["subject"]
    snippet = message["snippet"]

    if "SENT" in message["labels"]:
        # Mail the user wrote: only kept when it reads like a follow-up.
        match = match_sent(subject, message["body"])
        if match is None:
            return None
        category, evidence = match
        who = f"Sent by the user to: {message['to']}"
    else:
        # Phrases first (decided on the server from the full text). Only if
        # nothing matches is the AI asked, and then it sees just the sender,
        # subject and short preview.
        match = match_received(subject, message["body"])
        if match:
            category, evidence = match
        else:
            category = classify_email(message["from"], subject, snippet)
            evidence = []
        who = message["from"]

    details = extract_details(
        who, subject, snippet, evidence, category,
        reply_to=message["reply_to"], auth=message["auth"],
    )
    return message, category, details



@emails_bp.errorhandler(GoogleReconnectNeeded)
def handle_reconnect(error):
    """The saved Google connection is unusable: send the user through
    Google once more instead of showing an error page. Background
    (fetch) requests get JSON so the page can do the redirect itself."""
    reconnect_url = url_for("google_auth.reconnect")
    if request.method == "POST":
        return jsonify({"reconnect_url": reconnect_url}), 401
    return redirect(reconnect_url)


CATEGORY_LABELS = {
    "job_applied": "Applications",
    "rejection": "Rejections",
    "interview_scheduled": "Interviews",
    "assessment_invite": "Assessments",
    "offer_received": "Offers",
    "follow_up_sent": "Follow-ups sent",
    "recruiter_outreach": "Recruiter outreach",
    "general": "Other",
}

# Cards on the day page, in this order. General and Recruiter outreach
# are shown separately below them, and neither counts as "real" activity.
REAL_CATEGORIES = (
    "job_applied",
    "rejection",
    "interview_scheduled",
    "assessment_invite",
    "offer_received",
    "follow_up_sent",
)
NOT_REAL_ACTIVITY = ("general", "recruiter_outreach", "follow_up_sent")


def _sender_name(from_header):
    """Display name from a From header, falling back to the address."""
    name, address = parseaddr(from_header or "")
    return (name or address or "Unknown sender").strip()


def _format_date(target_date):
    """Build a readable date string without any OS-specific strftime
    codes. %-d ("day without leading zero") is a Linux/Mac-only
    extension and raises ValueError on Windows - building the string
    manually avoids that whole class of platform bug entirely.
    """
    return f"{target_date.strftime('%B')} {target_date.day}, {target_date.year}"


@emails_bp.route("/calendar")
@login_required
def calendar_view():
    """Show a month calendar since the user's signup date. Each day
    shows a rough mail count (cheap - no message content fetched) so
    the user can pick one day at a time to actually process, rather
    than the whole backlog processing at once on login.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    # Both "today" and the signup date are computed in THIS user's own
    # detected timezone (reported automatically by their browser), not
    # UTC and not one fixed assumption for everybody - correct no matter
    # where they're actually using the app from.
    user_tz = get_zone(current_user.timezone)
    today = datetime.now(user_tz).date()

    signup_date = current_user.created_at.replace(tzinfo=timezone.utc).astimezone(user_tz).date()
    first_viewable = date(signup_date.year, signup_date.month, 1)
    current_viewable = date(today.year, today.month, 1)

    # The month comes from the address (?year=&month=). A missing, invalid
    # or out-of-range value never crashes: it falls back to this month,
    # and is kept between the signup month and the current month.
    try:
        viewing = date(
            request.args.get("year", type=int), request.args.get("month", type=int), 1
        )
    except (TypeError, ValueError):
        viewing = current_viewable
    viewing = min(max(viewing, first_viewable), current_viewable)
    year, month = viewing.year, viewing.month

    # An account made with email + password has no Google connection yet.
    # Instead of bouncing the user to Google, the calendar still opens and
    # shows a "Connect Gmail" card (no mail is read until they connect).
    gmail_connected = bool(
        current_user.google_access_token or current_user.google_refresh_token
    )
    service = None
    if gmail_connected:
        from app.services.gmail_service import _build_credentials
        from googleapiclient.discovery import build as build_service

        credentials = _build_credentials(current_user, client_id, client_secret)
        service = build_service("gmail", "v1", credentials=credentials)

    cal = calendar.Calendar(firstweekday=6)
    grid = cal.monthdatescalendar(year, month)

    # Emails already processed and saved for each day. Gmail's live count
    # drops when the user deletes mail, so the calendar shows whichever
    # is larger - a day's number never disappears just because mail was
    # deleted in Gmail afterwards.
    saved_rows = (
        db.session.query(Email.date_received, func.count(Email.id))
        .filter(
            Email.user_id == current_user.id,
            Email.date_received >= grid[0][0],
            Email.date_received <= grid[-1][-1],
        )
        .group_by(Email.date_received)
        .all()
    )
    saved_counts = {day: total for day, total in saved_rows}

    weeks = []
    for week in grid:
        week_data = []
        for day in week:
            in_month = day.month == month
            before_signup = day < signup_date
            after_today = day > today
            count = 0
            if in_month and not before_signup and not after_today:
                live_count = (
                    count_messages_on_date(service, day, current_user.timezone)
                    if service else 0
                )
                count = max(live_count, saved_counts.get(day, 0))
            week_data.append({
                "date": day,
                "in_month": in_month,
                "disabled": before_signup or after_today or not in_month,
                "count": count,
            })
        weeks.append(week_data)

    return render_template(
        "emails/calendar.html",
        gmail_connected=gmail_connected,
        weeks=weeks,
        month_name=viewing.strftime("%B %Y"),
        year=year,
        month=month,
        show_prev=viewing > first_viewable,
        show_next=viewing < current_viewable,
        prev_year=year if month > 1 else year - 1,
        prev_month=month - 1 if month > 1 else 12,
        next_year=year if month < 12 else year + 1,
        next_month=month + 1 if month < 12 else 1,
    )


@emails_bp.route("/review/<date_str>")
@login_required
def review(date_str):
    """Loads instantly and shows a spinner while the real processing
    happens in the background via JS - a plain server-rendered page
    can't show a custom loading state DURING its own single request,
    since the browser just shows its own generic spinner until the
    whole response arrives. Splitting into "show spinner" -> "do the
    work" -> "show results" is what makes a real spinner possible.
    """
    target_date = _parse_day(date_str)
    return render_template(
        "emails/scanning.html",
        date_str=date_str,
        formatted_date=_format_date(target_date),
    )


@emails_bp.route("/process/<date_str>", methods=["POST"])
@login_required
def process(date_str):
    """Does the actual Gmail fetch + classify + extract + save work for
    one day. Called via JS from the spinner page, not visited directly -
    returns JSON so the page's JS knows when to move on to the results.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    target_date = _parse_day(date_str)

    message_ids, credentials = list_message_ids_on_date(
        current_user, client_id, client_secret, target_date
    )

    already_saved_ids = {
        row.gmail_message_id
        for row in Email.query.filter(
            Email.user_id == current_user.id,
            Email.gmail_message_id.in_(message_ids),
        ).all()
    }
    new_ids = [mid for mid in message_ids if mid not in already_saved_ids]

    saved_count = 0
    failed_count = 0
    user_id = current_user.id

    # Several emails are read and analysed at the same time (the slow part is
    # waiting for Gmail and the AI). Saving to the database stays here, one
    # email at a time, so the database is never used from two threads.
    # One bad email (an AI timeout, an unusual message, mail removed from
    # Gmail mid-scan) must not lose the rest of the day. A failed email is
    # NOT saved, so opening the day again simply tries it again.
    with ThreadPoolExecutor(max_workers=PARALLEL_EMAILS) as pool:
        jobs = {
            pool.submit(_analyse_message, credentials, mid): mid
            for mid in new_ids
        }
        for job in as_completed(jobs):
            message_id = jobs[job]
            try:
                result = job.result()
                if result is None:
                    continue  # draft, or sent mail that is not a follow-up
                message, category, details = result
                db.session.add(Email(
                    user_id=user_id,
                    gmail_message_id=message_id,
                    category=category,
                    company=details["company"],
                    role=details["role"],
                    deadline=details["deadline"],
                    urgency=details["urgency"],
                    sender_check=details["sender_check"],
                    sender_note=details["sender_note"],
                    from_trash="TRASH" in message["labels"],
                    from_spam="SPAM" in message["labels"],
                    sender=(details["source"] or _sender_name(message["from"]))[:200],
                    summary=(details["summary"] or message["subject"] or "")[:300],
                    raw_snippet=message["snippet"],
                    date_received=target_date,
                ))
                db.session.commit()
                saved_count += 1
            except GoogleReconnectNeeded:
                raise
            except Exception:
                db.session.rollback()
                logger.exception("Could not process message %s", message_id)
                failed_count += 1

    return jsonify({"new_count": saved_count, "failed_count": failed_count})


@emails_bp.route("/results/<date_str>")
@login_required
def results(date_str):
    """Shows one day's already-saved results. Pure database read, no
    Gmail or agent calls - fast regardless of how the processing step
    performed, since by this point everything is already saved.
    """
    target_date = _parse_day(date_str)
    failed_count = request.args.get("failed", type=int, default=0)

    day_rows = Email.query.filter_by(
        user_id=current_user.id, date_received=target_date
    ).all()
    counts = {}
    category_details = {}
    general_items = []
    recruiter_items = []
    for row in day_rows:
        counts[row.category] = counts.get(row.category, 0) + 1

        if row.category in ("general", "recruiter_outreach"):
            item = {
                "source": _readable_name(row.sender),
                "summary": row.summary or "(no description available)",
                "from_spam": bool(row.from_spam),
                "from_trash": bool(row.from_trash),
                "sender_check": row.sender_check,
                "sender_note": row.sender_note,
            }
            if row.category == "general":
                general_items.append(item)
            else:
                recruiter_items.append(item)
        else:
            category_details.setdefault(row.category, []).append({
                # When the mail never names the employer, show who it came
                # from (recruiter, staffing firm or assessment platform)
                # instead of a blank "Company not identified".
                "company": row.company or _readable_name(row.sender),
                "company_missing": not row.company,
                "role": row.role,
                "sender_check": row.sender_check,
                "sender_note": row.sender_note,
                "from_spam": bool(row.from_spam),
                "from_trash": bool(row.from_trash),
            })

    ordered_categories = [c for c in REAL_CATEGORIES if c in counts]
    real_total = sum(
        count for cat, count in counts.items() if cat not in NOT_REAL_ACTIVITY
    )
    general_total = counts.get("general", 0)
    spam_total = sum(1 for row in day_rows if row.from_spam)

    return render_template(
        "emails/results.html",
        target_date=target_date,
        formatted_date=_format_date(target_date),
        counts=counts,
        category_labels=CATEGORY_LABELS,
        category_details=category_details,
        ordered_categories=ordered_categories,
        failed_count=failed_count,
        spam_total=spam_total,
        general_items=general_items,
        recruiter_items=recruiter_items,
        real_total=real_total,
        general_total=general_total,
    )
