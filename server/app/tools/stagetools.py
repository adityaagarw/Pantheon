# ruff: noqa: E501  (the Stage reference is prose for the model)
"""Stage tools: show the user interactive lessons, animations and 3D scenes."""

from __future__ import annotations

import asyncio
import re

from app.browser.manager import BrowserError, manager
from app.core.config import settings
from app.services import sitepack, stage
from app.tools.base import (
    B,
    I,
    S,
    ToolContext,
    ToolError,
    ToolOutput,
    arr,
    obj,
    resolve_in_workspace,
    tool,
)

DOCS = r"""
# The Stage library (a manim-style layer over three.js, with KaTeX)

Your `code` is a JavaScript module; top-level `await` works and everything below is
already in scope (no imports needed). LaTeX inside JS strings needs doubled backslashes
("\\frac{a}{b}") or String.raw`\frac{a}{b}`. Units: 2D mode is ~8 units tall, origin in the
middle, y up. Colors: any CSS color (manim-ish: #58c4dd blue, #fc6255 red, #83c167 green,
#ffff00 yellow, #9a72ac purple).

const s = new Stage({ mode: "2d" | "3d", title: "…", background: "#0f1116" });

Objects (all return a mobject you can animate):
  s.axes({ x: [-5, 5, 1], y: [-3, 3, 1], grid: false, labels: true })
  s.plot(x => Math.sin(x), { x: [-5, 5], color })       s.parametric(t => [x, y, z], { t: [0, 6.28] })
  s.line([[x, y], [x, y], …], { color, width, dashed })  s.arrow(from, to, { color })   s.vector([x, y])
  s.dot([x, y], { color, r })   s.circle(center, r, { color, fill })   s.rect(center, w, h, { fill })
  s.polygon([[x, y], …], { color, fill, fillOpacity })
  s.text("hello", { at: [x, y], size: 1.2, color, anchor: "left" })   s.tex("\\int_0^1 x^2\\,dx", { at, size: 1.6 })
  3D: s.surface((x, y) => z, { x: [-3, 3], y: [-3, 3], wireframe }), s.sphere(at, r), s.box(at, [w, h, d]),
      s.cylinder(at, r, h), s.mesh(anyThreeGeometry, at, color), s.object(anyThreeObject3D)
  s.group(a, b, …)   mob.at([x, y])   mob.setColor(c)   mob.opacity = 0.5   mob.remove()   s.clear()
  Raw three.js is available as THREE (and s.scene, s.camera); s.onFrame = dt => {…} runs every frame.

Animations — `await s.play(anim, anim, …)` runs them together:
  create(mob)  uncreate(mob)  write(label)  fadeIn(mob)  fadeOut(mob)  grow(mob)
  moveTo(mob, [x, y])  shift(mob, [dx, dy])  scaleTo(mob, k)  rotate(mob, angle, "z")  colorTo(mob, c)
  transform(plotA, plotB)   (morphs A into B's shape; B is removed)
  countTo(label, from, to, v => v.toFixed(1))   tween(t => {…}, seconds)
  Each takes an optional duration in seconds as its last argument; `await s.play(a, b, 0.8)`
  sets one duration for the whole play. They also work as methods: s.tween(fn, 1), s.fadeIn(mob).
  await s.wait(1)   await s.cameraTo([x, y, z], [tx, ty, tz])  (3D)

Narration (teach-along):
  await s.say("Watch the point go round.")   (shown as the caption and paced for reading;
      narrated aloud when the viewer supports it; run alongside animations with
      await Promise.all([s.say("…"), s.play(create(circle))]))
  s.step("Part 2: projections")   (marks where the lesson is; you'll see it if the user asks)
  If the user interrupts to ask something, the lesson pauses (animations and narration wait)
  and resumes after you've answered; keep each say() to one or two short sentences.

Narration and interaction (the learner's answers are sent to you as messages):
  s.title("…")   s.caption("Narration with $\\LaTeX$ inline")   await s.next()  (waits for "Next →")
  const choice = await s.ask("Question?", ["A", "B", "C"], { correct: 1 })
  const text = await s.input("Explain in your words:")
  s.slider({ label: "$a$", min: -2, max: 2, value: 1, onChange: v => … })   s.button("Replay", fn)
  pantheon.send("any text", { any: "data" })   (reaches you only while the user has the page open; to
      test something yourself, console.log it and read it in stage_check)
  pantheon.open("Ch 4")   (open another Stage page by id, title or title start: "Ch 4" opens
      "Ch 4 · …", not Ch 41; e.g. a contents page's cards and a lesson's "Back to contents")

Example:
const s = new Stage({ title: "Derivative as a slope" });
const ax = s.axes({ x: [-4, 4], y: [-1, 5] });
const f = s.plot(x => x * x / 2, { x: [-3, 3] });
await s.play(create(ax), create(f));
s.caption("The derivative at a point is the slope of the tangent there.");
const a = s.slider({ label: "$x_0$", min: -2.5, max: 2.5, value: 1, onChange: v => draw(v) });
let tan; function draw(x0) { tan?.remove(); tan = s.line([[x0 - 1.5, x0*x0/2 - 1.5*x0], [x0 + 1.5, x0*x0/2 + 1.5*x0]], { color: "#fc6255" }); }
draw(1);
await s.next();
await s.ask("What is the slope at $x_0 = 2$?", ["1", "2", "4"], { correct: 1 });

# Art and motion libraries (bundled, work offline; import what you need)

The Stage draws on a three.js canvas. For illustration and motion-graphics work, add your own
layers, position: fixed, inset 0, pointer-events none except on what the learner clicks:
- behind the Stage's objects (a designed background, scenery): new Stage({ background:
  "transparent" }) and insert the layer before <canvas class="stage">;
- in front of them (characters, icons, callouts): insert it right after the canvas, and
  keep its own background transparent, or it hides everything the Stage draws.
Captions, questions and Next stay on top of both.

import { gsap, MorphSVGPlugin, SplitText } from "gsap";   // all plugins already registered
  The motion engine: timelines, easing ("expo.out", "back.out(1.6)", "elastic.out(1, .5)"),
  stagger. Plugins: DrawSVG (lines drawing on: gsap.from(path, { drawSVG: 0 })), MorphSVG
  (one shape becoming another: gsap.to(a, { morphSVG: b })), MotionPath (move along a path,
  autoRotate), SplitText (headlines by letter/word), Flip (layout changes animate),
  CustomEase/CustomBounce/CustomWiggle, ScrambleText, Text, Physics2D, Draggable + Inertia
  (drag things), PixiPlugin. A timeline is awaitable: await gsap.timeline().to(...).
  Pause GSAP with the lesson: when the page body gets the class "stage-held" call
  gsap.globalTimeline.pause(), and resume() when it goes.
import { icon, iconNames, peep, peepParts } from "stage/art";
  Ready-made art; no emoji needed. icon("rocket", { size, color, stroke }) gives one of
  ~2,000 Lucide icons as SVG (iconNames("door") lists names). peep({ pose, face, hair,
  facialHair, accessory, stroke, fill, height }) gives an Open Peeps person, hand-drawn.
  { inner: true } returns just the shapes for your own <svg> (icon: 24x24; peep: 850x1200).
  Every part (BW: dark top, light bottom; WB the reverse):
  standing/ BlazerBW BlazerPantsBW BlazerPantsWB BlazerWB CrossedArmsBW CrossedArmsWB EasingBW
    EasingWB PointingFingerBW PointingFingerWB PolkaDots RestingBW RestingWB RoboDanceBW
    RoboDanceWB ShirtBW ShirtPantsBW ShirtPantsWB ShirtWB WalkingBW WalkingFilled WalkingWB Doc
    DocStethoscope
  sitting/ Bike ClosedLegBW ClosedLegWB CrossedLegs HandsBackBW HandsBackWB MediumBW MediumWB
    OneLegUpBW OneLegUpWB WheelChair
  bust/ (head and shoulders) BlazerBlackTee Shirt ButtonShirt Dress Gaming Geek Hoodie
    PointingUp Turtleneck ArmsCrossed Coffee Device DotJacket Explaining Paper PocketShirt
    PoloSweater ShirtCoat Sweater Whatever
  face: Angry Blank Calm Cheeky Concerned Contempt Cute Driven EatingHappy EyesClosed OldAged
    Serious Smile Solemn Suspicious Tired VeryAngry Awe ConcernedFear Explaining Fear Hectic
    LoveGrin Rage SmileBig SmileLol SmileTeeth CalmNM SmileNM
  hair: Afro Bald BaldSides BaldTop Bangs Bear Bun BunCurly Buns FlatTop Long LongAfro
    LongBangs LongCurly Medium MediumBangs MediumLong MediumShort MediumStraight Mohawk Pomp
    ShavedSides Short ShortCurly ShortMessy ShortVolumed ShortWavy BantuKnots Beanie BunFancy
    CornRows GrayBun GrayMedium GrayShort Hijab Turban Twists TwistsVolumed HatHip
  facialHair: Chin Full FullMedium Goatee Handlebars MoustacheThin GrayFull…
  accessory: GlassRound GlassRoundThick GlassAviator SunglassWayfarer Eyepatch…
import * as d3 from "d3"            charts, scales, axes, force graphs, maps, data transitions.
import rough from "roughjs"         a hand-drawn, sketchy look for SVG or canvas shapes.
import * as PIXI from "pixi.js"; import { GlowFilter, AdvancedBloomFilter } from "pixi-filters";
  GPU 2D: thousands of particles, glow, bloom, displacement and blur effects.
import { EffectComposer, RenderPass, EffectPass, BloomEffect, VignetteEffect, NoiseEffect } from "postprocessing";
  Cinematic 3D: bloom, depth of field, vignette, grain and tone mapping. Build an
  EffectComposer(s.renderer) with a RenderPass(s.scene, s.camera) and your EffectPass, and
  assign it to s.composer: the Stage then renders through it every frame.
import { Text } from "troika-three-text"   crisp text inside 3D scenes.
Fonts (already loaded): "Inter" (UI, body), "Fraunces" (display serif), "Space Grotesk"
(display sans).
""".strip()


