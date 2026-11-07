import json
import re
from datetime import datetime

from app.agents.classifier import chat

# These categories are not about a specific job the user applied to, so
# there is no company/role/deadline to pull out - they only get a source
# name and a one-line description.
DESCRIBE_ONLY_CATEGORIES = ("general", "recruiter_outreach")

# Every received job-search mail gets a sender check, so the user can see
# whether it came from the company itself, a recruiting platform or agency,
# or somewhere doubtful. Not for "general" (not job-related) and not for
# "follow_up_sent" (the user wrote that mail).
SENDER_CHECK_CATEGORIES = (
    "job_applied", "rejection", "interview_scheduled", "assessment_invite",
    "offer_received", "recruiter_outreach",
)
SENDER_CHECK_VALUES = ("direct", "platform", "unverified")

_EMPTY = {
    "company": None, "role": None, "deadline": None, "urgency": None,
    "source": None, "summary": None, "sender_check": None, "sender_note": None,
}


def _clean_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text.strip()


def _parse_deadline(deadline_text):
    """Convert the model's deadline (asked for as YYYY-MM-DD) into a real
    date object the database can store. Returns None on any parse failure
    rather than raising - a missing deadline is far less harmful than
    crashing the whole extraction over one unparseable date string.
    """
    if not deadline_text or not isinstance(deadline_text, str):
        return None
    try:
        return datetime.strptime(deadline_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _ask(prompt):
    try:
        data = json.loads(_clean_json(chat(prompt)))
    except json.JSONDecodeError:
        return {}
    # The model is asked for a JSON object; anything else is treated as empty.
    return data if isinstance(data, dict) else {}


def _text(value, limit):
    """A clean, length-limited string from a model answer, or None. The
    model occasionally returns a list, number or "null" text instead of a
    string, and the database would reject those."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in ("null", "none", "n/a"):
        return None
    return value[:limit]


def _sender_rules(reply_to, auth_text):
    return f"""
Also judge WHO SENT this email. Reply-To address: {reply_to or "same as sender"}.
Gmail's own sender check (SPF/DKIM/DMARC): {auth_text}.
"sender_check" must be one of:
- "direct": sent from the employer's own domain or its official HR/careers address
- "platform": sent by a hiring platform, applicant tracking system or a
  recruitment/staffing agency acting for the employer
- "unverified": anything doubtful - a free personal email address posing as
  a company, a reply address that differs or looks unrelated, a domain that
  imitates a company's name, a failed Gmail sender check, or any request for
  money, fees, gift cards or bank details
"sender_note": ONE short sentence (at most 12 words) giving the reason."""


def _final_check(data, auth):
    """Validate the AI's sender answer and apply Gmail's failed check."""
    check = data.get("sender_check")
    note = _text(data.get("sender_note"), 200)
    if check not in SENDER_CHECK_VALUES:
        check, note = "unverified", "The sender could not be checked."
    # A failed Gmail check is a fact, not an opinion: the AI cannot talk it
    # into "direct" or "platform".
    if auth.get("dmarc") == "fail" or auth.get("dkim") == "fail":
        check, note = "unverified", "Failed Gmail's sender check."
    return check, note


def extract_details(who, subject, snippet, evidence, category, reply_to=None, auth=None):
    """Agent 2: pull structured details out of an already-classified email.

    The AI is given only: who it is from (or, for sent mail, who it was
    sent to), the subject, the short preview, and the few sentences around
    the matched phrase. Never the whole body.
    """
    auth = auth or {}
    auth_text = ", ".join(f"{k}={v}" for k, v in auth.items()) or "not available"
    evidence_text = "\n".join(f"- {line}" for line in evidence) or "(none)"
    email_text = (
        f"From/To: {who}\nSubject: {subject}\nPreview: {snippet}\n"
        f"Key sentences:\n{evidence_text}"
    )

    if category in DESCRIBE_ONLY_CATEGORIES:
        about = (
            "which recruiter or company emailed, and about what role"
            if category == "recruiter_outreach"
            else "what the email is about"
        )
        check_it = category in SENDER_CHECK_CATEGORIES
        extra = ""
        extra_rules = ""
        if check_it:
            extra_rules = _sender_rules(reply_to, auth_text)
            extra = ''',
  "sender_check": "direct, platform or unverified",
  "sender_note": "short reason"'''
        data = _ask(f"""You are describing an email for a job search tracking app.
{extra_rules}
Respond in valid JSON only, no markdown:
{{
  "source": "short name of who or what this email came from (a company,
    website or platform), based on the sender name and address",
  "summary": "ONE plain sentence of at most 15 words saying {about}"{extra}
}}

Email:
{email_text}""")
        result = {
            **_EMPTY,
            "source": _text(data.get("source"), 200),
            "summary": _text(data.get("summary"), 300),
        }
        if check_it:
            result["sender_check"], result["sender_note"] = _final_check(data, auth)
        return result

    sent_note = (
        "\nThis email was WRITTEN BY the candidate (a follow-up they sent). "
        "The company is the employer they are writing to."
        if category == "follow_up_sent" else ""
    )

    check_sender = category in SENDER_CHECK_CATEGORIES
    sender_fields = ""
    sender_rules = ""
    if check_sender:
        sender_rules = _sender_rules(reply_to, auth_text)
        sender_fields = ''',
  "sender_check": "direct, platform or unverified",
  "sender_note": "short reason"'''

    data = _ask(f"""You are extracting structured details from a job-search
related email, already classified as: {category}{sent_note}

Rules for "company": use the employer's full official name. The sender
may be a hiring platform, payroll or HR system rather than the employer -
the employer is the organization the candidate applied to, named in the
subject or the text. Never use a shortened or partial name.
Rules for "role": the job title only, without requisition or reference
numbers.
{sender_rules}
Respond in valid JSON only, no markdown:
{{
  "company": "employer's full name, or null if not stated",
  "role": "job title/role mentioned, or null if not stated",
  "deadline": "any deadline mentioned (assessment due date, interview
    date, response-needed-by date), converted to YYYY-MM-DD format,
    or null if no deadline is mentioned",
  "urgency": "one of: high, medium, low - based on how time-sensitive
    this email is (e.g. a deadline within 2 days is high, a general
    update with no deadline is low)"{sender_fields}
}}

Email:
{email_text}""")

    result = {
        **_EMPTY,
        "company": _text(data.get("company"), 120),
        "role": _text(data.get("role"), 120),
        "deadline": _parse_deadline(data.get("deadline")),
        "urgency": _text(data.get("urgency"), 20),
    }

    if check_sender:
        result["sender_check"], result["sender_note"] = _final_check(data, auth)

    return result
