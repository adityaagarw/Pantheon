"""Live talk: VAD-driven turn taking (unit) and the WebSocket end to end."""

from __future__ import annotations

import array
import asyncio
import math

from app.voice import live as live_mod
from app.voice.live import LiveSession
from app.voice.vad import RATE, EnergyVad, VadUnavailable


def tone(ms: int, amp: int = 6000, hz: int = 220) -> bytes:
    n = RATE * ms // 1000
    return array.array("h", (int(amp * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n))).tobytes()


def silence(ms: int) -> bytes:
    return b"\x00\x00" * (RATE * ms // 1000)


async def feed_chunked(session: LiveSession, pcm: bytes, chunk_ms: int = 100) -> None:
    step = RATE * 2 * chunk_ms // 1000
    for i in range(0, len(pcm), step):
        await session.feed(pcm[i : i + step])
        for _ in range(3):  # let background VAD ticks run, as in a real stream
            await asyncio.sleep(0)


async def test_energy_vad_finds_speech():
    segs = await EnergyVad().segments(silence(500) + tone(600) + silence(500))
    assert len(segs) == 1
    start, end = segs[0]
    assert 0.45 * RATE < start < 0.55 * RATE
    assert 1.05 * RATE < end < 1.35 * RATE


async def test_session_detects_utterances_and_transcribes_each():
    events: list[dict] = []
    heard: list[bytes] = []
    sent: list[str] = []

    async def emit(e):
        events.append(e)

    async def transcribe(pcm: bytes) -> str:
        heard.append(pcm)
        return f"utterance {len(heard)}"

    async def on_text(text: str):
        sent.append(text)
        return {"messageId": f"m{len(sent)}"}

    s = LiveSession(EnergyVad(), transcribe, on_text, emit, silence_ms=600)
    await feed_chunked(s, silence(800) + tone(900) + silence(1200) + tone(700) + silence(1200))
    await s.flush()
    kinds = [e["type"] for e in events]
    assert kinds.count("speech_start") == 2
    assert kinds.count("speech_end") == 2
    assert sent == ["utterance 1", "utterance 2"]
    # Each clip contains its speech plus a little padding, not the whole stream.
    assert all(0.7 * RATE * 2 < len(p) < 2.0 * RATE * 2 for p in heard)


async def test_silence_and_short_blips_are_ignored():
    events: list[dict] = []

    async def emit(e):
        events.append(e)

    async def never(_):
        raise AssertionError("should not transcribe")

    s = LiveSession(EnergyVad(), never, never, emit)
    await feed_chunked(s, silence(1500) + tone(90) + silence(1500))
    await s.flush()
    assert not [e for e in events if e["type"] == "speech_start"]


async def test_falls_back_when_primary_vad_dies():
    events: list[dict] = []

    class Broken:
        name = "broken"

        async def segments(self, pcm):
            raise VadUnavailable("gone")

    async def emit(e):
        events.append(e)

    async def transcribe(_):
        return "hello"

    async def on_text(_):
        return {}

    s = LiveSession(Broken(), transcribe, on_text, emit, fallback=EnergyVad(), silence_ms=500)
    await feed_chunked(s, silence(600) + tone(800) + silence(1000))
    await s.flush()
    assert any(e["type"] == "vad" and "energy" in e["engine"] for e in events)
    assert any(e["type"] == "sent" for e in events)


async def test_live_websocket_sends_voice_to_agent(app_client, monkeypatch):
    from httpx import AsyncClient
    from httpx_ws import aconnect_ws
    from httpx_ws.transport import ASGIWebSocketTransport

    from app.api import voice as voice_api
    from app.main import app
    from app.models import Message
    from tests.helpers import make_agent, make_org, rows, say, script

    org = await make_org(app_client)
    vera = await make_agent(app_client, org["id"], "Vera")
    script(vera["id"], say("On it."))

    async def fake_vad(base_url, model=None):
        return EnergyVad(), "audio.cpp not available in tests"

    async def fake_transcribe(data, *a, **k):
        return "please summarise today's work"

    monkeypatch.setattr(voice_api, "make_vad", fake_vad)
    monkeypatch.setattr(voice_api, "transcribe_bytes", fake_transcribe)
    _ = live_mod
    async with AsyncClient(transport=ASGIWebSocketTransport(app), base_url="http://test") as c:
        async with aconnect_ws(f"/api/v1/voice/live?org={org['id']}&agent=Vera", c) as ws:
            ready = await ws.receive_json()
            assert ready["type"] == "ready" and "energy" in ready["vad"]
            pcm = silence(600) + tone(900) + silence(1100)
            step = RATE * 2 // 10
            for i in range(0, len(pcm), step):
                await ws.send_bytes(pcm[i : i + step])
            got: list[dict] = []
            while not any(e.get("type") == "sent" for e in got):
                got.append(await ws.receive_json(timeout=10))
            await ws.send_text('{"type":"stop"}')
    assert [e["type"] for e in got][:3] == ["speech_start", "speech_end", "transcript"]
    sent = next(e for e in got if e["type"] == "sent")
    assert sent["text"] == "please summarise today's work" and sent["messageId"]
    msgs = await rows(Message, Message.id == sent["messageId"])
    assert msgs[0].sender_type == "user" and msgs[0].meta == {"via": "voice"}
