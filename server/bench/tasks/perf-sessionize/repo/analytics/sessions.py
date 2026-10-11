"""Group click events into user sessions.

An event is a dict: {"user": str, "ts": int (unix seconds), "page": str}.
The input list is not sorted.

Rules:
- Order the events by time. Events with the same time keep their input order.
- Two events of the same user are in the same session when the time between
  them is at most `gap_minutes` minutes. A larger gap starts a new session.
- Return the sessions sorted by start time, then by user.
"""


def _ordered(events):
    pool = list(events)
    ordered = []
    while pool:
        best = 0
        for i in range(1, len(pool)):
            if pool[i]["ts"] < pool[best]["ts"]:
                best = i
        ordered.append(pool.pop(best))
    return ordered


def sessionize(events, gap_minutes=30):
    gap = gap_minutes * 60
    sessions = []
    for event in _ordered(events):
        current = None
        for session in sessions:
            if session["user"] == event["user"] and event["ts"] - session["end"] <= gap:
                current = session
        if current is None:
            current = {"user": event["user"], "start": event["ts"], "end": event["ts"], "pages": []}
            sessions.append(current)
        current["end"] = event["ts"]
        current["pages"].append(event["page"])
    result = []
    for session in sorted(sessions, key=lambda s: (s["start"], s["user"])):
        result.append({
            "user": session["user"],
            "start": session["start"],
            "end": session["end"],
            "duration": session["end"] - session["start"],
            "count": len(session["pages"]),
            "pages": session["pages"],
        })
    return result


def bounce_rate(sessions):
    """Share of sessions with only one event, from 0.0 to 1.0."""
    if not sessions:
        return 0.0
    bounces = 0
    for session in sessions:
        if len([s for s in sessions if s is session and s["count"] == 1]) == 1:
            bounces += 1
    return bounces / len(sessions)


def top_pages(sessions, n=3):
    """The n pages that start the most sessions, with counts. Ties: page name A to Z."""
    starts = [s["pages"][0] for s in sessions]
    counted = []
    for page in starts:
        if page not in [p for p, _ in counted]:
            counted.append((page, starts.count(page)))
    counted.sort(key=lambda pc: (-pc[1], pc[0]))
    return counted[:n]
