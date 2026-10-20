"""The command line (RECUR.md section 6)."""

import argparse
import sys
from datetime import datetime

from calrule import zones
from calrule.agenda import agenda
from calrule.errors import CalendarError, UnknownZone
from calrule.ics import read_calendar
from calrule.localtime import localize


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = _Parser(prog="calrule")
    parser.add_argument("file")
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--tz", default="UTC")
    try:
        args = parser.parse_args(argv)
        zones.check(args.tz)
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")
        if end <= start:
            raise ValueError("--to must be after --from")
        try:
            with open(args.file, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise ValueError(f"cannot read {args.file}") from None
        try:
            events = read_calendar(text)
        except CalendarError as e:
            raise ValueError(f"{args.file}: {e}") from None
        sys.stdout.write(agenda(events, localize(start, args.tz), localize(end, args.tz), args.tz))
        return 0
    except (ValueError, UnknownZone) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
