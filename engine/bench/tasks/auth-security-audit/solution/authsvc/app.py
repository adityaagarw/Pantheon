"""The account service: one request in, one response out (App.dispatch)."""

import base64
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from authsvc import tokens
from authsvc.users import UserStore, check_password, needs_upgrade

MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
RESET_SECONDS = 3600
BAD_LOGIN = {"error": "invalid username or password"}
RESET_SENT = {"status": "if that address has an account, we sent it an email"}
AVATAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMAGE = (".png", ".jpg", ".jpeg", ".gif")


@dataclass
class Response:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


def safe_next(target):
    target = str(target or "")
    return (target.startswith("/") and not target.startswith("//") and "\\" not in target
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in target))


class App:
    """data_dir holds users.json and avatars/. clock() gives unix seconds. Emails the
    service sends are appended to self.outbox ({"to", "subject", "token"})."""

    def __init__(self, data_dir, secret, clock=time.time):
        self.dir = Path(data_dir)
        self.users = UserStore(self.dir / "users.json")
        self.secret = secret
        self.clock = clock
        self.outbox = []
        self.failures = {}  # casefolded username -> [failure times]
        self.resets = {}  # token -> {"uid", "created"}

    # --- helpers ---------------------------------------------------------------------------

    def current_user(self, headers):
        auth = next((v for k, v in (headers or {}).items() if k.lower() == "authorization"), "")
        if not auth.startswith("Bearer "):
            return None
        uid = tokens.verify(self.secret, auth[len("Bearer "):], self.clock())
        return self.users.get(uid) if uid else None

    # --- routes ----------------------------------------------------------------------------

    def dispatch(self, method, path, headers=None, body=None):
        headers, body = headers or {}, body or {}
        route = (method, path.rstrip("/") or "/")
        if route == ("POST", "/login"):
            return self.login(body)
        if route == ("POST", "/password-reset/request"):
            return self.reset_request(body)
        if route == ("POST", "/password-reset/confirm"):
            return self.reset_confirm(body)
        user = self.current_user(headers)
        if user is None:
            return Response(401, {"error": "not signed in"})
        admin = user["role"] == "admin"
        if route == ("GET", "/me"):
            return Response(200, self.profile(user))
        if route == ("POST", "/me/avatar"):
            return self.avatar(user, body)
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 3 and parts[0] == "users" and parts[2] == "profile":
            if parts[1] != user["id"] and not admin:
                return Response(403, {"error": "not allowed"})
            other = self.users.get(parts[1])
            if other is None:
                return Response(404, {"error": "no such user"})
            return Response(200, self.profile(other))
        if route == ("GET", "/admin/users"):
            if not admin:
                return Response(403, {"error": "admins only"})
            return Response(200, {"users": [self.profile(u) for u in self.users.users.values()]})
        return Response(404, {"error": "not found"})

    def profile(self, user):
        return {"id": user["id"], "username": user["username"], "email": user["email"],
                "role": user["role"]}

    def login(self, body):
        username, password = str(body.get("username", "")), str(body.get("password", ""))
        key, now = username.casefold(), self.clock()
        recent = [t for t in self.failures.get(key, []) if now - t < LOCK_SECONDS]
        self.failures[key] = recent
        if len(recent) >= MAX_FAILURES:
            return Response(429, {"error": "too many attempts, try again later"})
        user = self.users.by_username(username)
        if user is None or not check_password(password, user["password"]):
            self.failures[key] = recent + [now]
            return Response(401, dict(BAD_LOGIN))
        self.failures.pop(key, None)
        if needs_upgrade(user["password"]):
            self.users.set_password(user["id"], password)
        result = {"token": tokens.issue(self.secret, user["id"], now)}
        if body.get("next") and safe_next(body["next"]):
            result["redirect"] = body["next"]
        return Response(200, result)

    def reset_request(self, body):
        user = self.users.by_email(str(body.get("email", "")))
        if user is not None:
            for old in [t for t, e in self.resets.items() if e["uid"] == user["id"]]:
                del self.resets[old]
            token = secrets.token_urlsafe(32)
            self.resets[token] = {"uid": user["id"], "created": self.clock()}
            self.outbox.append({"to": user["email"], "subject": "Reset your password", "token": token})
        return Response(202, dict(RESET_SENT))

    def reset_confirm(self, body):
        token = str(body.get("token", ""))
        entry = self.resets.get(token)
        if entry is None or self.clock() - entry["created"] >= RESET_SECONDS:
            self.resets.pop(token, None)
            return Response(400, {"error": "invalid or expired token"})
        password = str(body.get("new_password", ""))
        if len(password) < 10:
            return Response(400, {"error": "passwords need at least 10 characters"})
        del self.resets[token]
        self.users.set_password(entry["uid"], password)
        return Response(200, {"status": "password changed"})

    def avatar(self, user, body):
        name = str(body.get("filename", ""))
        if not AVATAR.match(name) or ".." in name or not name.lower().endswith(IMAGE):
            return Response(400, {"error": "that file name isn't allowed"})
        try:
            content = base64.b64decode(str(body.get("content", "")), validate=True)
        except ValueError:
            return Response(400, {"error": "content must be base64"})
        folder = (self.dir / "avatars" / user["id"]).resolve()
        target = (folder / name).resolve()
        if target.parent != folder:
            return Response(400, {"error": "that file name isn't allowed"})
        folder.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return Response(201, {"path": f"avatars/{user['id']}/{name}"})
