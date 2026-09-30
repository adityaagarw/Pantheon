"""Render the Pantheon explainer to MP4.

    python render.py                 # -> pantheon-explainer.mp4 (+ .srt captions)
    python render.py --voice         # re-synthesize the voiceover first (needs Pantheon + audio.cpp)
    python render.py --no-captions   # without burned-in captions

Needs: playwright (+ chromium), ffmpeg/ffprobe on PATH. Edit script.json to change the
narration or voice, index.html to change the visuals (open it in a browser to preview;
add ?still=12.5 to freeze on a moment).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
FPS = 30


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def synthesize(script: dict, api: str) -> None:
    (HERE / "vo").mkdir(exist_ok=True)
    for sc in script["scenes"]:
        req = urllib.request.Request(
            f"{api}/api/v1/voice/speak", method="POST",
            data=json.dumps({"text": sc["vo"], "voice": script["voice"]}).encode(),
            headers={"Content-Type": "application/json"})
        (HERE / "vo" / f"{sc['id']}.wav").write_bytes(urllib.request.urlopen(req, timeout=300).read())
        print("voice", sc["id"])


def srt_time(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", action="store_true", help="re-synthesize the voiceover")
    ap.add_argument("--api", default="http://localhost:8710")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--out", default="pantheon-explainer.mp4")
    args = ap.parse_args()

    script = json.loads((HERE / "script.json").read_text(encoding="utf-8"))
    if args.voice:
        synthesize(script, args.api)
    scenes = [[sc["id"], duration(HERE / "vo" / f"{sc['id']}.wav"), sc["vo"]] for sc in script["scenes"]]

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto((HERE / "index.html").as_uri())
        await page.evaluate("document.fonts.ready")
        await page.wait_for_function("[...document.images].every(i => i.complete)")
        await page.evaluate("(s) => window.__setup({scenes: s})", scenes)
        if script.get("url"):
            await page.evaluate("(u) => document.getElementById('cta-url').querySelector('.w').textContent = u",
                                script["url"])
        await page.evaluate(f"window.__captions({'false' if args.no_captions else 'true'})")
        total = await page.evaluate("window.__duration")
        plan = await page.evaluate("window.__plan")
        frames = int(total * FPS)

        # Voice clips laid on the timeline, then loudness-normalized.
        inputs, delays = [], []
        for i, sc in enumerate(plan):
            inputs += ["-i", str(HERE / "vo" / f"{sc['id']}.wav")]
            ms = int(sc["start"] * 1000)
            delays.append(f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]")
        mix = ";".join(delays) + ";" + "".join(f"[a{i}]" for i in range(len(plan))) + \
            f"amix=inputs={len(plan)}:normalize=0,apad,atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        out = HERE / args.out
        ff = subprocess.Popen(  # noqa: ASYNC220 - offline render tool
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS),
             "-c:v", "mjpeg", "-i", "-", *inputs, "-filter_complex", mix,
             "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryslow", "-crf", "26", "-tune", "animation",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", "-shortest", str(out)],
            stdin=subprocess.PIPE)
        for f in range(frames):
            await page.evaluate(f"window.__render({f / FPS})")
            ff.stdin.write(await page.screenshot(type="jpeg", quality=95))
            if f % (FPS * 5) == 0:
                print(f"frame {f}/{frames}")
        ff.stdin.close()
        ff.wait()
        await browser.close()

    srt = [f"{i + 1}\n{srt_time(sc['start'])} --> {srt_time(sc['start'] + scenes[i][1])}\n{scenes[i][2]}\n"
           for i, sc in enumerate(plan)]
    out.with_suffix(".srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"wrote {out} ({total:.1f}s)")


asyncio.run(main())
