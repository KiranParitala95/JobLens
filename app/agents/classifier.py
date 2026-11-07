import json
import os
import re
import threading
import time

import httpx
from dotenv import load_dotenv
from groq import Groq, RateLimitError

load_dotenv()

# ---------------------------------------------------------------------------
# AI providers, tried in this order:
#   1. Groq key 1, Groq key 2, ... (GROQ_API_KEYS, comma separated; the old
#      single GROQ_API_KEY still works). Calls are shared across the keys.
#   2. Hugging Face (HF_TOKEN), only when every Groq key is rate limited.
#   3. Wait for the earliest Groq key to be free again (short waits only).
# Keys live only in .env - never in the code, never on GitHub.
# ---------------------------------------------------------------------------
GROQ_MODEL = "openai/gpt-oss-120b"
HF_URL = "https://router.huggingface.co/v1/chat/completions"
HF_MODEL = os.getenv("HF_MODEL", "openai/gpt-oss-120b")
HF_TIMEOUT_SECONDS = 60

MAX_TRIES = 5
FALLBACK_WAIT_SECONDS = 5
MAX_WAIT_SECONDS = 30


def _load_groq_keys():
    raw = os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY") or ""
    keys = [key.strip() for key in raw.split(",") if key.strip()]
    return list(dict.fromkeys(keys))  # drop duplicates, keep order


GROQ_KEYS = _load_groq_keys()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
_clients = [Groq(api_key=key) for key in GROQ_KEYS]
_blocked_until = [0.0] * len(_clients)  # time when each key may be used again
_next_key = 0
_lock = threading.Lock()


def _wait_seconds(error):
    """How long Groq asked us to wait, from text like 'try again in 1.88s'
    or 'try again in 2m3.5s'. Falls back to a fixed pause."""
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(error))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2)) + 0.5
    return FALLBACK_WAIT_SECONDS


def _pick_key():
    """Next Groq key that is not rate limited (round-robin), or None."""
    global _next_key
    with _lock:
        now = time.time()
        for step in range(len(_clients)):
            index = (_next_key + step) % len(_clients)
            if _blocked_until[index] <= now:
                _next_key = (index + 1) % len(_clients)
                return index
    return None


def _block_key(index, seconds):
    with _lock:
        _blocked_until[index] = time.time() + seconds


def _earliest_free_in():
    with _lock:
        return max(0.0, min(_blocked_until) - time.time())


def _ask_huggingface(prompt, temperature):
    response = httpx.post(
        HF_URL,
        headers={"Authorization": "Bearer " + HF_TOKEN},
        json={
            "model": HF_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        },
        timeout=HF_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def chat(prompt, temperature=0):
    """Send one prompt to the AI and return the reply text. Used by all
    agents. When a Groq key is rate limited, the next key takes over at once;
    when all are limited, Hugging Face is used if a token is set."""
    if not _clients and not HF_TOKEN:
        raise RuntimeError(
            "No AI key found. Set GROQ_API_KEYS (or GROQ_API_KEY) in .env."
        )

    last_error = None
    for _attempt in range(MAX_TRIES):
        index = _pick_key()
        if index is not None:
            try:
                response = _clients[index].chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                )
                return response.choices[0].message.content
            except RateLimitError as error:
                last_error = error
                _block_key(index, _wait_seconds(error))
                print(f"[ai] Groq key {index + 1} rate limited - switching.")
                continue  # try the next key straight away, no sleeping

        # Every Groq key is limited right now.
        if HF_TOKEN:
            try:
                print("[ai] All Groq keys limited - using Hugging Face.")
                return _ask_huggingface(prompt, temperature)
            except Exception as error:
                last_error = error
                print(f"[ai] Hugging Face failed: {error}")

        wait = _earliest_free_in() if _clients else FALLBACK_WAIT_SECONDS
        if wait > MAX_WAIT_SECONDS:
            break
        time.sleep(wait + 0.1)

    if last_error:
        raise last_error
    raise RuntimeError("The AI service is busy. Please try again.")


VALID_CATEGORIES = (
    "job_applied",
    "rejection",
    "interview_scheduled",
    "assessment_invite",
    "offer_received",
    "recruiter_outreach",
    "general",
)


def _clean_json(text):
    """Pull the JSON object out of the model's reply, in case it wraps the
    response in markdown fences despite being asked not to."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text.strip()


def classify_email(sender, subject, snippet):
    """Backup agent: used only when no phrase matched. Reads the sender,
    subject and short preview and returns a category.

    Defaults to "general" on any parsing failure or an unrecognized
    category name - a mislabeled promotional email is a minor annoyance,
    while a real rejection/interview silently vanishing into the wrong
    bucket would actually cost the user visibility into their job search.
    """
    email_text = f"From: {sender}\nSubject: {subject}\n\n{snippet}"

    prompt = f"""You are classifying an email for a job search tracking app.
Read the email and decide which ONE category it belongs to:

- job_applied: confirms that the candidate's OWN application was submitted
  or received. Newsletters, job alerts, surveys and advice are never this.
  Also never this: a later update about an application already made, such
  as "we are still reviewing your application" or "thank you for your
  interest" - those are general. (A reminder that asks the candidate to
  complete a form, questionnaire or assessment is assessment_invite.)
- rejection: says the candidate was not selected, the position was
  filled or closed, or the hiring process ended without them
- interview_scheduled: invites the candidate to an interview
- assessment_invite: a specific employer or recruiter inviting or
  reminding the candidate to complete a coding test, questionnaire,
  behavioural or personality assessment, candidate form, additional
  questions or screening task AS PART OF evaluating them for a job they
  applied to
- offer_received: extends a job offer to the candidate
- recruiter_outreach: a recruiter or staffing firm contacting the
  candidate about a job opportunity the candidate did NOT apply for
- general: anything else (promotional, newsletters, job alerts, surveys,
  account or security notices, unrelated content) - this INCLUDES
  marketing from training platforms, certification providers, or
  learning services that use words like "test your skills,"
  "assessment," or "certification," even though that vocabulary
  overlaps with assessment_invite. The distinguishing question is: is
  this email part of an actual hiring process for a specific job the
  person applied to, or is it a vendor selling a course/certification/
  skill badge? If it's the latter, it's general, regardless of how
  similar the wording sounds.

Respond in valid JSON only, no markdown:
{{
  "category": "one of the seven categories above",
  "reason": "one short sentence explaining the decision"
}}

Email:
{email_text}"""

    raw = _clean_json(chat(prompt))

    try:
        data = json.loads(raw)
        category = data.get("category", "general") if isinstance(data, dict) else "general"
    except json.JSONDecodeError:
        category = "general"

    if category not in VALID_CATEGORIES:
        category = "general"

    return category