def stool(name: str, description: str, params: dict):
    return tool(name, description, params, category="stage", timeout=120)


@stool("stage_show", "Show the user something on the Stage (the lesson/visual panel in "
       "Pantheon): an animated, interactive explanation. Pass `code` (a JavaScript module using "
       "the Stage library: manim-style animations on three.js with LaTeX; call stage_docs for "
       "the reference) or `html` (a full page), or `file`: a workspace file holding either "
       "(.html is a page, anything else Stage code), so a fix is an edit_file and a re-show. "
       "`include`: workspace files of shared code (a visual kit, helpers) put before the page's "
       "code, in order; for an .html page they run as module scripts first, so a kit shared "
       "by both should put what it offers on globalThis. Re-show a page to pick up a changed kit. "
       "Same title (or page id) updates the page. Then use stage_check to make sure it renders.",
       obj({"title": S, "code": S, "html": S, "file": S, "include": arr(S), "page": S},
           ["title"]))
async def stage_show(args: dict, ctx: ToolContext) -> str:
    code, html = args.get("code") or "", args.get("html") or ""

    def read(name: str) -> str:
        path = resolve_in_workspace(ctx, name)
        if not path.is_file():
            raise ToolError(f"no file '{name}' in your workspace")
        return path.read_text(encoding="utf-8", errors="replace")

    if args.get("file"):
        source = read(args["file"])
        code, html = ("", source) if args["file"].lower().endswith((".html", ".htm")) \
            else (source, "")
    kit = "\n\n".join(f"// --- {name} ---\n{read(name)}" for name in args.get("include") or [])
    if kit and html:
        safe = re.sub(r"</(script)", r"<\\/\1", kit, flags=re.IGNORECASE)
        script = f'<script type="module">\n{safe}\n</script>'
        html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + script, html, count=1,
                      flags=re.IGNORECASE) if re.search(r"<body[^>]*>", html, re.IGNORECASE) \
            else script + html
    elif kit:
        code = f"{kit}\n\n// --- page ---\n{code}"
    try:
        p = await stage.save(ctx.org.id, ctx.agent.id, args["title"], code=code,
                             html=html, page_id=args.get("page") or None)
    except stage.StageError as e:
        raise ToolError(str(e)) from None
    return (f"'{p.title}' is on the Stage (page {p.id}, version {p.version}). The user's answers "
            "and clicks will arrive as messages. Run stage_check to confirm it renders.")


