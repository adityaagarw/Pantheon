"""python -m close <data_dir> <YYYY-MM-DD>: close the month, write out/close-<YYYY-MM>.json."""

import json
import sys
from datetime import date
from pathlib import Path

from close.report import run


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m close <data_dir> <YYYY-MM-DD>", file=sys.stderr)
        return 2
    close_date = date.fromisoformat(argv[1])
    result = run(argv[0], close_date)
    out = Path("out") / f"close-{close_date:%Y-%m}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
