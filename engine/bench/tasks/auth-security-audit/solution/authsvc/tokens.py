"""Session tokens: "<user id>.<issued (unix seconds)>.<signature>", signed over both parts
(SECURITY_REQUIREMENTS.md, section 1)."""

import hashlib
import hmac

SESSION_SECONDS = 8 * 3600


def sign(secret, payload):
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue(secret, uid, now):
    payload = f"{uid}.{int(now)}"
    return f"{payload}.{sign(secret, payload)}"


def verify(secret, token, now):
    """The user id a token belongs to, or None."""
    parts = str(token).split(".")
    if len(parts) != 3 or not all(parts):
        return None
    uid, issued, signature = parts
    if not hmac.compare_digest(signature, sign(secret, f"{uid}.{issued}")):
        return None
    try:
        issued_at = int(issued)
    except ValueError:
        return None
    if not 0 <= now - issued_at < SESSION_SECONDS:
        return None
    return uid
