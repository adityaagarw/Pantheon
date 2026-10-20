"""Voice: speech-to-text and text-to-speech through an OpenAI-compatible audio server.

That is a local audio.cpp ``audiocpp_server`` by default, but any server speaking
the OpenAI audio API (``/v1/audio/transcriptions``, ``/v1/audio/speech``) works:
OpenAI itself, Groq, Speaches, Kokoro-FastAPI... The browser never talks to it
directly; the backend proxies so the URL, models and API key live in one place.

Voice is optional. Without a reachable server, ``/speak`` answers with a
``{"browser": true, "voice": ...}`` hint and the page speaks with the browser's
own voices; speech input can fall back to the browser's recognizer if enabled.
"""

from __future__ import annotations

import io
import time
import wave
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import decrypt_secret, encrypt_secret
from app.models import Agent, Org, Setting
from app.services import comms
from app.services.comms import Sender
from app.voice.live import LiveSession
from app.voice.vad import RATE, EnergyVad, make_vad

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
KEY = "voice"
EDITABLE = {"baseUrl", "sttModel", "ttsModel", "defaultVoice", "autoSpeak", "language",
            "browserTts", "browserStt"}
BROWSER_PREFIX = "browser:"
# OpenAI's speech API has named voices but no endpoint that lists them.
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage",
                 "shimmer", "verse"]


async def get_config() -> dict[str, Any]:
    """The full voice config, including the encrypted API key (never sent to the page)."""
    async with SessionLocal() as session:
        row = await session.get(Setting, KEY)
    cfg = {"baseUrl": settings.voice_base_url, "sttModel": settings.voice_stt_model,
           "ttsModel": settings.voice_tts_model, "defaultVoice": "", "autoSpeak": False,
           "language": "", "browserTts": True, "browserStt": False}
    if row is not None and isinstance(row.value, dict):
        cfg.update({k: v for k, v in row.value.items() if v is not None})
    return cfg


def public(cfg: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in cfg.items() if k != "apiKeyEnc"}
    out["hasApiKey"] = bool(cfg.get("apiKeyEnc"))
    return out


def _auth(cfg: dict[str, Any]) -> dict[str, str]:
    if not cfg.get("apiKeyEnc"):
        return {}
    try:
        return {"Authorization": f"Bearer {decrypt_secret(cfg['apiKeyEnc'])}"}
    except Exception:  # noqa: BLE001 - a key sealed with another encryption key is unusable
        return {}


def _client(cfg: dict[str, Any], timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers=_auth(cfg))


def _base(cfg: dict[str, Any]) -> str:
    return str(cfg.get("baseUrl") or "").strip().rstrip("/")


_reach: dict[str, tuple[float, bool]] = {}


async def reachable(cfg: dict[str, Any]) -> bool:
    """Is the speech server up? (Cached briefly: pages ask on every load.)"""
    base = _base(cfg)
    if not base:
        return False
    cache_key = f"{base}|{bool(cfg.get('apiKeyEnc'))}"
    hit = _reach.get(cache_key)
    if hit and time.monotonic() - hit[0] < 20:
        return hit[1]
    ok = False
    try:
        async with _client(cfg, 4) as client:
            # audio.cpp has /health; hosted APIs (OpenAI, Groq) answer /v1/models.
            for path in ("/health", "/v1/models"):
                if (await client.get(base + path)).status_code < 400:
                    ok = True
                    break
    except httpx.HTTPError:
        ok = False
    _reach[cache_key] = (time.monotonic(), ok)
    return ok


@router.get("/config")
async def read_config() -> dict[str, Any]:
    return public(await get_config())


@router.put("/config")
async def write_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    cfg = {**await get_config(), **{k: v for k, v in body.items() if k in EDITABLE}}
    if "apiKey" in body:  # write-only; "" clears it
        key = str(body.get("apiKey") or "").strip()
        if key:
            cfg["apiKeyEnc"] = encrypt_secret(key)
        else:
            cfg.pop("apiKeyEnc", None)
    async with SessionLocal() as session:
        row = await session.get(Setting, KEY)
        if row is None:
            session.add(Setting(key=KEY, value=cfg))
        else:
            row.value = cfg
        await session.commit()
    _reach.clear()
    return public(cfg)


@router.get("/status")
async def status() -> dict[str, Any]:
    """What voice the UI can offer: a speech server, and/or the browser's own speech."""
    cfg = await get_config()
    return {"server": await reachable(cfg), "browserTts": bool(cfg.get("browserTts", True)),
            "browserStt": bool(cfg.get("browserStt", False)), "language": cfg.get("language", "")}


@router.get("/health")
async def health() -> dict[str, Any]:
    cfg = await get_config()
    base = _base(cfg)
    if not base:
        return {"ok": False, "error": "no speech server configured", "baseUrl": ""}
    try:
        async with _client(cfg, 5) as client:
            h = await client.get(f"{base}/health")
            models: list[str] = []
            listed = False
            try:
                m = await client.get(f"{base}/v1/models")
                listed = m.status_code < 400
                models = [str(x.get("id")) for x in m.json().get("data", []) if x.get("id")]
            except Exception:  # noqa: BLE001
                pass
        vad, reason = await make_vad(base)
        await vad.close()
        return {"ok": h.status_code < 400 or listed, "status": h.status_code, "models": models,
                "baseUrl": base, "vad": vad.name, "vadError": reason,
                "shareDir": str(settings.voice_share_dir),
                "shareHostDir": settings.voice_share_host_dir or None}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "baseUrl": base}


class SpeechError(RuntimeError):
    pass


