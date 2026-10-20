# ruff: noqa: E501  (the Stage reference is prose for the model)
"""Stage tools: show the user interactive lessons, animations and 3D scenes."""

from __future__ import annotations

import asyncio
import re

from app import packs
from app.browser.manager import BrowserError, manager
from app.core.config import settings
from app.services import preview, sitepack, stage
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

# Art (built in) and libraries (yours are listed at the end; they work offline)

The Stage draws on a three.js canvas. For illustration and motion-graphics work, add your own
layers, position: fixed, inset 0, pointer-events none except on what the learner clicks:
- behind the Stage's objects (a designed background, scenery): new Stage({ background:
  "transparent" }) and insert the layer before <canvas class="stage">;
- in front of them (characters, icons, callouts): insert it right after the canvas, and
  keep its own background transparent, or it hides everything the Stage draws.
Captions, questions and Next stay on top of both.

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
Fonts (already loaded): "Inter" (UI, body), "Fraunces" (display serif), "Space Grotesk"
(display sans).
""".strip()


def docs_for(libs: list[packs.Library]) -> str:
    """The reference with the libraries this agent's pages can import, and nothing else:
    importing any other name fails."""
    extra = [lib for lib in libs if not lib.core]
    if not extra:
        return DOCS + ("\n\nNo other libraries are available to you (only the Stage, three.js "
                       "and KaTeX). Zeus can add any npm library and give it to you (ask_zeus).")
    parts = [DOCS, "", "# Your libraries (import only these; any other import fails. Zeus can "
             "add any npm library and give it to you: ask_zeus)"]
    for lib in extra:
        names = ", ".join(f'"{n}"' for n in lib.imports)
        parts += ["", f"## {lib.name} ({names})", lib.docs or lib.summary]
    return "\n".join(parts)


def _check_page(ref: str) -> bool:
    """The acceptance checker's own test pages (.acceptance/...) run on the Stage but aren't
    the work: they go straight to the archive, so the user's list shows only real pages."""
    return re.match(r"(\./)?\.acceptance/", ref.strip().replace("\\", "/")) is not None


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
    check = any(_check_page(str(args.get(k) or "")) for k in ("file", "title"))
    try:
        p = await stage.save(ctx.org.id, ctx.agent.id, args["title"], code=code,
                             html=html, page_id=args.get("page") or None,
                             folder=stage.ARCHIVE if check else "")
    except stage.StageError as e:
        raise ToolError(str(e)) from None
    return (f"'{p.title}' is on the Stage (page {p.id}, version {p.version}). The user's answers "
            "and clicks will arrive as messages. Run stage_check to confirm it renders.")


@stool("stage_docs", "The Stage library reference with an example.", obj({}))
async def stage_docs(args: dict, ctx: ToolContext) -> str:
    return docs_for(await stage.libraries_for(ctx.org.id, ctx.agent.id))


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
TEXT_CAP = 8000  # characters of a page's visible text (`text`)
# What a learner would press next: "Next →", or the first choice of a question.
CLICK_ON = """() => {
  const b = document.querySelector('.stage-next') ||
            document.querySelector('.stage-choices button:not([disabled])');
  if (!b) return '';
  const label = b.textContent.trim().slice(0, 40);
  b.click();
  return label;
}"""


# What a screenshot can't show reliably: sideways scrolling (and what sticks out) and
# controls too small to tap.
MEASURE = """() => {
  const out = [];
  const vw = window.innerWidth;
  const over = document.documentElement.scrollWidth - vw;
  const name = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') +
    (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
  if (over > 0) {
    const shown = e => { for (let p = e; p; p = p.parentElement) { const s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden') return false;
      if (p !== e && /(auto|scroll|hidden|clip)/.test(s.overflowX)) return false; } return true; };
    const wide = [...document.querySelectorAll('body *')].filter(e => {
      const r = e.getBoundingClientRect(); return r.width > 0 && r.right > vw + 1 && shown(e); });
    const outer = wide.filter(e => !wide.includes(e.parentElement)).slice(0, 3);
    out.push(`${over}px wider than the window (sideways scroll)` + (outer.length ? ': ' + outer.map(e =>
      `${name(e)} reaches ${Math.round(e.getBoundingClientRect().right)}px`).join(', ') : ''));
  }
  const small = [...document.querySelectorAll('a[href],button,input,select,textarea,[role=button]')]
    .filter(e => { const r = e.getBoundingClientRect();
                   return r.width > 0 && r.height > 0 && (r.width < 44 || r.height < 44)
                     && getComputedStyle(e).visibility !== 'hidden'; });
  if (small.length) out.push(`${small.length} controls under 44px (e.g. ` + small.slice(0, 3)
    .map(e => `${e.tagName.toLowerCase()} "${(e.textContent || e.getAttribute('aria-label') || '')
    .trim().slice(0, 24)}"`).join(', ') + ')');
  return out;
}"""


