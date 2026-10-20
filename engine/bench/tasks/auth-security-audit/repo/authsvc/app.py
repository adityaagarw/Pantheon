"""The account service: one request in, one response out (App.dispatch)."""

import base64
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from authsvc import tokens
from authsvc.users import UserStore, check_password

MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
RESET_SECONDS = 3600


@dataclass
class Response:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


class App:
    """data_dir holds users.json and avatars/. clock() gives unix seconds. Emails the
    service sends are appended to self.outbox ({"to", "subject", "token"})."""

    def __init__(self, data_dir, secret, clock=time.time):
        self.dir = Path(data_dir)
        self.users = UserStore(self.dir / "users.json")
        self.secret = secret
        self.clock = clock
        self.outbox = []
        self.failures = {}  # username -> [failure times]
        self.resets = {}  # token -> {"uid", "created"}

    # --- helpers ---------------------------------------------------------------------------

    def current_user(self, headers):
        auth = (headers or {}).get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        uid = tokens.verify(self.secret, auth[len("Bearer "):], self.clock())
        return self.users.get(uid) if uid else None

    def is_admin(self, user, headers):
        return user["role"] == "admin" or (headers or {}).get("X-Role") == "admin"

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
        if route == ("GET", "/me"):
            return Response(200, self.profile(user))
        if route == ("POST", "/me/avatar"):
            return self.avatar(user, body)
        if method == "GET" and path.startswith("/users/") and path.endswith("/profile"):
            other = self.users.get(path.split("/")[2])
            if other is None:
                return Response(404, {"error": "no such user"})
            return Response(200, self.profile(other))
        if route == ("GET", "/admin/users"):
            if not self.is_admin(user, headers):
                return Response(403, {"error": "admins only"})
            return Response(200, {"users": [self.profile(u) for u in self.users.users.values()]})
        return Response(404, {"error": "not found"})

    def profile(self, user):
        return {"id": user["id"], "username": user["username"], "email": user["email"],
                "role": user["role"]}

    def login(self, body):
        username, password = str(body.get("username", "")), str(body.get("password", ""))
        now = self.clock()
        recent = [t for t in self.failures.get(username, []) if now - t < LOCK_SECONDS]
        if len(recent) >= MAX_FAILURES:
            return Response(429, {"error": "too many attempts, try again later"})
        user = self.users.by_username(username)
        if user is None:
            return Response(401, {"error": "no such user"})
        if not check_password(password, user["password"]):
            self.failures[username] = recent + [now]
            return Response(401, {"error": "wrong password"})
        self.failures.pop(username, None)
        result = {"token": tokens.issue(self.secret, user["id"], now)}
        if body.get("next"):
            result["redirect"] = body["next"]
        return Response(200, result)

    def reset_request(self, body):
        user = self.users.by_email(str(body.get("email", "")))
        if user is None:
            return Response(404, {"error": "no account with that email"})
        random.seed(int(self.clock()))
        token = "%016x" % random.getrandbits(64)
        self.resets[token] = {"uid": user["id"], "created": self.clock()}
        self.outbox.append({"to": user["email"], "subject": "Reset your password", "token": token})
        return Response(202, {"status": "email sent"})

    def reset_confirm(self, body):
        entry = self.resets.get(str(body.get("token", "")))
        if entry is None:
            return Response(400, {"error": "invalid token"})
        password = str(body.get("new_password", ""))
        if len(password) < 10:
            return Response(400, {"error": "passwords need at least 10 characters"})
        self.users.set_password(entry["uid"], password)
        return Response(200, {"status": "password changed"})

    def avatar(self, user, body):
        name = str(body.get("filename", ""))
        if not name:
            return Response(400, {"error": "filename is required"})
        try:
            content = base64.b64decode(str(body.get("content", "")), validate=True)
        except ValueError:
            return Response(400, {"error": "content must be base64"})
        folder = self.dir / "avatars" / user["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
        return Response(201, {"path": f"avatars/{user['id']}/{name}"})
