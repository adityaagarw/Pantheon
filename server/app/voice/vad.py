"""Voice activity detection for live talk.

Primary engine: audio.cpp's Silero VAD (``/v1/tasks/run`` with the
``silero-vad`` model). audio.cpp only reads audio from files on its own
machine, so clips are written to a folder both sides can see
(``voice_share_dir`` here, ``voice_share_host_dir`` as audio.cpp sees it).

Fallback engine: a small adaptive energy detector, used automatically when
audio.cpp's VAD is unreachable so live talk keeps working.

All audio is 16 kHz mono signed 16-bit PCM. Segments are (start, end) sample
offsets into the clip that was analysed.
"""

from __future__ import annotations

import array
import asyncio
import logging
import math
import uuid
import wave
from pathlib import Path, PureWindowsPath
from typing import Protocol

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)
RATE = 16_000

Segment = tuple[int, int]


class Vad(Protocol):
    name: str

    async def segments(self, pcm: bytes) -> list[Segment]: ...


class VadUnavailable(RuntimeError):
    pass


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


class AudioCppVad:
    """Silero VAD running inside audio.cpp."""

    name = "audio.cpp silero-vad"

    def __init__(self, base_url: str, model: str, share_dir: str, host_dir: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.share = Path(share_dir).resolve()
        self.share.mkdir(parents=True, exist_ok=True)
        self.host_dir = host_dir.strip()
        self.file = f"live-{uuid.uuid4().hex[:10]}.wav"
        self.client = httpx.AsyncClient(timeout=10)

    def _host_path(self) -> str:
        if not self.host_dir:
            return str(self.share / self.file)
        if "\\" in self.host_dir or (len(self.host_dir) > 1 and self.host_dir[1] == ":"):
            return str(PureWindowsPath(self.host_dir) / self.file)
        return f"{self.host_dir.rstrip('/')}/{self.file}"

    async def segments(self, pcm: bytes) -> list[Segment]:
        await asyncio.to_thread(write_wav, self.share / self.file, pcm)
        try:
            resp = await self.client.post(
                f"{self.base_url}/v1/tasks/run",
                json={"model": self.model, "request": {"audio": self._host_path()}},
            )
        except httpx.HTTPError as e:
            raise VadUnavailable(f"audio.cpp unreachable: {e}") from None
        if resp.status_code >= 400:
            raise VadUnavailable(f"audio.cpp VAD error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return [(int(s["start_sample"]), int(s["end_sample"])) for s in data.get("segments", [])]

    async def close(self) -> None:
        await self.client.aclose()
        try:
            (self.share / self.file).unlink(missing_ok=True)
        except OSError:
            pass


class EnergyVad:
    """Adaptive RMS detector (fallback). 30 ms frames, noise floor tracking."""

    name = "energy (fallback)"
    FRAME = 480  # 30 ms

    def __init__(self) -> None:
        self.noise = 200.0

    async def segments(self, pcm: bytes) -> list[Segment]:
        samples = array.array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        flags: list[bool] = []
        for i in range(0, len(samples) - self.FRAME + 1, self.FRAME):
            frame = samples[i : i + self.FRAME]
            rms = math.sqrt(sum(x * x for x in frame) / self.FRAME)
            speech = rms > max(600.0, self.noise * 3.0)
            if not speech:
                self.noise = 0.95 * self.noise + 0.05 * rms
            flags.append(speech)
        segs: list[Segment] = []
        start = None
        gap = 0
        for idx, f in enumerate(flags + [False] * 8):
            if f:
                if start is None:
                    start = idx
                gap = 0
            elif start is not None:
                gap += 1
                if gap > 6:  # 180 ms hangover
                    end = idx - gap + 1
                    if end - start >= 3:
                        segs.append((start * self.FRAME, end * self.FRAME))
                    start, gap = None, 0
        return segs

    async def close(self) -> None:
        return None


async def make_vad(base_url: str, model: str | None = None) -> tuple[Vad, str | None]:
    """Return a working VAD engine and, if it's the fallback, the reason."""
    engine = AudioCppVad(base_url, model or settings.voice_vad_model, settings.voice_share_dir,
                         settings.voice_share_host_dir)
    try:
        await engine.segments(b"\x00\x00" * (RATE // 4))
        return engine, None
    except (VadUnavailable, OSError, ValueError) as e:
        await engine.close()
        log.warning("audio.cpp VAD unavailable, using energy fallback: %s", e)
        return EnergyVad(), str(e)
