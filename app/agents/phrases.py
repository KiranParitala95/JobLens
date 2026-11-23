"""Every hardcoded phrase and limit the email rules use, in ONE place.

Edit this file to add or remove wording - nothing else in the code needs
to change. Matching ignores capital letters, and curly quotes/dashes are
treated like plain ones. A phrase must match whole words (so "w2" will not
match inside another word).

An entry that starts with "re:" is a regular expression instead of plain
text (used only where the wording has a gap, like "process ... has ended").
"""

# --- Phrases that decide the category of a RECEIVED email -----------------

REJECTION = [
    "not moving forward",
    "will not be moving forward",
    "move forward with other candidates",
    "pursue other candidates",
    "pursue other applicants",
    "not selected",
    "did not select you",
    "not been selected",
    "no longer being considered",
    "will not be considered",
    "regret to inform",
    "position has been filled",
    "role has been filled",
    "filled the role",
    "position is closed",
    r"re:process\b[^.]{0,80}\bhas (now )?ended",
    "not proceeding with your application",
    "decided not to proceed",
    "not shortlisted",
    "won't be moving forward",
    "unable to offer you a position",
]

OFFER = [
    "pleased to offer you",
    "excited to offer you",
    "offer of employment",
    "job offer",
    "offer letter",
    "extend an offer",
]

INTERVIEW = [
    "schedule an interview",
    "interview invitation",
    "invite you to interview",
    "like to interview you",
    "phone screen",
    "screening call",
    "schedule a call with",
    "available for a call",
    "set up a call",
]

ASSESSMENT = [
    "online assessment",
    "technical assessment",
    "coding challenge",
    "coding test",
    "take-home",
    "hackerrank",
    "codility",
    "ai interview",
    "ai-led interview",
    "complete the questionnaire",
    "complete the online coding assessment",
    "behavioral assessment",
    "behavioural assessment",
    "personality assessment",
    "cognitive assessment",
    "situational judgment",
    "situational judgement",
    "pre-employment assessment",
    "pre-screening questionnaire",
    "screening questionnaire",
    "candidate questionnaire",
    "behavioral questionnaire",
    "behavioural questionnaire",
    "complete the candidate form",
    "candidate information sheet",
    "answer the additional questions",
    "complete the application form",
    r"re:complete \d+ quick steps",
]

APPLICATION = [
    "thank you for applying",
    "thanks for applying",
    "received your application",
    "application has been received",
    "application received",
    "successfully submitted",
    "application was submitted",
]

# Resume-service / staffing notices (not a real employer update): these go
# to General, but only after one of these phrases is actually found.
STAFFING_NOTICE = [
    "your resume is ready",
    "resume is ready",
    "resume has been approved",
    "resume approved",
    "requested resume",
]

RECRUITER_OUTREACH = [
    "came across your profile",
    "came across your resume",
    "found your resume",
    "our client is looking",
    "open position with our client",
    "send me your updated resume",
    "please send your updated resume",
    "if interested",
    "interested please",
    r"re:part of (the )?talent acquisition team",
    "i'm a recruiter",
    "w2",
    "c2c",
    "corp-to-corp",
]

# --- Phrases that decide whether a SENT email is a follow-up --------------

FOLLOW_UP_SENT = [
    "following up",
    "follow up",
    "follow-up",
    "checking in",
    "check in on",
    "any update",
    "status of my application",
    "circling back",
    "touching base",
    "touch base",
]

# Wording that only describes what MIGHT happen later ("if you are selected,
# we will schedule an interview"). When a mail contains any of these, it is
# a normal application mail: interview, screening and assessment phrases in
# it are ignored, so a promise of a future interview is not counted as one.
CONDITIONAL = [
    "if you are selected",
    "if you are shortlisted",
    "if you are chosen",
    "if you are approved for next steps",
    "if you are qualified for the next round",
    "if you are qualified to the next round",
    "if you are qualified to next round",
    "if you are qualified to move forward",
    "if your qualifications match",
    "if your skills and experience are a strong match",
    "if your profile is a match",
    "if your background is a match",
    "should you be selected",
    "should you be shortlisted",
    "if selected",
    "if shortlisted",
    "if you are among the candidates",
    "if we decide to move forward",
    r"re:if you (are|were) (selected|shortlisted|approved|chosen|qualified)",
    r"re:if your [^.]{0,40}\b(match|fit|aligns?)\b",
    r"re:if there('s| is| are)[^.]{0,30}\b(match|fit)\b",
    # Mails that only announce a later mail ("watch for an email with the
    # link"): the real invitation is judged when that later mail arrives.
    "be on the lookout for",
    "look out for",
    "keep an eye out for",
    "watch for an email",
    "you may receive an email",
    "you will receive an email",
    "you will be receiving an email",
    "you can expect an email",
    "you can expect to receive",
]

# Categories that a CONDITIONAL phrase cancels.
CANCELLED_BY_CONDITIONAL = ("interview_scheduled", "assessment_invite")

# Order matters: the first group that matches wins, so a rejection beats an
# offer, which beats an interview/assessment, and so on.
RECEIVED_PRIORITY = [
    ("rejection", REJECTION),
    ("offer_received", OFFER),
    ("interview_scheduled", INTERVIEW),
    ("assessment_invite", ASSESSMENT),
    ("job_applied", APPLICATION),
    ("general", STAFFING_NOTICE),
    ("recruiter_outreach", RECRUITER_OUTREACH),
]

# --- Limits on the text handed to the AI after a phrase matches -----------

# The matched sentence plus the one before and the one after it.
MAX_EVIDENCE_SENTENCES = 3
# Each of those sentences is cut to this many characters.
MAX_SENTENCE_CHARS = 300
