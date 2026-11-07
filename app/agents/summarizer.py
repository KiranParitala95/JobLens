import json
import re

from app.agents.classifier import chat


def _clean_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text.strip()


def generate_summary(stats, period="week"):
    """Agent 3: turn pre-counted stats into a short, readable recap.

    Takes numbers that have ALREADY been calculated via database queries -
    this function never counts anything itself and never touches the
    database. Its only job is deciding what's actually worth saying about
    numbers that are already known to be correct, e.g. noticing a response
    rate improved rather than just restating it.

    `stats` is expected to be a flat dict of counts for the requested
    period, e.g.:
      {"applications": 12, "rejections": 5, "interviews": 2,
       "assessments": 3, "response_rate": "42%"}

    Defaults to a plain, honest fallback sentence built directly from the
    numbers if the model call fails - the dashboard should never show
    nothing just because a summary call had an issue.
    """
    prompt = f"""You are writing a short job-search progress recap for a
tracking app. All the numbers below are already correct and final - do
not recalculate or second-guess them, only comment on what they show.

Period: this {period}
Applications sent: {stats.get("applications", 0)}
Rejections received: {stats.get("rejections", 0)}
Interviews scheduled: {stats.get("interviews", 0)}
Assessments/screenings: {stats.get("assessments", 0)}
Response rate: {stats.get("response_rate", "0%")}

Respond in valid JSON only, no markdown:
{{
  "headline": "one short sentence capturing the most notable thing about
    this period - a trend, a highlight, or an honest read if nothing
    stands out. Do not just restate every number.",
  "recap": "two to three sentences summarizing the period in plain,
    encouraging but honest language. Reference the actual numbers
    naturally, don't just list them."
}}"""

    try:
        raw = _clean_json(chat(prompt, temperature=0.3))
        data = json.loads(raw)
        return {
            "headline": data.get("headline", ""),
            "recap": data.get("recap", ""),
        }
    except Exception:
        # Plain, honest fallback built straight from the numbers - the
        # dashboard should never show a blank summary just because the
        # API call failed for any reason.
        fallback = (
            f"This {period}: {stats.get('applications', 0)} applications sent, "
            f"{stats.get('rejections', 0)} rejections, "
            f"{stats.get('interviews', 0)} interviews scheduled, "
            f"{stats.get('assessments', 0)} assessments pending. "
            f"Response rate: {stats.get('response_rate', '0%')}."
        )
        return {"headline": f"Your {period} in review", "recap": fallback}