# What the brief's quality floor asks and a screenshot hides: text contrast (WCAG: 4.5:1,
# 3:1 for large text), form fields without a label, images without alt text.
AUDIT = """() => {
  const out = [];
  const rgb = c => { const m = c.match(/[\\d.]+/g); return m ? m.map(Number) : null; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bg = e => { for (let p = e; p; p = p.parentElement) { const s = getComputedStyle(p);
      if (s.backgroundImage !== 'none') return null;
      const c = rgb(s.backgroundColor); if (c && (c.length < 4 || c[3] > 0.9)) return c; }
    return [255, 255, 255]; };
  const low = [];
  for (const e of document.querySelectorAll('body *')) {
    if (![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const s = getComputedStyle(e), r = e.getBoundingClientRect();
    if (!r.width || !r.height || s.visibility === 'hidden' || +s.opacity === 0) continue;
    const fg = rgb(s.color), back = bg(e);
    if (!fg || !back || (fg.length > 3 && fg[3] < 0.5)) continue;
    const a = lum(fg), b = lum(back), ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const size = parseFloat(s.fontSize), large = size >= 24 || (size >= 18.6 && +s.fontWeight >= 700);
    if (ratio < (large ? 3 : 4.5)) low.push(`"${e.textContent.trim().slice(0, 30)}" ${ratio.toFixed(2)}:1 (${s.color} on rgb(${back.slice(0, 3).join(', ')}))`);
  }
  if (low.length) out.push(`${low.length} texts below contrast 4.5:1 (3:1 large), e.g. ` + low.slice(0, 3).join('; '));
  const unlabeled = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea')]
    .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby') && !e.title);
  if (unlabeled.length) out.push(`${unlabeled.length} form fields without a label (e.g. ${unlabeled.slice(0, 3).map(e => e.name || e.id || e.type).join(', ')})`);
  const noAlt = [...document.images].filter(i => !i.hasAttribute('alt'));
  if (noAlt.length) out.push(`${noAlt.length} images without alt text`);
  return out;
}"""

# Links that go nowhere: #anchors with no target, and relative links (checked against the
# workspace when the page is a workspace file).
LINKS = """() => {
  const missing = [], files = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.getAttribute('href').trim();
    if (!h || h === '#' || /^(https?:|mailto:|tel:|javascript:|data:)/i.test(h)) continue;
    if (h.startsWith('#')) { if (!document.getElementById(decodeURIComponent(h.slice(1)))) missing.push(h); }
    else files.push(h.split('#')[0].split('?')[0]);
  }
  return {missing: [...new Set(missing)], files: [...new Set(files)]};
}"""

# Click a control by its visible text (or a CSS selector): one step of a flow. A control
# answers to any of its names: the text as shown ("Mon 19" from <span>Mon</span><span>19</span>),
# its aria-label, title or value. Visible controls win. Not found: the controls there are.
CLICK = """(what) => {
  let el = null;
  try { el = document.querySelector(what); } catch (e) {}
  const all = [...document.querySelectorAll('a,button,[role=button],[role=tab],[role=option],[role=link],[role=menuitem],input[type=submit],input[type=button],input[type=checkbox],input[type=radio],label,summary,[onclick],[tabindex]')];
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const names = e => [e.innerText, e.textContent, e.getAttribute('aria-label'), e.getAttribute('title'), e.value].map(norm).filter(Boolean);
  const shown = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  if (!el) {
    const want = norm(what);
    const pool = [...all.filter(shown), ...all.filter(e => !shown(e))];
    el = pool.find(e => names(e).includes(want))
      || pool.find(e => names(e).some(n => n.startsWith(want)))
      || pool.find(e => names(e).some(n => n.includes(want)))
      || pool.find(e => names(e).some(n => n.replace(/ /g, '').startsWith(want.replace(/ /g, ''))));
  }
  if (!el) return [...new Set(all.filter(shown).map(e => names(e)[0]).filter(n => n && n.length < 60))].slice(0, 15);
  el.scrollIntoView({block: 'center'});
  el.click();
  return true;
}"""


