"""Phrase matching: decides an email's category from its own wording,
without asking the AI. The full body is only read here, on the server -
it is never saved and never sent to the AI. Only a few short sentences
around the matched phrase are handed on (see phrases.py for the limits).
"""

import html
import re

from app.agents import phrases


def _normalize(text):
    """Lower-case, with curly quotes/dashes turned into plain ones."""
    text = html.unescape(text or "")
    for fancy, plain in (("’", "'"), ("‘", "'"), ("–", "-"), ("—", "-")):
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip().lower()


def _compile(entries):
    parts = []
    for entry in entries:
        if entry.startswith("re:"):
            parts.append(entry[3:])
        else:
            parts.append(r"\b" + re.escape(entry).replace(r"\ ", r"\s+") + r"\b")
    return re.compile("|".join(parts))


_RECEIVED_PATTERNS = [(cat, _compile(entries)) for cat, entries in phrases.RECEIVED_PRIORITY]
_FOLLOW_UP_PATTERN = _compile(phrases.FOLLOW_UP_SENT)
_CONDITIONAL_PATTERN = _compile(phrases.CONDITIONAL)

# Where a reply quotes the earlier thread - everything below is not the
# user's own new text, so it is ignored when looking for a follow-up.
_QUOTED_REPLY = re.compile(r"\n\s*on .{0,100}wrote:|\n-{2,}\s*original message", re.IGNORECASE)


def _sentences(text):
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in pieces if p and p.strip()]


def _units(subject, body):
    """Subject first, then each sentence of the body, duplicates removed
    (an email with both plain-text and HTML copies repeats itself)."""
    seen, units = set(), []
    for piece in [subject or ""] + _sentences(body):
        key = _normalize(piece)
        if key and key not in seen:
            seen.add(key)
            units.append(piece)
    return units


def _evidence(units, hit_index):
    """The matched sentence plus its neighbours, kept short. The neighbours
    are what usually carry the job title and company name."""
    start = max(0, hit_index - 1)
    chosen = units[start:start + phrases.MAX_EVIDENCE_SENTENCES]
    if hit_index == len(units) - 1 and hit_index >= 2:
        chosen = units[hit_index - 2:hit_index + 1]
    return [s[:phrases.MAX_SENTENCE_CHARS] for s in chosen]


def match_received(subject, body):
    """Returns (category, evidence_sentences) for a received email, or None
    when no phrase matched (the caller then asks the AI as a backup)."""
    units = _units(subject, body)
    normalized = [_normalize(u) for u in units]

    # A promise of a possible future step ("if you are selected, we will
    # schedule an interview") makes this a normal application mail.
    conditional = any(_CONDITIONAL_PATTERN.search(t) for t in normalized)

    for category, pattern in _RECEIVED_PATTERNS:
        if conditional and category in phrases.CANCELLED_BY_CONDITIONAL:
            continue
        for index, text in enumerate(normalized):
            if pattern.search(text):
                return category, _evidence(units, index)
    return None


def match_sent(subject, body):
    """Returns ("follow_up_sent", evidence) for a sent email that reads like
    a follow-up, or None - the caller then skips the email entirely."""
    own_text = _QUOTED_REPLY.split("\n" + (body or ""), maxsplit=1)[0]
    units = _units(subject, own_text)
    for index, unit in enumerate(units):
        if _FOLLOW_UP_PATTERN.search(_normalize(unit)):
            return "follow_up_sent", _evidence(units, index)
    return None
