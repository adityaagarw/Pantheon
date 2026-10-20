"""Users, kept in a JSON file: {"<id>": {"id", "username", "email", "password", "role"}}.

Passwords: PBKDF2-HMAC-SHA256 (SECURITY_REQUIREMENTS.md, section 2). Old "sha256$salt$hex"
hashes still verify, and are upgraded on the next successful sign-in (App.login).
"""

import hashlib
import hmac
import json
import os
from pathlib import Path

ITERATIONS = 200_000


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def check_password(password, stored):
    parts = stored.split("$")
    if parts[0] == "pbkdf2_sha256" and len(parts) == 4:
        expected = hash_password(password, parts[2], int(parts[1]))
    elif parts[0] == "sha256" and len(parts) == 3:  # the old format
        expected = "sha256$%s$%s" % (parts[1], hashlib.sha256((parts[1] + password).encode()).hexdigest())
    else:
        return False
    return hmac.compare_digest(expected, stored)


def needs_upgrade(stored):
    parts = stored.split("$")
    return parts[0] != "pbkdf2_sha256" or int(parts[1]) < ITERATIONS


class UserStore:
    def __init__(self, path):
        self.path = Path(path)
        self.users = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self):
        self.path.write_text(json.dumps(self.users, indent=2, sort_keys=True), encoding="utf-8")

    def create(self, username, email, password, role="user"):
        uid = str(max((int(k) for k in self.users), default=0) + 1)
        self.users[uid] = {"id": uid, "username": username, "email": email,
                           "password": hash_password(password), "role": role}
        self.save()
        return self.users[uid]

    def by_username(self, username):
        wanted = username.casefold()
        return next((u for u in self.users.values() if u["username"].casefold() == wanted), None)

    def by_email(self, email):
        wanted = email.casefold()
        return next((u for u in self.users.values() if u["email"].casefold() == wanted), None)

    def get(self, uid):
        return self.users.get(str(uid))

    def set_password(self, uid, password):
        self.users[str(uid)]["password"] = hash_password(password)
        self.save()
