"""Narration for packaged Stage sites: every line a lesson says, rendered once as audio.

A lesson narrates with ``s.say(text)``; on the Stage, Pantheon speaks each line as it comes.
A packaged site has no Pantheon behind it, so the lines are rendered when the site is
packaged, in one voice, and the site plays the files (``narration.js`` maps text to file).

The text of a line is often built at run time (``s.say(step.note)``), so the lines aren't
read from the ``say`` calls alone: every string in the page's code that reads like a
sentence is rendered. A line the site has no file for falls back to the browser's voice.

A voice is ``data/voices/<name>.json``: ``{"model", "ref", "reference_text", "seed"}``,
with ``ref`` a WAV clip next to it that every line is cloned from, so the voice stays the
same across chapters. Rendered lines are cached by voice and text, so packaging again
only renders what changed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import lameenc

from app.core.config import settings

log = logging.getLogger(__name__)
SAY = re.compile(r"\.say\(\s*$")


class NarrationError(ValueError):
    pass


def voices_dir() -> Path:
    return settings.data_path / "voices"


def voices() -> list[str]:
    return sorted(p.stem for p in voices_dir().glob("*.json"))


def load_voice(name: str) -> dict:
    path = voices_dir() / f"{name}.json"
    if not re.fullmatch(r"[\w-]+", name or "") or not path.is_file():
        raise NarrationError(f"no voice '{name}' (voices: {', '.join(voices()) or 'none'})")
    v = json.loads(path.read_text(encoding="utf-8"))
    ref = voices_dir() / v.get("ref", "")
    if not v.get("model") or not ref.is_file():
        raise NarrationError(f"voice '{name}' needs a model and a ref clip next to it")
    return {**v, "name": name, "refData": base64.b64encode(ref.read_bytes()).decode()}


def strings(src: str) -> list[tuple[str, bool]]:
    """Every string literal in JS source, and whether it is the first argument of .say(.
    Template strings with ${...} are skipped: their text only exists at run time."""
    out: list[tuple[str, bool]] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
        elif c == "/" and src.startswith("/*", i):
            i = src.find("*/", i + 2)
            i = n if i < 0 else i + 2
        elif c in "\"'`":
            j, buf, dynamic = i + 1, [], False
            while j < n and src[j] != c:
                if src[j] == "\\" and j + 1 < n:
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if c != "`" and src[j] == "\n":
                    break
                if c == "`" and src.startswith("${", j):
                    dynamic = True
                buf.append(src[j])
                j += 1
            if not dynamic:
                text = "".join(buf)
                try:
                    text = json.loads('"' + text.replace('"', '\\"') + '"') if "\\" in text else text
                except ValueError:
                    pass
                out.append((text, bool(SAY.search(src[max(0, i - 40):i]))))
            i = j + 1
        else:
            i += 1
    return out


def spoken(text: str) -> str:
    """What the voice reads: inline $tex$ as its letters, no markup."""
    text = re.sub(r"\$([^$]*)\$", lambda m: re.sub(r"\\[a-zA-Z]+|[{}^_]", " ", m.group(1)), text)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def lines(src: str) -> list[str]:
    """The lines a page may say: its say() strings, and every full sentence elsewhere in
    its code (seven words or more, ending like a sentence; no code, markup or URL), which
    is how a line passed to say() in a variable is written. Labels and answer choices are
    shorter and are left out."""
    seen: dict[str, None] = {}
    for text, said in strings(src):
        t = text.strip()
        if not t or t in seen:
            continue
        sentence = len(t.split()) >= 7 and re.search(r"[.!?…\"”)]$", t) and \
            not re.search(r"[<>{};=]|=>|https?:|\b(const|function|return)\b", t)
        if said or sentence:
            seen[t] = None
    return list(seen)


def key(voice: dict, text: str) -> str:
    raw = json.dumps([voice["model"], voice.get("reference_text", ""), voice.get("seed"),
                      voice["refData"][-64:], spoken(text)])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _mp3(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, channels, pcm = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(rate)
    enc.set_channels(channels)
    enc.set_quality(2)
    return enc.encode(pcm) + enc.flush()


async def _render(client: httpx.AsyncClient, base: str, voice: dict, text: str) -> bytes:
    body = {"model": voice["model"], "input": spoken(text),
            "voice_ref": {"type": "base64", "data": voice["refData"]}}
    if voice.get("reference_text"):
        body["reference_text"] = voice["reference_text"]
    if voice.get("seed") is not None:
        body["seed"] = voice["seed"]
    for attempt in range(3):
        try:
            r = await client.post(f"{base}/v1/audio/speech", json=body)
            r.raise_for_status()
            return _mp3(r.content)
        except (httpx.HTTPError, wave.Error, EOFError) as e:
            if attempt == 2:
                raise NarrationError(f"the speech server failed on “{text[:60]}”: {e}") from e
            await asyncio.sleep(5 * (attempt + 1))
    raise AssertionError


async def narrate(texts: list[str], voice_name: str, out_dir: Path,
                  progress: Callable[[int, int], Awaitable[None]] | None = None) -> dict:
    """Render each line (or take it from the cache) into ``out_dir``. Returns
    ``{"files": {text: file}, "rendered": n, "failed": [text...]}``."""
    from app.api.voice import _base, _client, get_config

    voice = load_voice(voice_name)
    cfg = await get_config()
    base = _base(cfg)
    if not base:
        raise NarrationError("no speech server is set up (Settings → Voice)")
    cache = settings.data_path / "voice-cache" / voice_name
    cache.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    rendered, failed = 0, []
    async with _client(cfg, timeout=600) as client:
        for i, text in enumerate(texts):
            if not spoken(text):
                continue
            name = key(voice, text) + ".mp3"
            hit = cache / name
            if not hit.is_file():
                try:
                    hit.write_bytes(await _render(client, base, voice, text))
                    rendered += 1
                except NarrationError as e:
                    log.warning("%s", e)
                    failed.append(text)
                    continue
            (out_dir / name).write_bytes(hit.read_bytes())
            files[text] = name
            if progress:
                await progress(i + 1, len(texts))
    return {"files": files, "rendered": rendered, "failed": failed}
