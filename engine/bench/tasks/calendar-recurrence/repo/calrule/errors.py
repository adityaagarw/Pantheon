"""Errors. Given: don't change this file."""


class UnknownZone(KeyError):
    """A time zone that calrule.zones doesn't have."""

    def __init__(self, zone):
        self.zone = zone
        super().__init__(zone)

    def __str__(self):
        return f"unknown time zone {self.zone}"


class RuleError(ValueError):
    """A recurrence rule that can't be read. The message starts with the part's name."""


class CalendarError(ValueError):
    """A problem in a calendar file, at a line (the first line of a folded property)."""

    def __init__(self, line, message):
        self.line = line
        self.message = message
        super().__init__(f"line {line}: {message}")
