import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from todo import cli

TODAY = "2026-10-10"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "todo.json"
        self.env = mock.patch.dict(os.environ, {"TODO_FILE": str(self.path), "TODO_TODAY": TODAY})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code or 0, out.getvalue(), err.getvalue()

    def lines(self, *args):
        code, out, err = self.run_cli("list", *args)
        self.assertEqual(code, 0, err)
        return [line for line in out.splitlines() if line.strip()]

    def data(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class AddDueTest(Base):
    def test_add_with_due_is_stored(self):
        self.assertEqual(self.run_cli("add", "Pay rent", "--due", "2026-11-01")[0], 0)
        self.assertEqual(self.data()["tasks"][0]["due"], "2026-11-01")

    def test_add_without_due_stores_null(self):
        self.run_cli("add", "Buy milk")
        task = self.data()["tasks"][0]
        self.assertIn("due", task)
        self.assertIsNone(task["due"])

    def test_invalid_month(self):
        code, _, err = self.run_cli("add", "X", "--due", "2026-13-01")
        self.assertEqual(code, 2)
        self.assertIn("error: invalid date: 2026-13-01 (use YYYY-MM-DD)", err)
        self.assertFalse(self.path.exists() and self.data()["tasks"])

    def test_invalid_words_and_other_formats(self):
        for value in ("tomorrow", "01/11/2026", "2026-02-30"):
            code, _, err = self.run_cli("add", "X", "--due", value)
            self.assertEqual(code, 2, value)
            self.assertIn(f"invalid date: {value}", err)
        self.assertFalse(self.path.exists() and self.data()["tasks"])


class ListTest(Base):
    def test_due_suffix(self):
        self.run_cli("add", "Pay rent", "--due", "2026-11-01")
        self.assertEqual(self.lines(), ["[ ] 1 Pay rent (due 2026-11-01)"])

    def test_overdue_suffix(self):
        self.run_cli("add", "Taxes", "--due", "2026-10-09")
        self.assertEqual(self.lines(), ["[ ] 1 Taxes (due 2026-10-09, OVERDUE)"])

    def test_due_today_is_not_overdue(self):
        self.run_cli("add", "Today", "--due", TODAY)
        self.assertEqual(self.lines(), [f"[ ] 1 Today (due {TODAY})"])

    def test_done_task_is_not_overdue(self):
        self.run_cli("add", "Old", "--due", "2026-01-01")
        self.run_cli("done", "1")
        self.assertEqual(self.lines(), ["[x] 1 Old (due 2026-01-01)"])

    def test_no_due_unchanged(self):
        self.run_cli("add", "Plain")
        self.assertEqual(self.lines(), ["[ ] 1 Plain"])

    def test_overdue_filter(self):
        self.run_cli("add", "A", "--due", "2026-10-01")
        self.run_cli("add", "B", "--due", "2026-10-30")
        self.run_cli("add", "C")
        self.run_cli("add", "D", "--due", "2026-09-01")
        self.run_cli("done", "4")
        self.assertEqual(self.lines("--overdue"), ["[ ] 1 A (due 2026-10-01, OVERDUE)"])

    def test_sort_due(self):
        self.run_cli("add", "none1")
        self.run_cli("add", "late", "--due", "2026-12-01")
        self.run_cli("add", "early", "--due", "2026-10-15")
        self.run_cli("add", "none2")
        self.run_cli("add", "early-too", "--due", "2026-10-15")
        ids = [line.split()[2] for line in self.lines("--sort", "due")]
        self.assertEqual(ids, ["3", "5", "2", "1", "4"])

    def test_sort_id_is_default(self):
        self.run_cli("add", "A", "--due", "2026-12-01")
        self.run_cli("add", "B", "--due", "2026-10-15")
        self.assertEqual([line.split()[2] for line in self.lines()], ["1", "2"])
        self.assertEqual([line.split()[2] for line in self.lines("--sort", "id")], ["1", "2"])

    def test_options_together(self):
        self.run_cli("add", "A", "--due", "2026-10-05")
        self.run_cli("add", "B", "--due", "2026-10-01")
        self.run_cli("add", "C", "--due", "2026-11-01")
        ids = [line.split()[2] for line in self.lines("--pending", "--overdue", "--sort", "due")]
        self.assertEqual(ids, ["2", "1"])


class DueCommandTest(Base):
    def test_set_and_change(self):
        self.run_cli("add", "A")
        self.assertEqual(self.run_cli("due", "1", "2026-12-24")[0], 0)
        self.assertEqual(self.data()["tasks"][0]["due"], "2026-12-24")
        self.assertEqual(self.run_cli("due", "1", "2026-12-31")[0], 0)
        self.assertEqual(self.data()["tasks"][0]["due"], "2026-12-31")

    def test_clear(self):
        self.run_cli("add", "A", "--due", "2026-12-24")
        self.assertEqual(self.run_cli("due", "1", "--clear")[0], 0)
        self.assertIsNone(self.data()["tasks"][0]["due"])
        self.assertEqual(self.lines(), ["[ ] 1 A"])

    def test_unknown_id(self):
        self.run_cli("add", "A")
        code, _, err = self.run_cli("due", "7", "2026-12-24")
        self.assertEqual(code, 1)
        self.assertIn("error: no task with id 7", err)

    def test_bad_date(self):
        self.run_cli("add", "A")
        code, _, err = self.run_cli("due", "1", "2026-1-5")
        self.assertEqual(code, 2)
        self.assertIn("invalid date", err)
        self.assertIsNone(self.data()["tasks"][0]["due"])


class CompatibilityTest(Base):
    def test_old_data_file(self):
        self.path.write_text(json.dumps({"next_id": 3, "tasks": [
            {"id": 1, "text": "Old one", "done": False},
            {"id": 2, "text": "Old two", "done": True},
        ]}), encoding="utf-8")
        self.assertEqual(self.lines(), ["[ ] 1 Old one", "[x] 2 Old two"])
        self.assertEqual(self.lines("--sort", "due"), ["[ ] 1 Old one", "[x] 2 Old two"])
        self.assertEqual(self.lines("--overdue"), [])
        self.assertEqual(self.run_cli("due", "1", "2026-10-01")[0], 0)
        self.assertEqual(self.lines("--overdue"), ["[ ] 1 Old one (due 2026-10-01, OVERDUE)"])

    def test_old_commands_still_work(self):
        self.run_cli("add", "A")
        self.run_cli("add", "B")
        self.assertEqual(self.run_cli("done", "1")[0], 0)
        self.assertEqual(self.run_cli("remove", "2")[0], 0)
        self.assertEqual(self.lines("--pending"), [])
        self.assertEqual(self.lines(), ["[x] 1 A"])

    def test_readme_documents_feature(self):
        readme = (Path(os.environ["SWARMBENCH_WORKSPACE"]) / "README.md").read_text(encoding="utf-8")
        for word in ("--due", "--overdue", "--sort", "--clear", "OVERDUE"):
            self.assertIn(word, readme)
