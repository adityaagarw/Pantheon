"""Live, hands-free talk: continuous audio in, utterances out.

The session keeps a rolling buffer of 16 kHz PCM and runs VAD on the most
recent window a few times a second. It emits ``speech_start`` as soon as you
begin talking (the browser uses this to stop the agent mid-sentence), and when
you've been quiet for ``silence_ms`` it cuts the utterance, transcribes it and
hands the text on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.voice.vad import RATE, Vad, VadUnavailable

log = logging.getLogger(__name__)
BYTES_PER_MS = RATE * 2 // 1000

Emit = Callable[[dict[str, Any]], Awaitable[None]]
Transcribe = Callable[[bytes], Awaitable[str]]
OnText = Callable[[str], Awaitable[Any]]


class LiveSession:
    def __init__(
        self,
        vad: Vad,
        transcribe: Transcribe,
        on_text: OnText,
        emit: Emit,
        *,
        silence_ms: int = 700,
        min_speech_ms: int = 220,
        window_ms: int = 1200,
        tick_ms: int = 200,
        preroll_ms: int = 300,
        max_utterance_s: int = 30,
        fallback: Vad | None = None,
    ) -> None:
        self.vad = vad
        self.fallback = fallback
        self.transcribe = transcribe
        self.on_text = on_text
        self.emit = emit
        self.silence = silence_ms * BYTES_PER_MS
        self.min_speech = min_speech_ms * RATE // 1000
        self.window = window_ms * BYTES_PER_MS
        self.tick_every = tick_ms * BYTES_PER_MS
        self.preroll = preroll_ms * BYTES_PER_MS
        self.max_utt = max_utterance_s * RATE * 2
        self.buf = bytearray()
        self.since_tick = 0
        self.speaking = False
        self.start = 0  # byte offset of speech start within buf
        self.last_speech_end = 0  # byte offset
        self.busy = False
        self.tick_task: asyncio.Task | None = None
        self.pending: set[asyncio.Task] = set()

    async def feed(self, pcm: bytes) -> None:
        """Append audio; VAD runs in the background so receiving never stalls."""
        self.buf.extend(pcm)
        self.since_tick += len(pcm)
        if self.since_tick >= self.tick_every and not self.busy:
            self.since_tick = 0
            self.busy = True
            self.tick_task = asyncio.create_task(self._safe_tick())

    async def _safe_tick(self) -> None:
        try:
            await self.tick()
        except Exception as e:  # noqa: BLE001 - keep the session alive
            log.exception("live VAD tick failed")
            await self.emit({"type": "error", "message": f"voice detection failed: {e}"})

    async def tick(self) -> None:
        self.busy = True
        try:
            window = bytes(self.buf[-self.window :])
            offset = len(self.buf) - len(window)
            try:
                segs = await self.vad.segments(window)
            except VadUnavailable as e:
                if self.fallback is None:
                    raise
                log.warning("VAD failed mid-session, switching to fallback: %s", e)
                self.vad, self.fallback = self.fallback, None
                await self.emit({"type": "vad", "engine": self.vad.name, "reason": str(e)})
                segs = await self.vad.segments(window)
            now = len(self.buf)
            if not self.speaking:
                for s, e in segs:
                    if e - s >= self.min_speech:
                        self.speaking = True
                        self.start = offset + s * 2
                        self.last_speech_end = offset + e * 2
                        await self.emit({"type": "speech_start"})
                        break
                if not self.speaking:
                    keep = self.window + self.preroll
                    if len(self.buf) > keep * 2:
                        del self.buf[: len(self.buf) - keep]
                return
            if segs:
                self.last_speech_end = max(self.last_speech_end, offset + max(e for _, e in segs) * 2)
            quiet = now - self.last_speech_end
            too_long = now - self.start >= self.max_utt
            if quiet >= self.silence or too_long:
                await self._finish()
        finally:
            self.busy = False

    async def _finish(self) -> None:
        begin = max(0, self.start - self.preroll)
        end = min(len(self.buf), self.last_speech_end + 200 * BYTES_PER_MS)
        utterance = bytes(self.buf[begin:end])
        del self.buf[:end]
        self.speaking = False
        self.start = self.last_speech_end = 0
        await self.emit({"type": "speech_end", "ms": len(utterance) // BYTES_PER_MS})
        task = asyncio.create_task(self._handle(utterance))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _handle(self, utterance: bytes) -> None:
        try:
            text = (await self.transcribe(utterance)).strip()
        except Exception as e:  # noqa: BLE001 - report, keep listening
            await self.emit({"type": "error", "message": f"transcription failed: {e}"})
            return
        if not text.strip(".!?, "):
            await self.emit({"type": "transcript", "text": "", "ignored": True})
            return
        await self.emit({"type": "transcript", "text": text})
        try:
            result = await self.on_text(text)
            await self.emit({"type": "sent", "text": text, **(result or {})})
        except Exception as e:  # noqa: BLE001
            await self.emit({"type": "error", "message": str(e)})

    async def flush(self) -> None:
        """End of stream: finish an in-progress utterance and wait for it."""
        if self.tick_task is not None:
            await asyncio.gather(self.tick_task, return_exceptions=True)
        if self.speaking:
            await self._finish()
        if self.pending:
            await asyncio.gather(*self.pending, return_exceptions=True)
