import base64
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
sys.path.insert(0, str(WORKSPACE))

PNG = base64.b64encode(b"\x89PNG fake image").decode()
T0 = 1_780_000_000


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        from authsvc.app import App
        from authsvc.users import UserStore

        self.dir = Path(tempfile.mkdtemp(prefix="auth-"))
        (self.dir / "users.json").write_text("{}", encoding="utf-8")
        store = UserStore(self.dir / "users.json")
        self.alice = store.create("Alice", "alice@example.com", "correct horse battery", role="admin")
        self.bob = store.create("bob", "bob@example.com", "bob's long password")
        self.clock = Clock()
        self.app = App(self.dir, "s3cret-key", clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, method, path, body=None, token=None, headers=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        return self.app.dispatch(method, path, h, body)

    def login(self, username, password, **extra):
        return self.call("POST", "/login", {"username": username, "password": password, **extra})

    def token(self, username="bob", password="bob's long password"):
        r = self.login(username, password)
        self.assertEqual(r.status, 200, r.body)
        return r.body["token"]

    def stored(self, uid):
        return json.loads((self.dir / "users.json").read_text(encoding="utf-8"))[uid]["password"]


class StillWorks(Base):
    def test_sign_in_and_me(self):
        me = self.call("GET", "/me", token=self.token())
        self.assertEqual((me.status, me.body["username"]), (200, "bob"))

    def test_relative_next_is_kept(self):
        r = self.login("bob", "bob's long password", next="/settings?tab=2")
        self.assertEqual(r.body.get("redirect"), "/settings?tab=2")

    def test_reset_flow(self):
        r = self.call("POST", "/password-reset/request", {"email": "bob@example.com"})
        self.assertEqual(r.status, 202)
        token = self.app.outbox[-1]["token"]
        r = self.call("POST", "/password-reset/confirm", {"token": token, "new_password": "a brand new secret"})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(self.login("bob", "a brand new secret").status, 200)

    def test_avatar_upload(self):
        tok = self.token()
        r = self.call("POST", "/me/avatar", {"filename": "me-2.PNG", "content": PNG}, token=tok)
        self.assertEqual(r.status, 201, r.body)
        self.assertTrue((self.dir / "avatars" / self.bob["id"] / "me-2.PNG").is_file())

    def test_admin_and_own_profile(self):
        admin = self.token("Alice", "correct horse battery")
        self.assertEqual(self.call("GET", "/admin/users", token=admin).status, 200)
        self.assertEqual(self.call("GET", f"/users/{self.bob['id']}/profile", token=admin).status, 200)
        self.assertEqual(self.call("GET", "/users/999/profile", token=admin).status, 404)
        bob = self.token()
        self.assertEqual(self.call("GET", f"/users/{self.bob['id']}/profile", token=bob).status, 200)


class Passwords(Base):
    def test_new_hashes(self):
        stored = self.stored(self.alice["id"])
        algo, iterations, salt, digest = stored.split("$")
        self.assertEqual(algo, "pbkdf2_sha256")
        self.assertGreaterEqual(int(iterations), 200_000)
        self.assertEqual(len(bytes.fromhex(salt)), 16)
        self.assertEqual(digest, hashlib.pbkdf2_hmac("sha256", b"correct horse battery",
                                                     bytes.fromhex(salt), int(iterations)).hex())

    def test_old_hashes_are_upgraded_at_sign_in(self):
        users = json.loads((self.dir / "users.json").read_text(encoding="utf-8"))
        salt = "a1b2c3d4e5f60718"
        users[self.bob["id"]]["password"] = "sha256$%s$%s" % (
            salt, hashlib.sha256((salt + "old but long password").encode()).hexdigest())
        (self.dir / "users.json").write_text(json.dumps(users), encoding="utf-8")
        from authsvc.app import App

        self.app = App(self.dir, "s3cret-key", clock=self.clock)
        self.assertEqual(self.login("bob", "old but long password").status, 200)
        self.assertTrue(self.stored(self.bob["id"]).startswith("pbkdf2_sha256$"))
        self.assertEqual(self.login("bob", "old but long password").status, 200)

    def test_set_password_uses_the_new_form(self):
        from authsvc.users import UserStore

        store = UserStore(self.dir / "users.json")
        store.set_password(self.bob["id"], "yet another password")
        self.assertTrue(self.stored(self.bob["id"]).startswith("pbkdf2_sha256$"))


class Sessions(Base):
    def test_tokens_expire_after_8_hours(self):
        tok = self.token()
        self.clock.now = T0 + 8 * 3600 - 1
        self.assertEqual(self.call("GET", "/me", token=tok).status, 200)
        self.clock.now = T0 + 8 * 3600
        self.assertEqual(self.call("GET", "/me", token=tok).status, 401)

    def test_no_part_of_a_token_can_change(self):
        tok = self.token()
        for i, ch in enumerate(tok):
            if ch.isdigit():
                forged = tok[:i] + str((int(ch) + 1) % 10) + tok[i + 1:]
                self.assertEqual(self.call("GET", "/me", token=forged).status, 401, f"position {i}")

    def test_unsigned_tokens(self):
        tok = self.token()
        head = tok.rsplit(".", 1)[0]
        for forged in (head + ".", head, "", ".", f"{self.alice['id']}..", "x.y.z"):
            self.assertEqual(self.call("GET", "/me", token=forged).status, 401, forged)


class SignIn(Base):
    def test_same_answer_for_unknown_user_and_wrong_password(self):
        a = self.login("nobody", "whatever password")
        b = self.login("bob", "wrong password!!")
        self.assertEqual((a.status, a.body), (401, {"error": "invalid username or password"}))
        self.assertEqual((b.status, b.body), (a.status, a.body))

    def test_usernames_ignore_case(self):
        self.assertEqual(self.login("alice", "correct horse battery").status, 200)
        self.assertEqual(self.login("BOB", "bob's long password").status, 200)

    def test_lockout_cannot_be_dodged_with_case(self):
        for _ in range(5):
            self.login("bob", "wrong password!!")
        self.assertEqual(self.login("BOB", "bob's long password").status, 429)
        self.assertEqual(self.login("Bob", "bob's long password").status, 429)
        self.clock.now = T0 + 15 * 60
        self.assertEqual(self.login("bob", "bob's long password").status, 200)

    def test_unknown_usernames_lock_too(self):
        for _ in range(5):
            self.login("ghost", "nope nope nope")
        self.assertEqual(self.login("ghost", "nope nope nope").status, 429)

    def test_open_redirects(self):
        for target in ("https://evil.example/", "//evil.example", "/\\evil.example",
                       "javascript:alert(1)", "http:/evil.example", " /settings", "/a b"):
            r = self.login("bob", "bob's long password", next=target)
            self.assertEqual(r.status, 200)
            self.assertNotIn("redirect", r.body, target)


class Reset(Base):
    def request(self, email):
        return self.call("POST", "/password-reset/request", {"email": email})

    def confirm(self, token, password="a brand new secret"):
        return self.call("POST", "/password-reset/confirm", {"token": token, "new_password": password})

    def test_same_answer_for_unknown_address(self):
        a, b = self.request("bob@example.com"), self.request("nobody@example.com")
        self.assertEqual((a.status, a.body), (202, {"status": "if that address has an account, we sent it an email"}))
        self.assertEqual((b.status, b.body), (a.status, a.body))
        self.assertEqual(len(self.app.outbox), 1)

    def test_tokens_are_unpredictable(self):
        random.seed(1)
        self.request("bob@example.com")
        random.seed(1)
        self.request("alice@example.com")
        first, second = self.app.outbox[-2]["token"], self.app.outbox[-1]["token"]
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 22)

    def test_single_use(self):
        self.request("bob@example.com")
        tok = self.app.outbox[-1]["token"]
        self.assertEqual(self.confirm(tok).status, 200)
        self.assertEqual(self.confirm(tok, "another new secret").status, 400)
        self.assertEqual(self.login("bob", "a brand new secret").status, 200)

    def test_expire_after_an_hour(self):
        self.request("bob@example.com")
        tok = self.app.outbox[-1]["token"]
        self.clock.now = T0 + 3600
        self.assertEqual(self.confirm(tok).status, 400)

    def test_a_newer_request_replaces_older_tokens(self):
        self.request("bob@example.com")
        old = self.app.outbox[-1]["token"]
        self.clock.now += 60
        self.request("bob@example.com")
        self.assertEqual(self.confirm(old).status, 400)
        self.assertEqual(self.confirm(self.app.outbox[-1]["token"]).status, 200)


class Access(Base):
    def test_role_cannot_come_from_the_request(self):
        bob = self.token()
        r = self.call("GET", "/admin/users", token=bob, headers={"X-Role": "admin"})
        self.assertEqual(r.status, 403)

    def test_others_profiles(self):
        bob = self.token()
        self.assertEqual(self.call("GET", f"/users/{self.alice['id']}/profile", token=bob).status, 403)
        self.assertEqual(self.call("GET", "/users/999/profile", token=bob).status, 403)


class Uploads(Base):
    def test_bad_names_are_refused(self):
        tok = self.token()
        before = sorted(str(p) for p in self.dir.rglob("*"))
        for name in ("../users.json", "../../escape.png", "..\\x.png", "a/b.png", "/tmp/x.png",
                     "x.php", ".png", "a..png", "évil.png", "x.png.exe", "", "a" * 70 + ".png"):
            r = self.call("POST", "/me/avatar", {"filename": name, "content": PNG}, token=tok)
            self.assertEqual(r.status, 400, name)
        after = sorted(str(p) for p in self.dir.rglob("*"))
        self.assertEqual(after, before)
        self.assertIn("alice", (self.dir / "users.json").read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
