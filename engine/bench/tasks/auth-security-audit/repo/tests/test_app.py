import shutil
import tempfile
import unittest
from pathlib import Path

from authsvc.app import App
from authsvc.users import UserStore


class AppTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        UserStore(self.dir / "users.json").create("bob", "bob@example.com", "bob's long password")
        self.app = App(self.dir, "secret", clock=lambda: 1_780_000_000)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_sign_in(self):
        r = self.app.dispatch("POST", "/login", {}, {"username": "bob", "password": "bob's long password"})
        self.assertEqual(r.status, 200)
        me = self.app.dispatch("GET", "/me", {"Authorization": f"Bearer {r.body['token']}"})
        self.assertEqual(me.body["username"], "bob")

    def test_wrong_password(self):
        r = self.app.dispatch("POST", "/login", {}, {"username": "bob", "password": "nope"})
        self.assertEqual(r.status, 401)


if __name__ == "__main__":
    unittest.main()
