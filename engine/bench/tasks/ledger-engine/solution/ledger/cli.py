"""The command line (LEDGER.md section 7)."""

import argparse
import sys

from .budget import budget_report
from .dates import parse_date
from .errors import JournalError, PriceError
from .parser import load
from .reports import balance, income_statement, register

COMMANDS = {"balance": "balance", "bal": "balance", "register": "register", "reg": "register",
            "incomestatement": "is", "is": "is", "budget": "budget"}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"ledger: {message}", file=sys.stderr)
        raise SystemExit(2)


def _rows(report, flat, prec, out):
    for r in report.rows:
        lines = r.amount.lines(prec)
        if flat:
            name = r.account
        else:
            segs = r.account.split(":")
            name = "  " * (len(segs) - 1) + segs[-1]
        out.append(f"{lines[0]:>20}  {name}")
        out.extend(f"{x:>20}" for x in lines[1:])


def _total(mixed, prec, out):
    out.append("-" * 20)
    out.extend(f"{x:>20}" for x in mixed.lines(prec))


def main(argv=None):
    ap = _Parser(prog="ledger")
    ap.add_argument("-f", "--file", required=True)
    ap.add_argument("-b", "--begin", type=parse_date)
    ap.add_argument("-e", "--end", type=parse_date)
    ap.add_argument("--depth", type=int)
    ap.add_argument("--flat", action="store_true")
    ap.add_argument("-E", "--empty", action="store_true")
    ap.add_argument("-V", "--value")
    ap.add_argument("command")
    ap.add_argument("query", nargs="*")
    args = ap.parse_intermixed_args(argv)
    cmd = COMMANDS.get(args.command)
    if cmd is None:
        print(f"ledger: unknown command: {args.command}", file=sys.stderr)
        return 2
    try:
        journal = load(args.file)
        prec = journal.precisions
        out = []
        if cmd == "balance":
            rep = balance(journal, args.query, args.begin, args.end, args.depth, args.flat,
                          args.empty, args.value)
            _rows(rep, args.flat, prec, out)
            _total(rep.total, prec, out)
        elif cmd == "register":
            for r in register(journal, args.query, args.begin, args.end):
                a, t = r.amount.lines(prec), r.total.lines(prec)
                for i in range(max(len(a), len(t))):
                    left = (f"{r.date.isoformat()} {r.payee[:20]:<20} {r.account[:24]:<24}"
                            if i == 0 else " " * 56)
                    x = a[i] if i < len(a) else ""
                    y = t[i] if i < len(t) else ""
                    out.append(f"{left} {x:>14} {y:>14}".rstrip())
        elif cmd == "is":
            st = income_statement(journal, args.begin, args.end, args.depth)
            out.append("Revenues:")
            _rows(st.revenues, False, prec, out)
            _total(st.revenues.total, prec, out)
            out.append("")
            out.append("Expenses:")
            _rows(st.expenses, False, prec, out)
            _total(st.expenses.total, prec, out)
            out.append("")
            out.append("Net:")
            out.extend(f"{x:>20}" for x in st.net.lines(prec))
        else:
            if args.begin is None or args.end is None:
                print("ledger: budget needs --begin and --end", file=sys.stderr)
                return 2
            for r in budget_report(journal, args.begin, args.end):
                pct = f"{r.percent:>5}%" if r.percent is not None else f"{'-':>6}"
                line = (f"{r.month.strftime('%Y-%m')} {r.account:<24} {r.actual.lines(prec)[0]:>14} "
                        f"{r.budget.lines(prec)[0]:>14} {pct}{' over' if r.over else ''}")
                out.append(line.rstrip())
    except (JournalError, PriceError) as e:
        print(f"ledger: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ledger: {e}", file=sys.stderr)
        return 1
    sys.stdout.write("\n".join(out) + "\n")
    return 0