async def find_page(ctx: ToolContext, ref: str) -> stage.StagePage:
    """A page by id, title, the start of its title, or a workspace .html file (put on the
    Stage as it is now, updating the page of that name), so an agent never looks an id up."""
    ref = ref.strip()
    p = await stage.get(ref)
    if p is not None and p.org_id == ctx.org.id:
        return p
    live = [p for p in await stage.for_org(ctx.org.id) if p.folder != stage.ARCHIVE]
    key = stage.title_key(ref)
    found = None
    for test in (lambda t: stage.title_key(t) == key,
                 lambda t: stage.title_key(t).startswith(key),
                 lambda t: key in stage.title_key(t)):
        hits = [p for p in live if key and test(p.title)]
        if len(hits) == 1:
            found = hits[0]
            break
        if len(hits) > 1:
            raise ToolError(f"'{ref}' matches {len(hits)} pages: "
                            + "; ".join(f"{p.id} '{p.title}'" for p in hits[:6]))
    path = resolve_in_workspace(ctx, ref) if ref.lower().endswith((".html", ".htm")) else None
    if path is not None and path.is_file():
        try:
            return await stage.save(ctx.org.id, ctx.agent.id, found.title if found else ref,
                                    html=path.read_text(encoding="utf-8", errors="replace"),
                                    page_id=found.id if found else None,
                                    folder=stage.ARCHIVE if _check_page(ref) else "")
        except stage.StageError as e:
            raise ToolError(str(e)) from None
    if found is not None:
        return found
    raise ToolError(f"no stage page '{ref}' (use its id or title from stage_list, or a "
                    "workspace .html file)")


@stool("stage_check", "Open a Stage page in a browser and report errors, console.log output "
       "and screenshots, so you can fix problems before the user sees them (to test what a "
       "library returns, console.log it here; pantheon.send only reaches you when the user has "
       "the page open). By default one screenshot after 3 s. "
       "`at`: seconds to take screenshots at, e.g. [0.5, 1, 2, 4] to see an animation as it "
       "moves. `walk`: go through the lesson like a learner (a screenshot every `every` "
       "seconds, default 3, then press Next or the first answer), for up to `frames` "
       f"screenshots (at most {MAX_FRAMES}). `widths`: also screenshot the whole page at these "
       "window widths, e.g. [390, 768, 1440] for phone, tablet and desktop; each width also "
       "reports sideways overflow (and the elements sticking out) and controls smaller than "
       "44 px. `page`: an id, a title, or a workspace .html file, which opens from the "
       "workspace as it is now, like a real site: its links, stylesheets, scripts and images "
       "work and localStorage carries over to the pages it leads to. It may end in ?query or "
       "#fragment (a state the page reads). `clicks`: go through a flow, clicking each control "
       "in turn (its visible text, or a CSS selector), with a screenshot after each; a link "
       "goes on to its page, so one check can follow a path across pages. `text`: also return "
       "the page's visible text (after the clicks), to compare what pages say without reading "
       "their source. Every check also reports console errors, requests that failed (missing "
       "images, scripts, styles), links that go nowhere, and for a page: text below 4.5:1 "
       "contrast, form fields without labels and images without alt text, in one list of "
       "problems. Several checks can run at once. Use this rather than writing your own "
       "screenshot, server or test scripts.",
       obj({"page": S, "at": arr({"type": "number"}), "walk": B, "frames": I,
            "every": {"type": "number"}, "widths": arr({"type": "number"}),
            "clicks": arr(S), "text": B}, ["page"]))
