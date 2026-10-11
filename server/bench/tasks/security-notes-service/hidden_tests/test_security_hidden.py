import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from notes.service import NotesService


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "data" / "notes.db"
        self.db_path.parent.mkdir()
        self.exports = self.root / "data" / "exports"
        self.service = NotesService(self.db_path, self.exports)
        self.alice = self.service.create_user("alice", "Tr0ub4dor&3")
        self.bob = self.service.create_user("bob", "hunter2")
        self.a1 = self.service.add_note(self.alice, "Bank", "PIN is 4321")
        self.a2 = self.service.add_note(self.alice, "Bob's party", "Bring cake, 100% chocolate")
        self.b1 = self.service.add_note(self.bob, "Groceries", "Milk")

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def user_rows(self):
        db = sqlite3.connect(str(self.db_path))
        db.row_factory = sqlite3.Row
        rows = {r["username"]: dict(r) for r in db.execute("SELECT * FROM users")}
        db.close()
        return rows


class InjectionTest(Base):
    def test_injection_does_not_leak_notes(self):
        for payload in ("x%' OR 1=1 --", "%' OR '1'='1", "' UNION SELECT id, user_id, title, body FROM notes --"):
            try:
                found = self.service.search_notes(self.bob, payload)
            except sqlite3.Error:
                self.fail(f"query {payload!r} raised a database error")
            self.assertTrue(all(n["user_id"] == self.bob for n in found), payload)

    def test_quote_in_query_works(self):
        found = self.service.search_notes(self.alice, "Bob's")
        self.assertEqual([n["id"] for n in found], [self.a2])

    def test_percent_is_literal(self):
        self.assertEqual([n["id"] for n in self.service.search_notes(self.alice, "100%")], [self.a2])
        self.assertEqual([n["id"] for n in self.service.search_notes(self.alice, "%")], [self.a2])

    def test_search_still_case_insensitive(self):
        self.assertEqual([n["id"] for n in self.service.search_notes(self.alice, "pin")], [self.a1])
        self.assertEqual([n["id"] for n in self.service.search_notes(self.bob, "MILK")], [self.b1])


class ExportTest(Base):
    def test_parent_folder_blocked(self):
        for name in ("../evil.txt", "../../evil.txt", "sub/../../evil.txt", "..\\evil.txt"):
            with self.assertRaises(ValueError, msg=name):
                self.service.export_note(self.alice, self.a1, name)
        self.assertFalse((self.root / "data" / "evil.txt").exists())
        self.assertFalse((self.root / "evil.txt").exists())

    def test_absolute_path_blocked(self):
        target = self.root / "abs.txt"
        with self.assertRaises(ValueError):
            self.service.export_note(self.alice, self.a1, str(target))
        self.assertFalse(target.exists())

    def test_subfolder_blocked(self):
        with self.assertRaises(ValueError):
            self.service.export_note(self.alice, self.a1, "a/b.txt")

    def test_plain_name_works(self):
        path = Path(self.service.export_note(self.alice, self.a1, "bank.txt"))
        self.assertEqual(path.resolve().parent, self.exports.resolve())
        self.assertEqual(path.read_text(encoding="utf-8"), "Bank\n\nPIN is 4321\n")


class PasswordTest(Base):
    def test_not_plain_or_fast_hash(self):
        row = self.user_rows()["alice"]
        weak = {
            "Tr0ub4dor&3",
            hashlib.md5(b"Tr0ub4dor&3").hexdigest(),
            hashlib.sha1(b"Tr0ub4dor&3").hexdigest(),
            hashlib.sha256(b"Tr0ub4dor&3").hexdigest(),
        }
        for key, value in row.items():
            if key in ("id", "username"):
                continue
            text = value.hex() if isinstance(value, bytes) else str(value)
            for bad in weak:
                self.assertNotIn(bad, text, f"column {key} has a weak password value")

    def test_salted(self):
        self.service.create_user("carol", "same-password")
        self.service.create_user("dave", "same-password")
        rows = self.user_rows()
        carol = {k: v for k, v in rows["carol"].items() if k not in ("id", "username")}
        dave = {k: v for k, v in rows["dave"].items() if k not in ("id", "username")}
        self.assertNotEqual(carol, dave)

    def test_login_still_works(self):
        self.assertEqual(self.service.login("alice", "Tr0ub4dor&3"), self.alice)
        self.assertEqual(self.service.login("bob", "hunter2"), self.bob)
        self.assertIsNone(self.service.login("alice", "hunter2"))
        self.assertIsNone(self.service.login("alice", ""))
        self.assertIsNone(self.service.login("zed", "hunter2"))

    def test_login_survives_restart(self):
        self.service.close()
        self.service = NotesService(self.db_path, self.exports)
        self.assertEqual(self.service.login("alice", "Tr0ub4dor&3"), self.alice)


class AccessControlTest(Base):
    def test_get_other_users_note(self):
        self.assertIsNone(self.service.get_note(self.bob, self.a1))
        self.assertEqual(self.service.get_note(self.alice, self.a1)["body"], "PIN is 4321")

    def test_export_other_users_note(self):
        with self.assertRaises(LookupError):
            self.service.export_note(self.bob, self.a1, "stolen.txt")
        self.assertFalse((self.exports / "stolen.txt").exists())

    def test_change_or_delete_other_users_note(self):
        self.assertFalse(self.service.update_note(self.bob, self.a1, "hacked"))
        self.assertFalse(self.service.delete_note(self.bob, self.a1))
        self.assertEqual(self.service.get_note(self.alice, self.a1)["body"], "PIN is 4321")

    def test_list_only_own_notes(self):
        self.assertEqual([n["id"] for n in self.service.list_notes(self.bob)], [self.b1])
