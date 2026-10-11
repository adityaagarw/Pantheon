import sqlite3
import tempfile
import unittest
from pathlib import Path

from notes.service import NotesService


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = NotesService(self.root / "notes.db", self.root / "exports")
        self.alice = self.service.create_user("alice", "pw-alice")
        self.bob = self.service.create_user("bob", "pw-bob")
        self.secret = self.service.add_note(self.alice, "Secret", "alice only")

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_sql_injection_in_search(self):
        self.assertEqual(self.service.search_notes(self.bob, "x%' OR 1=1 --"), [])

    def test_path_traversal_in_export(self):
        with self.assertRaises(ValueError):
            self.service.export_note(self.alice, self.secret, "../evil.txt")
        self.assertFalse((self.root / "evil.txt").exists())

    def test_password_is_salted_hash(self):
        db = sqlite3.connect(str(self.root / "notes.db"))
        stored = db.execute("SELECT password FROM users WHERE username = 'alice'").fetchone()[0]
        db.close()
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))

    def test_other_users_note_is_hidden(self):
        self.assertIsNone(self.service.get_note(self.bob, self.secret))
        with self.assertRaises(LookupError):
            self.service.export_note(self.bob, self.secret, "x.txt")


if __name__ == "__main__":
    unittest.main()