@stool("stage_docs", "The Stage library reference with an example.", obj({}))
async def stage_docs(args: dict, ctx: ToolContext) -> str:
    return DOCS


@stool("stage_list", "Pages on this organization's Stage.", obj({}))
async def stage_list(args: dict, ctx: ToolContext) -> str:
    rows = await stage.for_org(ctx.org.id)
    live = [p for p in rows if p.folder != stage.ARCHIVE]
    out = "\n".join(f"- {p.id} '{p.title}' ({p.kind}, v{p.version})" for p in live) or \
        "The Stage is empty."
    if archived := len(rows) - len(live):
        out += (f"\n({archived} older pages are in the archive; re-showing a title makes "
                "a new page rather than touching them.)")
    return out


@stool("stage_archive", "Put Stage pages away in the archive, such as drafts, test pages and "
       "superseded copies, so the user's Stage list shows only the real pages. Nothing is "
       "deleted: an archived page keeps its source and can be restored (`restore: true`). "
       "`pages`: ids or exact titles.", obj({"pages": arr(S), "restore": B}, ["pages"]))
async def stage_archive(args: dict, ctx: ToolContext) -> str:
    restore = bool(args.get("restore"))
    rows = [p for p in await stage.for_org(ctx.org.id) if (p.folder == stage.ARCHIVE) == restore]
    done, missing = [], []
    for ref in args.get("pages") or []:
        ref = str(ref).strip()
        p = next((p for p in rows if p.id == ref), None) or \
            next((p for p in rows if p.title.strip().lower() == ref.lower()), None)
        if p is None:
            missing.append(ref)
            continue
        await stage.move(p.id, "" if restore else stage.ARCHIVE)
        rows.remove(p)
        done.append(f"- {p.id} '{p.title}'")
    if not done:
        raise ToolError(f"no {'archived' if restore else 'live'} page matches "
                        f"{', '.join(repr(m) for m in missing)} (use an id or exact title; "
                        "stage_list shows them)")
    out = f"{'Restored' if restore else 'Archived'} {len(done)}:\n" + "\n".join(done)
    if missing:
        out += f"\nNot found: {', '.join(repr(m) for m in missing)}"
    return out


