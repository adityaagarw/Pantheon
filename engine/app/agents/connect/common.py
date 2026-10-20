"""What the agent connectors share: starting the program, its output, usage limits."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

TIMEOUT = 6 * 60 * 60  # a turn can be long; the user can stop it any time


class ConnectError(RuntimeError):
    pass


def command_argv(command: str) -> list[str]:
    """The program to start, found on PATH or given as a path (a .py runs with this
    engine's Python: handy for scripts and tests)."""
    command = command.strip()
    if command.endswith(".py"):
        return [sys.executable, command]
    found = shutil.which(command)
    if not found:
        raise ConnectError(f"'{command}' isn't installed on this computer (or isn't on PATH)")
    return [found]


class Proc:
    """A running agent program: lines in, lines out, stderr kept (only its tail) so the
    pipe never fills up and stalls it."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self.err: list[str] = []
        self._err_task = asyncio.create_task(self._drain())

    @classmethod
    async def start(cls, argv: list[str], cwd: Path, env: dict[str, str] | None = None,
                    stdin: bool = True, merge_stderr: bool = False) -> Proc:
        """``merge_stderr``: its stderr comes in its output lines (a login prints its link
        there)."""
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env={**os.environ, "NO_COLOR": "1", **(env or {})},
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024)
        return cls(proc)

    async def _drain(self) -> None:
        if self.proc.stderr is None:
            return
        while line := await self.proc.stderr.readline():
            self.err.append(line.decode("utf-8", "replace"))
            del self.err[:-60]

    @property
    def stderr(self) -> str:
        return "".join(self.err)[-3000:]

    async def send(self, obj: Any) -> None:
        """One JSON line to its stdin (nothing if it has closed it)."""
        if self.proc.stdin is None or self.proc.stdin.is_closing():
            return
        try:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def write(self, text: str) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.write(text.encode("utf-8"))
            await self.proc.stdin.drain()

    def close_stdin(self) -> None:
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()

    async def lines(self):
        """Its stdout, line by line, until it ends."""
        assert self.proc.stdout is not None
        while line := await self.proc.stdout.readline():
            yield line.decode("utf-8", "replace")

    async def events(self):
        """Its stdout's JSON lines (anything else is skipped)."""
        async for line in self.lines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    async def wait(self) -> int:
        code = await self.proc.wait()
        await asyncio.gather(self._err_task, return_exceptions=True)
        return code

    async def stop(self) -> None:
        if self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            await self.proc.wait()
        self._err_task.cancel()

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode


# --- usage limits ---------------------------------------------------------------------------

_QUOTA = re.compile(r"usage limit|rate[ -]?limit|quota|too many requests|\b429\b|limit reached|"
                    r"out of (credits|tokens)|insufficient_quota|credit balance", re.I)
_IN = re.compile(r"(?:in|after)\s+((?:\d+\s*(?:d|h|m|s|days?|hours?|hrs?|minutes?|mins?|"
                 r"seconds?|secs?)\s*,?\s*(?:and\s*)?)+)", re.I)
_PART = re.compile(r"(\d+)\s*(d|h|m|s)", re.I)
_EPOCH = re.compile(r"\b(1[6-9]\d{8})\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_CLOCK = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def is_quota(text: str) -> bool:
    return bool(_QUOTA.search(text or ""))


def reset_time(text: str, now: datetime | None = None) -> datetime | None:
    """When a usage-limit message says the limit resets: "try again in 2h 5m", an ISO time,
    a Unix time ("…|1792048800") or "at 5pm" (local time)."""
    now = now or datetime.now(UTC)
    text = text or ""
    if m := _IN.search(text):
        unit = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        secs = sum(int(n) * unit[u.lower()] for n, u in _PART.findall(m.group(1)))
        if secs:
            return now + timedelta(seconds=secs)
    if m := _ISO.search(text):
        try:
            t = datetime.fromisoformat(m.group(0).replace("Z", "+00:00").replace(" ", "T"))
            return t if t.tzinfo else t.replace(tzinfo=UTC)
        except ValueError:
            pass
    if m := _EPOCH.search(text):
        return datetime.fromtimestamp(int(m.group(1)), UTC)
    if m := _CLOCK.search(text):
        hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if half == "pm" and hour < 12:
            hour += 12
        if half == "am" and hour == 12:
            hour = 0
        if hour < 24 and minute < 60:
            local = datetime.now().astimezone()
            t = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if t <= local:
                t += timedelta(days=1)
            return t.astimezone(UTC)
    return None


def text_of(content: Any) -> str:
    """Text from the many shapes a tool result takes (a string, blocks, nested content)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(t for t in (text_of(c) for c in content) if t)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        for key in ("content", "output", "stdout", "result"):
            if key in content:
                return text_of(content[key])
        return json.dumps(content)[:4000]
    return str(content)


def same_place(stored: str | None, workdir: Path) -> bool:
    """A session continues only in the folder it was made in (none recorded: assume so)."""
    if not stored:
        return True
    try:
        return Path(stored).resolve() == workdir.resolve()
    except OSError:
        return False
