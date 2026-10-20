"""Priority, duplicates and targets (SLA_POLICY.md, sections 3 to 5)."""

import re
from datetime import timedelta

PRIORITIES = ("urgent", "high", "normal", "low")
WORDS = [
    ("urgent", ["outage", "down", "breach", "cannot log in", "can't log in"]),
    ("high", ["error", "failed", "failing", "broken"]),
    ("low", ["question", "how do i", "feature request"]),
]
TARGETS = {  # hours: (first response, resolution)
    ("enterprise", "urgent"): (1, 8), ("enterprise", "high"): (2, 16),
    ("enterprise", "normal"): (4, 40), ("enterprise", "low"): (8, 80),
    ("business", "urgent"): (2, 16), ("business", "high"): (4, 24),
    ("business", "normal"): (8, 48), ("business", "low"): (16, 96),
    ("starter", "high"): (8, 40), ("starter", "normal"): (24, 80), ("starter", "low"): (48, 160),
}


def _has(text, phrase):
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE) is not None


def priority(ticket, plan):
    given = ticket.get("priority")
    if given in PRIORITIES:
        p = given
    else:
        subject = ticket.get("subject") or ""
        p = next((level for level, words in WORDS if any(_has(subject, w) for w in words)),
                 "normal")
    if plan == "starter" and p == "urgent":
        p = "high"
    return p


def key(subject):
    text = (subject or "").lower()
    while True:
        stripped = re.sub(r"^\s*(re|fwd|fw):\s*", "", text)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .!?")


def mark_duplicates(tickets):
    """tickets: dicts with "id", "customer", "subject", "created" (datetime). Returns the
    set of duplicate ids."""
    kept, dups = [], set()
    for t in sorted(tickets, key=lambda t: (t["created"], t["id"])):
        k = key(t["subject"])
        if any(o["customer"] == t["customer"] and key(o["subject"]) == k
               and t["created"] - o["created"] <= timedelta(hours=48) for o in kept):
            dups.add(t["id"])
        else:
            kept.append(t)
    return dups


def targets(plan, prio):
    first, resolution = TARGETS[(plan, prio)]
    return first * 3600, resolution * 3600  # seconds
