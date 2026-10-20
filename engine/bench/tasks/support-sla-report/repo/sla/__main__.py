"""python -m sla <data_dir>: write out/report.json (SLA_POLICY.md, section 7)."""

import json
import sys
from pathlib import Path

from sla.report import build


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m sla <data_dir>", file=sys.stderr)
        return 2
    report = build(argv[0])
    out = Path("out") / "report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(report['weeks'])} weeks, {len(report['breaches'])} breaches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
