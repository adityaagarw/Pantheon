"""The sandbox desktop: a Linux desktop in Docker that agents use instead of your own
computer (app/computer/sandbox.py: trycua's cua-ubuntu image with cua-spacesd).

Pantheon sets it up when you pick it in Settings → This computer: it checks for Docker,
downloads the image and creates the container.
"""

from __future__ import annotations


def _desktop():
    from app.computer import sandbox

    return sandbox.computer()


def register(api) -> None:
    api.computer_backend(
        "cua", "Sandbox desktop (Docker)",
        "A Linux desktop in Docker that agents use freely: your own files and apps are out of "
        "reach. Agents click and type there like a person, and you can watch or take over. "
        "Needs Docker; Pantheon downloads the desktop (about 1.8 GB) the first time.", _desktop)
