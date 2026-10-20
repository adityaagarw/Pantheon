"""Matching bank lines to the ledger (CLOSE_POLICY.md, section 3)."""

from decimal import Decimal

from close.banks import is_bank_charge, references

FEE_LIMIT = Decimal("25.00")


def order(lines):
    return sorted(lines, key=lambda l: (l["date"], l["bank"], int(l["id"].split(":")[1])))


def match(lines, items, aliases):
    """Match every line; items' "open" amounts are updated in place.

    Returns {"matches", "bank_charges", "unmatched", "overpayments", "applied"}; "applied"
    is a list of (line, item, amount) for every amount applied, in order.
    """
    by_id = {i["id"]: i for i in items}
    out = {"matches": [], "bank_charges": [], "unmatched": [], "overpayments": [], "fees": [],
           "applied": []}
    for line in order(lines):
        if is_bank_charge(line["description"]):
            out["bank_charges"].append(line)
            continue
        incoming = line["amount"] > 0
        kind = "invoice" if incoming else "bill"
        pay = abs(line["amount"])

        def usable(item):
            return (item["open"] > 0 and item["kind"] == kind
                    and item["currency"] == line["currency"])

        refs = [by_id[r] for r in references(line["description"])
                if r in by_id and usable(by_id[r])]
        applied = []
        if refs:
            total = sum(i["open"] for i in refs)
            if pay == total:
                match_kind = "exact" if len(refs) == 1 else "multi"
                applied = [(i, i["open"]) for i in refs]
            elif pay < total and incoming and total - pay <= FEE_LIMIT:
                match_kind = "fee"
                applied = [(i, i["open"]) for i in refs]
                out["fees"].append((line, total - pay))
            elif pay < total:
                match_kind = "partial"
                left = pay
                for i in sorted(refs, key=lambda i: (i["due"], i["id"])):
                    if left <= 0:
                        break
                    take = min(left, i["open"])
                    applied.append((i, take))
                    left -= take
            else:
                match_kind = "overpayment"
                applied = [(i, i["open"]) for i in refs]
                out["overpayments"].append({"line": line, "counterparty": refs[0]["counterparty"],
                                            "currency": line["currency"], "amount": pay - total})
        else:
            text = line["description"].upper()
            names = [name for name, alias_list in aliases.items()
                     if any(a.upper() in text for a in alias_list)]
            candidates = [i for i in items if i["counterparty"] in names and usable(i)
                          and i["open"] == pay]
            if len(candidates) != 1:
                out["unmatched"].append({"line": line,
                                         "reason": "ambiguous" if candidates else "no match"})
                continue
            match_kind = "alias"
            applied = [(candidates[0], pay)]
        for item, amount in applied:
            item["open"] -= amount
            out["applied"].append((line, item, amount))
        out["matches"].append({"line": line, "kind": match_kind,
                               "items": [(i["id"], amount) for i, amount in applied]})
    return out