@stool("stage_package", "Package Stage pages as one website the user can open from a link "
       "and host anywhere (a folder of static files, also as a zip). `pages`: ids or titles in "
       "reading order (a title start like \"Ch 4\" works; default every page); `index`: the "
       "home page (default the first). pantheon.open links between the pages keep working. "
       "Packaging again under the same name replaces the site.",
       obj({"name": S, "pages": arr(S), "index": S}, ["name"]))
async def stage_package(args: dict, ctx: ToolContext) -> str:
    try:
        site = await sitepack.package(ctx.org.id, args["name"], args.get("pages") or [],
                                      args.get("index") or "")
    except sitepack.SiteError as e:
        raise ToolError(str(e)) from None
    pages = "\n".join(f"- {p['file']}: {p['title']}" for p in site["pages"])
    return (f"Packaged {len(site['pages'])} pages.\nOpen: {site['url']}\nDownload: {site['zip']}"
            f"\n{pages}")


MAX_FRAMES = 12
# What a learner would press next: "Next →", or the first choice of a question.
CLICK_ON = """() => {
  const b = document.querySelector('.stage-next') ||
            document.querySelector('.stage-choices button:not([disabled])');
  if (!b) return '';
  const label = b.textContent.trim().slice(0, 40);
  b.click();
  return label;
}"""


