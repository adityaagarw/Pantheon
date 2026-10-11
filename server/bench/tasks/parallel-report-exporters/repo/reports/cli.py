"""Command line: python -m reports export --format text report.json"""

import argparse
import json
import sys
from pathlib import Path

from . import exporters
from .model import Report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="reports")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("export", help="export a report file")
    p.add_argument("input", help="report JSON file")
    p.add_argument("--format", default="text")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    args = parser.parse_args(argv)

    report = Report.from_dict(json.loads(Path(args.input).read_text(encoding="utf-8")))
    text = exporters.export(report, args.format)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="")
    else:
        sys.stdout.write(text)
    return 0
