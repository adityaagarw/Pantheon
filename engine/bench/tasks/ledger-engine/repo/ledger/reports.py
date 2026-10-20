"""Reports (LEDGER.md section 5). To build."""


def balance(journal, query=None, begin=None, end=None, depth=None, flat=False, empty=False,
            value=None):
    raise NotImplementedError


def register(journal, query=None, begin=None, end=None):
    raise NotImplementedError


def income_statement(journal, begin=None, end=None, depth=None):
    raise NotImplementedError


def balance_by_period(journal, interval, query=None, begin=None, end=None, depth=None):
    raise NotImplementedError
