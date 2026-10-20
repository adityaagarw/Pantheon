"""Session tokens: "<user id>.<expires (unix seconds)>.<signature>"."""

import hashlib
import hmac

SESSION_SECONDS = 8 * 3600


def sign(secret, uid):
    return hmac.new(secret.encode(), uid.encode(), hashlib.sha256).hexdigest()


def issue(secret, uid, now):
    expires = int(now) + SESSION_SECONDS
    return f"{uid}.{expires}.{sign(secret, uid)}"


def verify(secret, token, now):
    """The user id a token belongs to, or None."""
    try:
        uid, expires, signature = token.split(".")
    except ValueError:
        return None
    if signature and signature != sign(secret, uid):
        return None
    return uid
