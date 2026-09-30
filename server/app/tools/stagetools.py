# ruff: noqa: E501  (the Stage reference is prose for the model)
"""Stage tools: show the user interactive lessons, animations and 3D scenes."""

from __future__ import annotations

import asyncio

from app.browser.manager import BrowserError, manager
from app.core.config import settings
from app.services import stage
from app.tools.base import S, ToolContext, ToolError, ToolOutput, obj, tool

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
  Each takes an optional duration in seconds as its last argument.
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
  pantheon.send("any text", { any: "data" })   (message yourself from the page)

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
""".strip()


def stool(name: str, description: str, params: dict):
    return tool(name, description, params, category="stage", timeout=120)


@stool("stage_show", "Show the user something on the Stage (the lesson/visual panel in "
       "Pantheon): an animated, interactive explanation. Pass `code` (a JavaScript module using "
       "the Stage library: manim-style animations on three.js with LaTeX; call stage_docs for "
       "the reference) or `html` (a full page). Same title (or page id) updates the page. Then "
       "use stage_check to make sure it renders.",
       obj({"title": S, "code": S, "html": S, "page": S}, ["title"]))
async def stage_show(args: dict, ctx: ToolContext) -> str:
    try:
        p = await stage.save(ctx.org.id, ctx.agent.id, args["title"], code=args.get("code") or "",
                             html=args.get("html") or "", page_id=args.get("page") or None)
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
    return "\n".join(f"- {p.id} '{p.title}' ({p.kind}, v{p.version})" for p in rows) or \
        "The Stage is empty."


@stool("stage_check", "Open a Stage page in a browser, let it run a few seconds, and report "
       "errors plus a screenshot, so you can fix problems before the user sees them.",
       obj({"page": S}, ["page"]))
async def stage_check(args: dict, ctx: ToolContext) -> ToolOutput:
    p = await stage.get(args["page"])
    if p is None or p.org_id != ctx.org.id:
        raise ToolError(f"no stage page {args['page']}")
    key = f"stagecheck:{ctx.agent.id}"
    try:
        s = await manager.session(key)
        s.console.clear()
        url = f"{settings.stage_base_url.rstrip('/')}/api/v1/stage/{p.id}/view?v={p.version}"
        await s.page.goto(url, wait_until="load", timeout=30_000)
        await asyncio.sleep(3)
        s.shot = await s.page.screenshot(type="jpeg", quality=65)
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
    return ToolOutput(f"Checked '{p.title}' v{p.version} after 3 seconds. {verdict}",
                      [manager.data_url(s)])
