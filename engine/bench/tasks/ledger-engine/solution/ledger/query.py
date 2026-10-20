"""Queries: which postings a report uses (LEDGER.md section 4)."""

import re


class _Term:
    def __init__(self, text):
        self.kind, self.name = "acct", None
        if text.startswith("desc:"):
            self.kind, text = "desc", text[5:]
        elif text.startswith("tag:"):
            self.kind, text = "tag", text[4:]
            if "=" in text:
                self.name, text = text.split("=", 1)
            else:
                self.name, text = text, None
        self.rx = re.compile(text, re.I) if text is not None else None

    def matches(self, p):
        if self.kind == "acct":
            return bool(self.rx.search(p.account))
        if self.kind == "desc":
            return bool(self.rx.search(p.transaction.description))
        if self.name not in p.tags:
            return False
        return self.rx is None or bool(self.rx.search(p.tags[self.name]))


class Query:
    def __init__(self, terms=()):
        self.plain, self.must, self.nots = [], [], []
        for t in terms or ():
            if t.startswith("not:"):
                self.nots.append(_Term(t[4:]))
                continue
            term = _Term(t)
            (self.plain if term.kind == "acct" else self.must).append(term)

    def matches(self, posting):
        if self.plain and not any(t.matches(posting) for t in self.plain):
            return False
        if not all(t.matches(posting) for t in self.must):
            return False
        return not any(t.matches(posting) for t in self.nots)
