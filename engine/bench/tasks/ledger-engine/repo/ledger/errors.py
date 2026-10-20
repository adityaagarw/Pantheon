"""Errors. Given: don't change this file."""


class JournalError(Exception):
    """A problem in a journal, at a line of a file."""

    def __init__(self, path, line, message):
        self.path = path or "-"
        self.line = line
        self.message = message
        super().__init__(f"{self.path}:{line}: {message}")


class PriceError(Exception):
    """No price to convert one commodity into another."""
