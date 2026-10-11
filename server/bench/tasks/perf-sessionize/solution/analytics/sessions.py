"""Group click events into user sessions.

An event is a dict: {"user": str, "ts": int (unix seconds), "page": str}.
The input list is not sorted.

Rules:
- Order the events by time. Events with the same time keep their input order.
- Two events of the same user are in the same session when the time between
  them is at most `gap_minutes` minutes. A larger gap starts a new session.
- Return the sessions sorted by start time, then by user.
"""

from collections import Counter


def sessionize(events, gap_minutes=30):
    gap = gap_minutes * 60
    open_sessions = {}
    sessions = []
    for event in sorted(events, key=lambda e: e["ts"]):  # sorted() is stable
        user = event["user"]
        current = open_sessions.get(user)
        if current is None or event["ts"] - current["end"] > gap:
            current = {"user": user, "start": event["ts"], "end": event["ts"], "pages": []}
            open_sessions[user] = current
            sessions.append(current)
        current["end"] = event["ts"]
        current["pages"].append(event["page"])
    sessions.sort(key=lambda s: (s["start"], s["user"]))
    return [{
        "user": s["user"],
        "start": s["start"],
        "end": s["end"],
        "duration": s["end"] - s["start"],
        "count": len(s["pages"]),
        "pages": s["pages"],
    } for s in sessions]


def bounce_rate(sessions):
    """Share of sessions with only one event, from 0.0 to 1.0."""
    if not sessions:
        return 0.0
    return sum(1 for s in sessions if s["count"] == 1) / len(sessions)


def top_pages(sessions, n=3):
    """The n pages that start the most sessions, with counts. Ties: page name A to Z."""
    counts = Counter(s["pages"][0] for s in sessions)
    return sorted(counts.items(), key=lambda pc: (-pc[1], pc[0]))[:n]
