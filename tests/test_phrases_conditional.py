"""Bugs C1, C2, C3: a mail that only promises a possible future interview
("if your experience aligns, we'll be in touch to schedule an interview")
must not be shown as Interview.

Run from the project root:  python tests/test_phrases_conditional.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import phrases, rules  # noqa: E402

# Mail texts typed from the real screenshots (wording is approximate).
CONDITIONAL_MAILS = [
    (
        "C1 Pivotal",
        "Your application was received",
        "Thank you for applying to Pivotal. If your experience aligns with "
        "what we are looking for, we'll be in touch to schedule an interview.",
    ),
    (
        "C2 University of Pittsburgh",
        "Application submitted",
        "Thank you for your interest in the University of Pittsburgh. "
        "If you are among the candidates we would like to pursue, we will "
        "contact you to schedule an interview.",
    ),
    (
        "C3 CreatorIQ",
        "Application received",
        "Thanks for applying to CreatorIQ. If there's a good match between "
        "your background and the role, we'll be in touch to schedule an "
        "interview.",
    ),
]

# Mails that must keep working as before.
REAL_MAILS = [
    (
        "real interview invite",
        "Next steps",
        "We would like to schedule an interview with you this week. "
        "Please pick a time that suits you.",
        "interview_scheduled",
    ),
    (
        "real assessment invite",
        "Your online assessment",
        "Please complete the online assessment by Friday to move ahead.",
        "assessment_invite",
    ),
]


def demo():
    for name, subject, body in CONDITIONAL_MAILS:
        result = rules.match_received(subject, body)
        category = result[0] if result else None
        assert category not in phrases.CANCELLED_BY_CONDITIONAL, (
            name + " was wrongly shown as " + str(category)
        )
        print("ok   " + name + " -> " + str(category))

    for name, subject, body, expected in REAL_MAILS:
        result = rules.match_received(subject, body)
        category = result[0] if result else None
        assert category == expected, (
            name + " should be " + expected + " but was " + str(category)
        )
        print("ok   " + name + " -> " + str(category))

    print("all checks passed")


if __name__ == "__main__":
    demo()
