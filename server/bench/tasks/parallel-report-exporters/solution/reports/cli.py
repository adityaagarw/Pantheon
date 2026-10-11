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
    p.add_argument("--format", default="text", help="one of: " + ", ".join(exporters.available()))
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    sub.add_parser("formats", help="list the export formats")
    args = parser.parse_args(argv)

    if args.command == "formats":
        for name in exporters.available():
            print(name)
        return 0

    try:
        exporter = exporters.get_exporter(args.format)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = Report.from_dict(json.loads(Path(args.input).read_text(encoding="utf-8")))
    text = exporter(report)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="")
    else:
        sys.stdout.write(text)
    return 0
