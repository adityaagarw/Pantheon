"""Run a folder of unittest tests and write the result as JSON.

The grader starts this script in a separate process:

    python _unittest_runner.py --tests DIR --code DIR --out FILE

It writes {"ran": n, "passed": [...], "failed": [...], "skipped": n}.
"""

import argparse
import io
import json
import os
import sys
import unittest


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    tests_dir = os.path.abspath(args.tests)
    code_dir = os.path.abspath(args.code)
    os.chdir(code_dir)
    sys.path.insert(0, code_dir)

    if os.path.isfile(os.path.join(tests_dir, "__init__.py")):
        top_level = code_dir
        # Helpers next to the tests import either way ("from support import" works under
        # `python -m unittest discover -s tests`), so both must work here.
        sys.path.append(tests_dir)
    else:
        top_level = tests_dir
        sys.path.insert(0, tests_dir)

    suite = unittest.defaultTestLoader.discover(tests_dir, pattern="test*.py", top_level_dir=top_level)
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordingResult)
    result = runner.run(suite)

    failed = []
    for kind, items in (("failure", result.failures), ("error", result.errors)):
        for test, trace in items:
            failed.append({"id": test.id(), "kind": kind, "message": trace[-1500:]})
    data = {
        "ran": result.testsRun,
        "passed": result.passed,
        "failed": failed,
        "skipped": len(result.skipped),
        "log_tail": stream.getvalue()[-4000:],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
