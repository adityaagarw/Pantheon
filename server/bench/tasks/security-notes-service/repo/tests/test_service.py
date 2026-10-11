import tempfile
import unittest
from pathlib import Path

from notes.service import NotesService


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.service = NotesService(root / "notes.db", root / "exports")
        self.alice = self.service.create_user("alice", "pw-alice")

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_login(self):
        self.assertEqual(self.service.login("alice", "pw-alice"), self.alice)
        self.assertIsNone(self.service.login("alice", "wrong"))
        self.assertIsNone(self.service.login("nobody", "pw-alice"))

    def test_add_and_get(self):
        note_id = self.service.add_note(self.alice, "Title", "Body")
        self.assertEqual(self.service.get_note(self.alice, note_id)["body"], "Body")

    def test_search(self):
        self.service.add_note(self.alice, "Shopping", "Milk and bread")
        self.service.add_note(self.alice, "Work", "Call Bob")
        self.assertEqual([n["title"] for n in self.service.search_notes(self.alice, "milk")], ["Shopping"])

    def test_export(self):
        note_id = self.service.add_note(self.alice, "Title", "Body")
        path = self.service.export_note(self.alice, note_id, "note.txt")
        self.assertEqual(path.read_text(encoding="utf-8"), "Title\n\nBody\n")


if __name__ == "__main__":
    unittest.main()