async def transcribe_bytes(data: bytes, filename: str = "speech.wav",
                           content_type: str = "audio/wav", language: str = "") -> str:
    cfg = await get_config()
    form: dict[str, str] = {}
    if cfg.get("sttModel"):
        form["model"] = cfg["sttModel"]
    if language or cfg.get("language"):
        form["language"] = language or cfg["language"]
    try:
        async with _client(cfg, 120) as client:
            resp = await client.post(
                f"{_base(cfg)}/v1/audio/transcriptions",
                files={"file": (filename, data, content_type)}, data=form,
            )
    except httpx.HTTPError as e:
        raise SpeechError(f"speech service unreachable: {e}") from None
    if resp.status_code >= 400:
        raise SpeechError(f"speech service error {resp.status_code}: {resp.text[:500]}")
    try:
        payload = resp.json()
        text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
    except ValueError:
        text = resp.text
    return text.strip()


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("")) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty audio")
    try:
        text = await transcribe_bytes(data, file.filename or "speech.webm",
                                      file.content_type or "audio/webm", language)
    except SpeechError as e:
        raise HTTPException(502, str(e)) from None
    return {"text": text}


@router.websocket("/live")
async def live_talk(ws: WebSocket) -> None:
    """Hands-free conversation with an agent.

    Client sends 16 kHz mono PCM16 as binary frames (plus ``{"type":"stop"}``);
    the server replies with ``ready``, ``speech_start``, ``speech_end``,
    ``transcript``, ``sent`` and ``error`` events. Transcribed speech is sent
    to the agent as a direct message from the user.
    """
    await ws.accept()
    org_id = ws.query_params.get("org", "")
    agent_ref = ws.query_params.get("agent", "")
    silence_ms = int(ws.query_params.get("silence") or 700)
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        agent = await comms.resolve_agent(session, org_id, agent_ref) if org else None
    if org is None or agent is None:
        await ws.send_json({"type": "error", "message": "unknown organization or agent"})
        await ws.close()
        return
    cfg = await get_config()
    vad, reason = await make_vad(cfg["baseUrl"])
    fallback = EnergyVad() if not isinstance(vad, EnergyVad) else None
    closed = False

    async def emit(event: dict[str, Any]) -> None:
        if closed:
            return
        try:
            await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def transcribe_pcm(pcm: bytes) -> str:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        return await transcribe_bytes(buf.getvalue())

    async def on_text(text: str) -> dict[str, Any]:
        msg = await comms.send_direct(org_id, Sender.user(), agent.id, text,
                                      meta={"via": "voice"})
        return {"messageId": msg.id, "channelId": msg.channel_id}

    session_ = LiveSession(vad, transcribe_pcm, on_text, emit, silence_ms=max(300, min(silence_ms, 3000)),
                           fallback=fallback)
    await emit({"type": "ready", "vad": vad.name, "fallbackReason": reason, "agent": agent.name})
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                await session_.feed(msg["bytes"])
            elif msg.get("text"):
                if '"stop"' in msg["text"]:
                    await session_.flush()
                    break
    except WebSocketDisconnect:
        pass
    finally:
        closed = True
        await vad.close()
        if fallback is not None:
            await fallback.close()
        try:
            await ws.close()
        except RuntimeError:
            pass


@router.get("/voices")
async def voices() -> dict[str, Any]:
    """The named voices the speech model offers (empty if it has none or can't be reached)."""
    cfg = await get_config()
    common = {"default": cfg.get("defaultVoice", ""), "model": cfg.get("ttsModel", "")}
    if urlparse(_base(cfg)).hostname == "api.openai.com":
        return {"voices": OPENAI_VOICES, **common}
    params = {"model": cfg["ttsModel"]} if cfg.get("ttsModel") else {}
    try:
        async with _client(cfg, 8) as client:
            resp = await client.get(f"{_base(cfg)}/v1/audio/voices", params=params)
        names = resp.json().get("voices", []) if resp.status_code < 400 else []
    except (httpx.HTTPError, ValueError) as e:
        return {"voices": [], **common, "error": f"{type(e).__name__}: {e}"}
    return {"voices": [str(v) for v in names], **common}


@router.post("/speak")
async def speak(body: dict[str, Any] = Body(...)) -> Response:
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text is required")
    cfg = await get_config()
    voice = body.get("voice") or ""
    if not voice and body.get("agentId"):
        async with SessionLocal() as session:
            agent = await session.get(Agent, body["agentId"])
        voice = ((agent.avatar or {}).get("voice") if agent else "") or ""
    voice = voice or cfg.get("defaultVoice") or ""

    def in_browser(reason: str, status_code: int) -> JSONResponse:
        # The page says it with the browser's own voices (if the user allows that).
        return JSONResponse({"browser": True, "voice": voice, "detail": reason},
                            status_code=status_code)

    if voice.startswith(BROWSER_PREFIX):
        return in_browser("this is one of the browser's voices", 409)
    if not await reachable(cfg):
        return in_browser("no speech server reachable", 503)
    payload: dict[str, Any] = {"input": text[:4000], "response_format": "wav"}
    if cfg.get("ttsModel"):
        payload["model"] = cfg["ttsModel"]
    if voice:
        payload["voice"] = voice
    try:
        async with _client(cfg, 180) as client:
            resp = await client.post(f"{_base(cfg)}/v1/audio/speech", json=payload)
    except httpx.HTTPError as e:
        _reach.clear()
        return in_browser(f"speech service unreachable: {e}", 503)
    if resp.status_code >= 400:
        return in_browser(f"speech service error {resp.status_code}: {resp.text[:500]}", 502)
    return Response(content=resp.content,
                    media_type=resp.headers.get("content-type", "audio/wav"))
