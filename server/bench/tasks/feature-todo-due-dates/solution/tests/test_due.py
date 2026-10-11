import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from todo import cli


class DueDateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "todo.json"
        self.env = mock.patch.dict(os.environ, {"TODO_FILE": str(path), "TODO_TODAY": "2026-10-10"})
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

    def test_due_suffix_and_overdue(self):
        self.run_cli("add", "A", "--due", "2026-10-20")
        self.run_cli("add", "B", "--due", "2026-10-01")
        lines = self.run_cli("list")[1].splitlines()
        self.assertEqual(lines, ["[ ] 1 A (due 2026-10-20)", "[ ] 2 B (due 2026-10-01, OVERDUE)"])

    def test_invalid_date(self):
        code, _, err = self.run_cli("add", "A", "--due", "2026-13-01")
        self.assertEqual(code, 2)
        self.assertIn("invalid date", err)

    def test_sort_due(self):
        self.run_cli("add", "A")
        self.run_cli("add", "B", "--due", "2026-12-01")
        self.run_cli("add", "C", "--due", "2026-11-01")
        ids = [line.split()[2] for line in self.run_cli("list", "--sort", "due")[1].splitlines()]
        self.assertEqual(ids, ["3", "2", "1"])

    def test_due_command(self):
        self.run_cli("add", "A")
        self.assertEqual(self.run_cli("due", "1", "2026-10-11")[0], 0)
        self.assertIn("(due 2026-10-11)", self.run_cli("list")[1])
        self.run_cli("due", "1", "--clear")
        self.assertEqual(self.run_cli("list")[1].strip(), "[ ] 1 A")


if __name__ == "__main__":
    unittest.main()
