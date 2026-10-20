import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from authsvc.app import App
from authsvc.users import UserStore

PNG = base64.b64encode(b"img").decode()


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        store = UserStore(self.dir / "users.json")
        self.alice = store.create("Alice", "alice@example.com", "correct horse battery", role="admin")
        self.bob = store.create("bob", "bob@example.com", "bob's long password")
        self.now = 1_780_000_000
        self.app = App(self.dir, "secret", clock=lambda: self.now)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def login(self, user, password, **extra):
        return self.app.dispatch("POST", "/login", {}, {"username": user, "password": password, **extra})

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_expiry_and_tampering(self):
        token = self.login("bob", "bob's long password").body["token"]
        uid, issued, sig = token.split(".")
        forged = f"{uid}.{int(issued) + 3600}.{sig}"
        self.assertEqual(self.app.dispatch("GET", "/me", self.auth(forged)).status, 401)
        self.assertEqual(self.app.dispatch("GET", "/me", self.auth(f"{uid}.{issued}.")).status, 401)
        self.now += 8 * 3600
        self.assertEqual(self.app.dispatch("GET", "/me", self.auth(token)).status, 401)

    def test_enumeration_and_lockout(self):
        self.assertEqual(self.login("ghost", "x").body, self.login("bob", "x").body)
        for _ in range(5):
            self.login("bob", "x")
        self.assertEqual(self.login("BOB", "bob's long password").status, 429)

    def test_redirects(self):
        self.assertNotIn("redirect", self.login("bob", "bob's long password", next="//evil.example").body)
        self.assertEqual(self.login("bob", "bob's long password", next="/home").body["redirect"], "/home")

    def test_reset_tokens(self):
        self.app.dispatch("POST", "/password-reset/request", {}, {"email": "bob@example.com"})
        token = self.app.outbox[-1]["token"]
        body = {"token": token, "new_password": "a brand new secret"}
        self.assertEqual(self.app.dispatch("POST", "/password-reset/confirm", {}, body).status, 200)
        self.assertEqual(self.app.dispatch("POST", "/password-reset/confirm", {}, body).status, 400)

    def test_access(self):
        token = self.login("bob", "bob's long password").body["token"]
        headers = {**self.auth(token), "X-Role": "admin"}
        self.assertEqual(self.app.dispatch("GET", "/admin/users", headers).status, 403)
        self.assertEqual(self.app.dispatch("GET", f"/users/{self.alice['id']}/profile", headers).status, 403)

    def test_avatar_names(self):
        token = self.login("bob", "bob's long password").body["token"]
        r = self.app.dispatch("POST", "/me/avatar", self.auth(token), {"filename": "../users.json", "content": PNG})
        self.assertEqual(r.status, 400)
        r = self.app.dispatch("POST", "/me/avatar", self.auth(token), {"filename": "me.png", "content": PNG})
        self.assertEqual(r.status, 201)


if __name__ == "__main__":
    unittest.main()