@stool("stage_check", "Open a Stage page in a browser and report errors, console.log output "
       "and screenshots, so you can fix problems before the user sees them (to test what a "
       "library returns, console.log it here; pantheon.send only reaches you when the user has "
       "the page open). By default one screenshot after 3 s. "
       "`at`: seconds to take screenshots at, e.g. [0.5, 1, 2, 4] to see an animation as it "
       "moves. `walk`: go through the lesson like a learner (a screenshot every `every` "
       "seconds, default 3, then press Next or the first answer), for up to `frames` "
       f"screenshots (at most {MAX_FRAMES}).",
       obj({"page": S, "at": arr({"type": "number"}), "walk": B, "frames": I,
            "every": {"type": "number"}}, ["page"]))
async def stage_check(args: dict, ctx: ToolContext) -> ToolOutput:
    p = await stage.get(args["page"])
    if p is None or p.org_id != ctx.org.id:
        raise ToolError(f"no stage page {args['page']}")
    walk = bool(args.get("walk"))
    if walk:
        every = min(max(float(args.get("every") or 3), 0.5), 20)
        n = min(max(int(args.get("frames") or 8), 1), MAX_FRAMES)
    else:
        times = sorted({min(max(float(t), 0), 60) for t in (args.get("at") or [3])})[:MAX_FRAMES]
    key = f"stagecheck:{ctx.agent.id}"
    shots: list[str] = []
    notes: list[str] = []
    try:
        s = await manager.session(key)
        s.console.clear()
        s.log.clear()
        url = f"{settings.stage_base_url.rstrip('/')}/api/v1/stage/{p.id}/view?v={p.version}"
        await s.page.goto(url, wait_until="load", timeout=30_000)
        if walk:
            for i in range(n):
                await asyncio.sleep(every)
                s.shot = await s.page.screenshot(type="jpeg", quality=65)
                shots.append(manager.data_url(s))
                pressed = await s.page.evaluate(CLICK_ON)
                notes.append(f"{i + 1}: {(i + 1) * every:g}s" + (f", then pressed '{pressed}'"
                                                                  if pressed else ""))
        else:
            elapsed = 0.0
            for t in times:
                await asyncio.sleep(t - elapsed)
                elapsed = t
                s.shot = await s.page.screenshot(type="jpeg", quality=65)
                shots.append(manager.data_url(s))
                notes.append(f"{len(shots)}: {t:g}s")
        overlay = await s.page.evaluate(
            "() => document.getElementById('stage-error')?.textContent || ''")
    except BrowserError as e:
        raise ToolError(str(e)) from None
    except Exception as e:  # noqa: BLE001
        await manager.close(key)
        raise ToolError(f"couldn't load the page: {str(e).splitlines()[0]}") from None
    problems = list(dict.fromkeys([*s.console, *([overlay] if overlay else [])]))
    await manager.close(key)
    verdict = ("No errors." if not problems else
               "Problems:\n" + "\n".join(f"- {x}" for x in problems[:20]))
    frames = "" if len(shots) == 1 and not walk else "\nScreenshots, in order: " + "; ".join(notes)
    when = f"after {times[0]:g} seconds" if len(shots) == 1 and not walk else "in frames"
    log = "\nConsole:\n" + "\n".join(s.log)[:6000] if s.log else ""
    return ToolOutput(f"Checked '{p.title}' v{p.version} {when}. {verdict}{frames}{log}", shots)
