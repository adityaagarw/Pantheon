import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from todo import cli


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "todo.json"
        self.env = mock.patch.dict(os.environ, {"TODO_FILE": str(self.path)})
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

    def test_add_and_list(self):
        self.assertEqual(self.run_cli("add", "Buy milk")[0], 0)
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "[ ] 1 Buy milk")

    def test_done(self):
        self.run_cli("add", "Buy milk")
        self.run_cli("done", "1")
        self.assertEqual(self.run_cli("list")[1].strip(), "[x] 1 Buy milk")

    def test_pending_hides_done(self):
        self.run_cli("add", "A")
        self.run_cli("add", "B")
        self.run_cli("done", "1")
        self.assertEqual(self.run_cli("list", "--pending")[1].strip(), "[ ] 2 B")

    def test_unknown_id(self):
        code, _, err = self.run_cli("done", "9")
        self.assertEqual(code, 1)
        self.assertIn("no task with id 9", err)

    def test_data_file(self):
        self.run_cli("add", "Buy milk")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["tasks"][0]["text"], "Buy milk")


if __name__ == "__main__":
    unittest.main()
