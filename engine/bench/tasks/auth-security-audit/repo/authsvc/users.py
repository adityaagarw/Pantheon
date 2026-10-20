"""Users, kept in a JSON file: {"<id>": {"id", "username", "email", "password", "role"}}."""

import hashlib
import json
import os
from pathlib import Path


def hash_password(password, salt=None):
    salt = salt or os.urandom(8).hex()
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${digest}"


def check_password(password, stored):
    _, salt, _ = stored.split("$")
    return hash_password(password, salt) == stored


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
        return next((u for u in self.users.values() if u["username"] == username), None)

    def by_email(self, email):
        return next((u for u in self.users.values() if u["email"] == email), None)

    def get(self, uid):
        return self.users.get(str(uid))

    def set_password(self, uid, password):
        self.users[str(uid)]["password"] = hash_password(password)
        self.save()
