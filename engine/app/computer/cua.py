"""Errors from the computer agents use (the sandbox desktop, or this computer).

Pantheon 1 talked to trycua's computer-server here; the sandbox now runs cua-spacesd,
reached through cua-driver's MCP (app/computer/sandbox.py).
"""


class ComputerError(RuntimeError):
    pass