async def stage_check(args: dict, ctx: ToolContext) -> ToolOutput:
    # "page?query#state" opens the page with that query and fragment (a state it reads).
    ref, _, fragment = str(args["page"]).strip().partition("#")
    file_ref, _, query = ref.partition("?")
    source = None  # the workspace file, when the page is one: opened from the workspace
    if file_ref.lower().endswith((".html", ".htm")):
        source = resolve_in_workspace(ctx, file_ref)
        if not source.is_file():
            source = None
    # A workspace file's Stage copy follows the file (a new version only when it changed).
    p = await find_page(ctx, file_ref if source is not None else ref)
    walk = bool(args.get("walk"))
    if walk:
        every = min(max(float(args.get("every") or 3), 0.5), 20)
        n = min(max(int(args.get("frames") or 8), 1), MAX_FRAMES)
    else:
        times = sorted({min(max(float(t), 0), 60) for t in (args.get("at") or [3])})[:MAX_FRAMES]
    # One browser per check: checks run in parallel, and a shared tab aborted the other's load.
    key = f"stagecheck:{ctx.agent.id}:{ctx.tool_call_id or id(args)}"
    shots: list[str] = []
    notes: list[str] = []
    found: list[str] = []  # problems beyond the console: failed requests, dead links, layout
    try:
        s = await manager.session(key)
        s.console.clear()
        s.log.clear()

        def failed(request) -> None:
            if not request.url.startswith("data:"):
                found.append(f"request failed: {request.url[-120:]} ({request.failure or 'error'})")

        def status(response) -> None:
            if response.status >= 400 and "/api/v1/stage/" not in response.url \
                    and not response.url.endswith("/favicon.ico"):
                found.append(f"HTTP {response.status}: {response.url[-120:]}")

        s.page.on("requestfailed", failed)
        s.page.on("response", status)
        tail = (f"?{query}" if source is not None and query else "") + \
            (f"#{fragment}" if fragment else "")
        if source is not None:  # from the workspace, as a site: links, assets, localStorage
            url = await preview.url_for(ctx.workspace, str(source.relative_to(ctx.workspace)))
        else:
            url = f"{settings.stage_base_url.rstrip('/')}/api/v1/stage/{p.id}/view?v={p.version}"
        await s.page.goto(url + tail, wait_until="load", timeout=30_000)
        links = await s.page.evaluate(LINKS)
        found.extend(f"link to a missing anchor: {h}" for h in links["missing"])
        if source is not None:
            found.extend(f"link to a missing file: {h}" for h in links["files"]
                         if not (source.parent / h).exists())
        if p.kind == "html":
            found.extend(await s.page.evaluate(AUDIT))
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
            # The same page at other widths, whole (responsive layouts).
            for w in sorted({min(max(int(w), 320), 2560) for w in args.get("widths") or []})[:4]:
                await s.page.set_viewport_size({"width": w, "height": 844 if w < 600 else 900})
                await asyncio.sleep(0.6)
                s.shot = await s.page.screenshot(type="jpeg", quality=60, full_page=True)
                shots.append(manager.data_url(s))
                fit = await s.page.evaluate(MEASURE)
                notes.append(f"{len(shots)}: whole page at {w}px wide" + "".join(
                    f", {x}" for x in fit))
                found.extend(f"at {w}px: {x}" for x in fit)
            # A flow: click through it, a screenshot after each step. A link goes on to its
            # page (same site, same localStorage), so a path can cross pages.
            for what in [str(c) for c in args.get("clicks") or []][:MAX_FRAMES]:
                before = s.page.url
                try:
                    ok = await s.page.evaluate(CLICK, what)
                except Exception as e:  # noqa: BLE001 - the click navigated mid-evaluate
                    ok = "context" in str(e).lower() or "navigat" in str(e).lower()
                there = ok if isinstance(ok, list) else []
                ok = ok is True
                await asyncio.sleep(0.8)
                try:
                    await s.page.wait_for_load_state("load", timeout=10_000)
                except Exception:  # noqa: BLE001 - still loading: screenshot what is there
                    pass
                s.shot = await s.page.screenshot(type="jpeg", quality=65)
                shots.append(manager.data_url(s))
                moved = s.page.url.split("#")[0] != before.split("#")[0]
                where = f" (now on {s.page.url.split('/')[-1] or 'the index'})" if moved else ""
                notes.append(f"{len(shots)}: after clicking '{what}'{where}"
                             + ("" if ok else " (not found)"))
                if not ok:
                    found.append(f"nothing to click for '{what}'" + (
                        f" on {s.page.url.split('?')[0].split('/')[-1] or 'the page'}; its "
                        "controls: " + ", ".join(f"'{n}'" for n in there) if there else ""))
        overlay = await s.page.evaluate(
            "() => document.getElementById('stage-error')?.textContent || ''")
        text = ""
        if args.get("text"):
            text = await s.page.evaluate(
                "() => (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n')")
    except BrowserError as e:
        raise ToolError(str(e)) from None
    except Exception as e:  # noqa: BLE001
        await manager.close(key)
        raise ToolError(f"couldn't load the page: {str(e).splitlines()[0]}") from None
    problems = list(dict.fromkeys([*s.console, *([overlay] if overlay else []), *found]))
    await manager.close(key)
    verdict = ("No errors." if not problems else
               "Problems:\n" + "\n".join(f"- {x}" for x in problems[:20]))
    single = len(shots) == 1 and not walk and not args.get("widths") and not args.get("clicks")
    frames = "" if single else "\nScreenshots, in order: " + "; ".join(notes)
    when = f"after {times[0]:g} seconds" if len(shots) == 1 and not walk else "in frames"
    log = "\nConsole:\n" + "\n".join(s.log)[:6000] if s.log else ""
    shown = f"\nVisible text:\n{text.strip()[:TEXT_CAP]}" if text.strip() else ""
    return ToolOutput(f"Checked '{p.title}' v{p.version} {when}. {verdict}{frames}{log}{shown}",
                      shots)
