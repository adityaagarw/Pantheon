// Pantheon Stage: a small manim-style animation + interaction library on three.js.
//
//   const s = new Stage();                       // 2D (math units, y up, ~8 units tall)
//   const ax = s.axes({ x: [-4, 4], y: [-2, 2] });
//   const sine = s.plot(Math.sin, { x: [-4, 4], color: "#58c4dd" });
//   const eq = s.tex("y = \\sin x", { at: [2.4, 1.6] });
//   s.caption("A sine wave repeats every 2π.");
//   await s.play(create(ax), create(sine), write(eq));
//   await s.next();                              // wait for the learner
//   const cos = s.plot(Math.cos, { x: [-4, 4], color: "#fc6255" });
//   await s.play(transform(sine, cos));
//   const answer = await s.ask("Where is cos x = 0 first (x > 0)?", ["π/2", "π", "2π"]);
//
// Everything the learner answers or clicks with send=true reaches the agent.
//
// Teach-along: `await s.say("…")` narrates out loud (and as a caption) in the
// Pantheon UI; run it alongside animations with Promise.all. When the learner
// interrupts to ask something, the lesson holds (animations freeze, narration
// stops) and resumes, repeating the interrupted sentence, once they're done.

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import katex from "katex";

export { THREE };

// --- the bridge to Pantheon ----------------------------------------------------------

export const pantheon = {
  /** Send something to the agent that made this page (appears as your message). */
  send(text, data) {
    window.parent?.postMessage({ pantheon: true, type: "send", text: String(text), data: data ?? null }, "*");
  },
};
window.pantheon = pantheon;
window.addEventListener("error", (e) => showError(e.message || String(e.error)));
window.addEventListener("unhandledrejection", (e) => showError(String(e.reason?.message ?? e.reason)));

function progress(text, step = false) {
  window.parent?.postMessage({ pantheon: true, type: "progress", text: String(text).slice(0, 300), step }, "*");
}

function showError(msg) {
  console.error(msg);
  let el = document.getElementById("stage-error");
  if (!el) {
    el = document.createElement("div");
    el.id = "stage-error";
    el.style.cssText = "position:fixed;left:12px;right:12px;bottom:12px;padding:10px 12px;border-radius:8px;background:#5b1e24;color:#ffd7db;font:13px ui-monospace,monospace;z-index:99;white-space:pre-wrap";
    document.body.appendChild(el);
  }
  el.textContent = "⚠ " + msg;
  window.parent?.postMessage({ pantheon: true, type: "error", text: msg }, "*");
}

// --- easing & animation ---------------------------------------------------------------

export const ease = {
  linear: (t) => t,
  smooth: (t) => t * t * (3 - 2 * t),
  inOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  out: (t) => 1 - Math.pow(1 - t, 3),
};

class Anim {
  constructor(duration, update, { easing = ease.smooth, begin, end } = {}) {
    Object.assign(this, { duration, update, easing, begin, end });
  }
}

const toV = (p) => new THREE.Vector3(p?.[0] ?? 0, p?.[1] ?? 0, p?.[2] ?? 0);
const color = (c) => new THREE.Color(c ?? "#ffffff");

// --- mobjects -----------------------------------------------------------------------

class Mob {
  constructor(stage, obj) {
    this.stage = stage;
    this.obj = obj; // THREE.Object3D (or null for DOM-only labels)
    this._opacity = 1;
  }
  materials() {
    const out = [];
    this.obj?.traverse((o) => o.material && out.push(...[].concat(o.material)));
    return out;
  }
  get opacity() {
    return this._opacity;
  }
  set opacity(v) {
    this._opacity = v;
    for (const m of this.materials()) {
      m.transparent = true;
      m.opacity = v * (m.userData.baseOpacity ?? 1);
    }
    if (this.obj) this.obj.visible = v > 0.001;
  }
  get position() {
    return this.obj.position;
  }
  at(p) {
    this.obj.position.copy(toV(p));
    return this;
  }
  setColor(c) {
    for (const m of this.materials()) m.color?.set(c);
    return this;
  }
  remove() {
    this.stage.remove(this);
  }
}

class LineMob extends Mob {
  constructor(stage, points, { color: c = "#ffffff", width = 3, dashed = false } = {}) {
    const geom = new LineGeometry();
    const mat = new LineMaterial({ color: color(c), linewidth: width, dashed, dashSize: 0.15, gapSize: 0.1, transparent: true });
    mat.resolution.set(window.innerWidth, window.innerHeight);
    const line = new Line2(geom, mat);
    super(stage, line);
    this.material = mat;
    this.setPoints(points);
  }
  setPoints(points) {
    this.points = points.map((p) => [p[0], p[1], p[2] ?? 0]);
    const flat = this.points.flat();
    this.obj.geometry.dispose();
    this.obj.geometry = new LineGeometry();
    this.obj.geometry.setPositions(flat.length >= 6 ? flat : [...flat, ...flat]);
    this.obj.computeLineDistances();
    this.segments = Math.max(1, this.points.length - 1);
    this.draw(1);
  }
  draw(fraction) {
    this.obj.geometry.instanceCount = Math.max(0, Math.round(this.segments * fraction));
  }
}

class LabelMob extends Mob {
  constructor(stage, html, { at = [0, 0, 0], size = 0.32, color: c = "#ffffff", anchor = "center" } = {}) {
    const anchorObj = new THREE.Object3D();
    super(stage, anchorObj);
    anchorObj.position.copy(toV(at));
    this.el = document.createElement("div");
    this.el.className = "stage-label";
    this.el.style.color = c;
    this.el.style.fontSize = `${size}em`;
    this.el.dataset.anchor = anchor;
    this.el.innerHTML = html;
    stage.overlay.appendChild(this.el);
    stage.labels.add(this);
  }
  set opacity(v) {
    this._opacity = v;
    this.el.style.opacity = String(v);
  }
  get opacity() {
    return this._opacity;
  }
  setColor(c) {
    this.el.style.color = c;
    return this;
  }
  set html(h) {
    this.el.innerHTML = h;
  }
  remove() {
    this.el.remove();
    this.stage.labels.delete(this);
    super.remove();
  }
}

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

// --- the stage ------------------------------------------------------------------------

const CSS = `
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:#ececec;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}
canvas.stage{display:block;width:100vw;height:100vh}
.stage-overlay{position:fixed;inset:0;pointer-events:none;overflow:hidden}
.stage-label{position:absolute;transform:translate(-50%,-50%);white-space:nowrap;font-size:1em;line-height:1.2;will-change:transform}
.stage-label[data-anchor=left]{transform:translate(0,-50%)}
.stage-title{position:fixed;top:18px;left:0;right:0;text-align:center;font-size:22px;font-weight:600;letter-spacing:.2px;pointer-events:none}
.stage-caption{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);max-width:min(900px,90vw);padding:10px 16px;border-radius:10px;background:rgba(15,17,22,.78);font-size:17px;line-height:1.45;text-align:center;backdrop-filter:blur(6px);transition:opacity .25s}
.stage-panel{position:fixed;right:16px;top:16px;display:flex;flex-direction:column;gap:8px;max-width:320px;pointer-events:auto}
.stage-card{background:rgba(22,24,31,.92);border:1px solid #2f3441;border-radius:12px;padding:12px 14px;font-size:15px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.stage-btn{font:inherit;font-size:14px;padding:8px 14px;border-radius:8px;border:1px solid #3a4152;background:#232734;color:#eee;cursor:pointer;transition:background .15s}
.stage-btn:hover{background:#2e3445}.stage-btn.primary{background:#6d5dfc;border-color:#6d5dfc;color:#fff}
.stage-btn.right{background:#1f5f3f;border-color:#3ecf8e}.stage-btn.wrong{background:#5b1e24;border-color:#f2667a}
.stage-next{position:fixed;right:22px;bottom:22px;pointer-events:auto}
.stage-choices{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.stage-input{font:inherit;width:100%;box-sizing:border-box;margin-top:8px;padding:8px 10px;border-radius:8px;border:1px solid #3a4152;background:#11131a;color:#eee}
.stage-slider{width:100%}
.katex{font-size:1.1em}
body.stage-held .stage-caption{outline:2px solid #f5b454}
body.stage-held::after{content:"⏸ Paused — the teacher is listening";position:fixed;top:14px;left:14px;padding:4px 10px;border-radius:999px;background:#f5b454;color:#111;font:600 12px ui-sans-serif,system-ui;z-index:50}
`;

export class Stage {
  /**
   * @param {{mode?: "2d"|"3d", background?: string, height?: number, title?: string, grid?: boolean}} opts
   *   2D: an orthographic view `height` units tall (default 8), y up, origin centered.
   *   3D: a perspective camera you can orbit with the mouse.
   */
  constructor({ mode = "2d", background = "#0f1116", height = 8, title } = {}) {
    document.documentElement.style.setProperty("--bg", background);
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = new URL("./katex/katex.min.css", import.meta.url).href;
    document.head.appendChild(link);

    this.mode = mode;
    this.frameHeight = height;
    this.scene = new THREE.Scene();
    this.scene.background = color(background);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    this.renderer.domElement.className = "stage";
    document.body.appendChild(this.renderer.domElement);
    this.overlay = Object.assign(document.createElement("div"), { className: "stage-overlay" });
    document.body.appendChild(this.overlay);
    this.panel = Object.assign(document.createElement("div"), { className: "stage-panel" });
    document.body.appendChild(this.panel);
    this.labels = new Set();
    this.mobs = new Set();
    this.running = [];
    this.held = false;
    this._holdWaiters = [];
    this._sayWaiters = new Map();
    this._sayId = 0;
    window.addEventListener("message", (e) => {
      const d = e.data;
      if (e.source !== window.parent || !d?.pantheon) return;
      if (d.type === "hold") this.hold(!!d.on);
      if (d.type === "said") this._sayWaiters.get(d.id)?.(d);
      if (d.type === "say-ack") this._sayWaiters.get(d.id)?.({ ack: true });
    });

    if (mode === "3d") {
      this.camera = new THREE.PerspectiveCamera(50, 1, 0.05, 500);
      this.camera.position.set(6, 5, 8);
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.1));
      const sun = new THREE.DirectionalLight(0xffffff, 1.6);
      sun.position.set(5, 10, 7);
      this.scene.add(sun);
    } else {
      this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -100, 100);
      this.camera.position.set(0, 0, 10);
    }
    if (title) this.title(title);
    const resize = () => this.resize();
    window.addEventListener("resize", resize);
    resize();
    this.last = performance.now();
    this.renderer.setAnimationLoop(() => this.frame());
  }

  resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.renderer.setSize(w, h, false);
    if (this.mode === "3d") {
      this.camera.aspect = w / h;
    } else {
      const hh = this.frameHeight / 2;
      const hw = hh * (w / h);
      Object.assign(this.camera, { left: -hw, right: hw, top: hh, bottom: -hh });
    }
    this.camera.updateProjectionMatrix();
    for (const m of this.mobs) m.material?.resolution?.set(w, h);
  }

  frame() {
    const now = performance.now();
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    for (const r of this.held ? [] : [...this.running]) {
      r.t = Math.min(1, r.t + dt / Math.max(0.0001, r.anim.duration));
      r.anim.update(r.anim.easing(r.t));
      if (r.t >= 1) {
        this.running.splice(this.running.indexOf(r), 1);
        r.anim.end?.();
        r.resolve();
      }
    }
    if (!this.held) this.onFrame?.(dt);
    this.controls?.update();
    this.renderer.render(this.scene, this.camera);
    const w = window.innerWidth;
    const h = window.innerHeight;
    const v = new THREE.Vector3();
    for (const l of this.labels) {
      l.obj.getWorldPosition(v).project(this.camera);
      const hidden = v.z > 1 || !l.obj.visible;
      l.el.style.display = hidden ? "none" : "";
      l.el.style.left = `${((v.x + 1) / 2) * w}px`;
      l.el.style.top = `${((1 - v.y) / 2) * h}px`;
    }
  }

  add(mob) {
    if (mob.obj && !mob.obj.parent) this.scene.add(mob.obj);
    this.mobs.add(mob);
    return mob;
  }
  remove(mob) {
    mob.obj?.removeFromParent();
    this.mobs.delete(mob);
  }
  clear() {
    for (const m of [...this.mobs]) m.remove();
    this.panel.innerHTML = "";
  }

  // --- shapes & math ---------------------------------------------------------------

  line(points, opts) {
    return this.add(new LineMob(this, points, opts));
  }
  polyline(points, opts) {
    return this.line(points, opts);
  }
  arrow(from, to, { color: c = "#ffffff", width = 3, head = 0.22 } = {}) {
    const a = toV(from);
    const b = toV(to);
    const dir = b.clone().sub(a);
    const len = dir.length();
    const shaftEnd = a.clone().add(dir.clone().multiplyScalar(Math.max(0, (len - head) / len)));
    const group = new THREE.Group();
    const mob = new Mob(this, group);
    const shaft = new LineMob(this, [a.toArray(), shaftEnd.toArray()], { color: c, width });
    group.add(shaft.obj);
    const cone = new THREE.Mesh(new THREE.ConeGeometry(head * 0.45, head, 20), new THREE.MeshBasicMaterial({ color: color(c), transparent: true }));
    cone.position.copy(b.clone().sub(dir.clone().normalize().multiplyScalar(head / 2)));
    cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
    group.add(cone);
    mob.shaft = shaft;
    this.mobs.add(shaft);
    return this.add(mob);
  }
  vector(to, opts) {
    return this.arrow([0, 0, 0], to, opts);
  }
  dot(at, { color: c = "#ffffff", r = 0.07 } = {}) {
    const m = new THREE.Mesh(new THREE.CircleGeometry(r, 32), new THREE.MeshBasicMaterial({ color: color(c), transparent: true }));
    if (this.mode === "3d") m.geometry = new THREE.SphereGeometry(r, 20, 14);
    m.position.copy(toV(at));
    return this.add(new Mob(this, m));
  }
  circle(center = [0, 0], r = 1, opts = {}) {
    const pts = Array.from({ length: 129 }, (_, i) => {
      const a = (i / 128) * Math.PI * 2;
      return [center[0] + r * Math.cos(a), center[1] + r * Math.sin(a), center[2] ?? 0];
    });
    return this.shape(pts, opts);
  }
  rect(center = [0, 0], w = 2, h = 1, opts = {}) {
    const [x, y] = center;
    return this.shape([[x - w / 2, y - h / 2], [x + w / 2, y - h / 2], [x + w / 2, y + h / 2], [x - w / 2, y + h / 2], [x - w / 2, y - h / 2]], opts);
  }
  polygon(points, opts = {}) {
    return this.shape([...points, points[0]], opts);
  }
  /** A closed outline, optionally filled: {color, fill, fillOpacity, width}. */
  shape(points, { color: c = "#ffffff", fill, fillOpacity = 0.35, width = 3 } = {}) {
    const group = new THREE.Group();
    const mob = new Mob(this, group);
    const outline = new LineMob(this, points, { color: c, width });
    group.add(outline.obj);
    this.mobs.add(outline);
    mob.outline = outline;
    if (fill) {
      const s = new THREE.Shape(points.map((p) => new THREE.Vector2(p[0], p[1])));
      const mat = new THREE.MeshBasicMaterial({ color: color(fill), transparent: true, opacity: fillOpacity, side: THREE.DoubleSide, depthWrite: false });
      mat.userData.baseOpacity = fillOpacity;
      const mesh = new THREE.Mesh(new THREE.ShapeGeometry(s), mat);
      mesh.position.z = -0.01;
      group.add(mesh);
    }
    return this.add(mob);
  }

  /** Axes with ticks and optional numbers/grid: {x:[min,max,step], y:[...], z?, labels, grid, color}. */
  axes({ x = [-5, 5, 1], y = [-3, 3, 1], z, labels = true, grid = false, color: c = "#8a93a6" } = {}) {
    const group = new THREE.Group();
    const mob = new Mob(this, group);
    mob.parts = [];
    const axis = (from, to) => {
      const l = new LineMob(this, [from, to], { color: c, width: 2 });
      group.add(l.obj);
      this.mobs.add(l);
      mob.parts.push(l);
    };
    const [x0, x1, xs = 1] = x;
    const [y0, y1, ys = 1] = y;
    if (grid) {
      for (let v = Math.ceil(x0 / xs) * xs; v <= x1 + 1e-9; v += xs) axis([v, y0], [v, y1]);
      for (let v = Math.ceil(y0 / ys) * ys; v <= y1 + 1e-9; v += ys) axis([x0, v], [x1, v]);
      for (const p of mob.parts) {
        p.material.opacity = 0.25;
        p.material.userData.baseOpacity = 0.25;
      }
    }
    axis([x0, 0], [x1, 0]);
    axis([0, y0], [0, y1]);
    if (z) axis([0, 0, z[0]], [0, 0, z[1]]);
    const t = 0.08;
    for (let v = Math.ceil(x0 / xs) * xs; v <= x1 + 1e-9; v += xs) {
      if (Math.abs(v) < 1e-9) continue;
      axis([v, -t], [v, t]);
      if (labels) mob.parts.push(this.text(fmt(v), { at: [v, -0.32], size: 0.8, color: c }));
    }
    for (let v = Math.ceil(y0 / ys) * ys; v <= y1 + 1e-9; v += ys) {
      if (Math.abs(v) < 1e-9) continue;
      axis([-t, v], [t, v]);
      if (labels) mob.parts.push(this.text(fmt(v), { at: [-0.35, v], size: 0.8, color: c }));
    }
    mob.x = x;
    mob.y = y;
    return this.add(mob);
  }

  /** Graph of y = f(x): {x:[a,b], samples, color, width}. Transformable into another plot. */
  plot(f, { x = [-5, 5], samples = 240, ...opts } = {}) {
    const pts = [];
    for (let i = 0; i <= samples; i++) {
      const xv = x[0] + ((x[1] - x[0]) * i) / samples;
      const yv = f(xv);
      pts.push([xv, Number.isFinite(yv) ? yv : 0, 0]);
    }
    const mob = this.line(pts, { color: "#58c4dd", ...opts });
    mob.f = f;
    return mob;
  }
  /** Curve t -> [x, y, z]: {t:[a,b], samples, color}. */
  parametric(f, { t = [0, 1], samples = 240, ...opts } = {}) {
    const pts = Array.from({ length: samples + 1 }, (_, i) => f(t[0] + ((t[1] - t[0]) * i) / samples));
    return this.line(pts, { color: "#83c167", ...opts });
  }
  /** Surface z = f(x, y) (3D): {x:[a,b], y:[a,b], segments, color, wireframe}. */
  surface(f, { x = [-3, 3], y = [-3, 3], segments = 60, color: c = "#58c4dd", wireframe = false, opacity = 0.9 } = {}) {
    const geo = new THREE.PlaneGeometry(x[1] - x[0], y[1] - y[0], segments, segments);
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const px = pos.getX(i) + (x[0] + x[1]) / 2;
      const py = pos.getY(i) + (y[0] + y[1]) / 2;
      pos.setXYZ(i, px, f(px, py), -py);
    }
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ color: color(c), side: THREE.DoubleSide, wireframe, transparent: true, opacity, roughness: 0.6 });
    mat.userData.baseOpacity = opacity;
    return this.add(new Mob(this, new THREE.Mesh(geo, mat)));
  }
  sphere(at = [0, 0, 0], r = 1, { color: c = "#58c4dd", opacity = 1 } = {}) {
    return this.mesh(new THREE.SphereGeometry(r, 48, 32), at, c, opacity);
  }
  box(at = [0, 0, 0], size = [1, 1, 1], { color: c = "#fc6255", opacity = 1 } = {}) {
    return this.mesh(new THREE.BoxGeometry(...size), at, c, opacity);
  }
  cylinder(at = [0, 0, 0], r = 0.5, h = 1, { color: c = "#83c167", opacity = 1 } = {}) {
    return this.mesh(new THREE.CylinderGeometry(r, r, h, 40), at, c, opacity);
  }
  /** Any three.js geometry as a mobject. */
  mesh(geometry, at = [0, 0, 0], c = "#58c4dd", opacity = 1) {
    const mat = new THREE.MeshStandardMaterial({ color: color(c), transparent: opacity < 1, opacity, roughness: 0.5, metalness: 0.05 });
    mat.userData.baseOpacity = opacity;
    const m = new THREE.Mesh(geometry, mat);
    m.position.copy(toV(at));
    return this.add(new Mob(this, m));
  }
  /** Wrap your own three.js object (you manage its materials). */
  object(obj3d) {
    return this.add(new Mob(this, obj3d));
  }
  group(...mobs) {
    const g = new THREE.Group();
    const mob = new Mob(this, g);
    mob.children = mobs;
    for (const m of mobs) if (m.obj) g.add(m.obj);
    return this.add(mob);
  }

  // --- text -------------------------------------------------------------------------

  /** Plain text label at a point: {at, size (em), color, anchor: "center"|"left"}. */
  text(str, opts = {}) {
    return this.add(new LabelMob(this, esc(str), { size: 1.2, ...opts }));
  }
  /** LaTeX label (KaTeX): s.tex("e^{i\\pi} + 1 = 0", {at: [0, 1], size: 1.6}). */
  tex(latex, opts = {}) {
    const html = katex.renderToString(latex, { throwOnError: false, displayMode: false });
    const mob = this.add(new LabelMob(this, html, { size: 1.4, ...opts }));
    mob.latex = latex;
    return mob;
  }
  title(str) {
    let el = document.querySelector(".stage-title");
    if (!el) {
      el = Object.assign(document.createElement("div"), { className: "stage-title" });
      document.body.appendChild(el);
    }
    el.textContent = str;
  }
  /** A subtitle at the bottom (narration). Empty string hides it. Supports $inline tex$. */
  caption(str) {
    let el = document.querySelector(".stage-caption");
    if (!el) {
      el = Object.assign(document.createElement("div"), { className: "stage-caption" });
      document.body.appendChild(el);
    }
    el.style.opacity = str ? "1" : "0";
    el.innerHTML = richText(str ?? "");
    if (str) progress(str);
  }

  // --- time -------------------------------------------------------------------------

  /** Run animations together; resolves when all finish. */
  async play(...anims) {
    await this.gate();
    return Promise.all(
      anims.flat().map(
        (anim) =>
          new Promise((resolve) => {
            anim.begin?.();
            this.running.push({ anim, t: 0, resolve });
          }),
      ),
    );
  }
  async wait(seconds = 1) {
    let left = seconds * 1000;
    while (left > 0) {
      await this.gate();
      const step = Math.min(100, left);
      await new Promise((r) => setTimeout(r, step));
      if (!this.held) left -= step;
    }
  }

  // --- teach-along ------------------------------------------------------------------

  /** Pause/resume the lesson (the Pantheon UI does this while the learner is talking). */
  hold(on) {
    this.held = on;
    document.body.classList.toggle("stage-held", on);
    if (!on) for (const w of this._holdWaiters.splice(0)) w();
  }
  /** Resolves immediately, or when the hold is released. */
  gate() {
    return this.held ? new Promise((r) => this._holdWaiters.push(r)) : Promise.resolve();
  }
  /**
   * Narrate: speaks the text in the teacher's voice (and shows it as the caption).
   * Resolves when it has been said. If the learner interrupts, the sentence is
   * repeated after the lesson resumes. $inline tex$ is shown in the caption.
   */
  async say(text, { caption = true } = {}) {
    for (let attempt = 0; attempt < 3; attempt++) {
      await this.gate();
      if (caption) this.caption(text);
      else progress(text);
      const r = await this._speak(text);
      if (r.interrupted) continue;
      if (!r.spoken) await this.wait(Math.min(12, 1 + text.split(/\s+/).length / 2.6));
      return;
    }
  }
  _speak(text) {
    if (window.parent === window) return Promise.resolve({ spoken: false });
    const id = ++this._sayId;
    return new Promise((resolve) => {
      // A host that narrates acknowledges within a moment; otherwise fall back to
      // timed captions straight away.
      let timer = setTimeout(() => finish({ spoken: false }), 1500);
      const finish = (r) => {
        if (r?.ack) {
          clearTimeout(timer);
          timer = setTimeout(() => finish({ spoken: false }), 30000 + text.length * 120);
          return;
        }
        clearTimeout(timer);
        this._sayWaiters.delete(id);
        resolve(r);
      };
      this._sayWaiters.set(id, finish);
      window.parent.postMessage({ pantheon: true, type: "say", id, text: String(text) }, "*");
    });
  }
  /** Mark where the lesson is ("Part 2: the derivative"); the teacher sees it. */
  step(label) {
    progress(label, true);
  }
  /** Move the camera (3D: position + look-at target). */
  cameraTo(position, target = [0, 0, 0], duration = 1.2) {
    const p0 = this.camera.position.clone();
    const p1 = toV(position);
    const t0 = this.controls ? this.controls.target.clone() : new THREE.Vector3();
    const t1 = toV(target);
    return this.play(
      new Anim(duration, (t) => {
        this.camera.position.lerpVectors(p0, p1, t);
        if (this.controls) this.controls.target.lerpVectors(t0, t1, t);
        else this.camera.lookAt(t0.clone().lerp(t1, t));
      }),
    );
  }

  // --- interaction ------------------------------------------------------------------

  /** Wait until the learner clicks "Next" (pacing). */
  next(label = "Next →") {
    return new Promise((resolve) => {
      const b = Object.assign(document.createElement("button"), { className: "stage-btn primary stage-next", textContent: label });
      b.onclick = () => (b.remove(), resolve());
      document.body.appendChild(b);
    });
  }
  button(label, onClick) {
    const b = Object.assign(document.createElement("button"), { className: "stage-btn", textContent: label });
    b.onclick = () => onClick?.();
    this.panel.appendChild(b);
    return b;
  }
  /** A slider in the side panel: {label, min, max, step, value, onChange(v)}. */
  slider({ label = "", min = 0, max = 1, step = 0.01, value = min, onChange } = {}) {
    const card = Object.assign(document.createElement("div"), { className: "stage-card" });
    const title = document.createElement("div");
    const input = Object.assign(document.createElement("input"), { type: "range", min, max, step, value, className: "stage-slider" });
    const show = () => (title.innerHTML = `${richText(label)}: <b>${fmt(Number(input.value))}</b>`);
    input.oninput = () => (show(), onChange?.(Number(input.value)));
    show();
    card.append(title, input);
    this.panel.appendChild(card);
    return { get value() {
      return Number(input.value);
    }, el: input };
  }
  /**
   * Ask a multiple-choice question. Resolves with the chosen option; the answer is
   * sent to the agent. With {correct: index} the learner gets instant feedback.
   */
  ask(question, options, { correct, send = true } = {}) {
    return new Promise((resolve) => {
      const card = Object.assign(document.createElement("div"), { className: "stage-card" });
      card.innerHTML = `<div>${richText(question)}</div>`;
      const list = Object.assign(document.createElement("div"), { className: "stage-choices" });
      options.forEach((opt, i) => {
        const b = Object.assign(document.createElement("button"), { className: "stage-btn" });
        b.innerHTML = richText(String(opt));
        b.onclick = () => {
          for (const x of list.children) x.disabled = true;
          if (correct !== undefined) {
            b.classList.add(i === correct ? "right" : "wrong");
            list.children[correct]?.classList.add("right");
          }
          if (send) pantheon.send(`Answer to "${question}": ${opt}${correct !== undefined ? (i === correct ? " (correct)" : " (wrong)") : ""}`, { question, answer: opt, index: i });
          setTimeout(() => (card.remove(), resolve(opt)), correct !== undefined ? 1400 : 250);
        };
        list.appendChild(b);
      });
      card.appendChild(list);
      this.panel.appendChild(card);
    });
  }
  /** Ask for a typed answer; resolves with the text (also sent to the agent). */
  input(question, { send = true, placeholder = "" } = {}) {
    return new Promise((resolve) => {
      const card = Object.assign(document.createElement("div"), { className: "stage-card" });
      card.innerHTML = `<div>${richText(question)}</div>`;
      const field = Object.assign(document.createElement("input"), { className: "stage-input", placeholder });
      const ok = Object.assign(document.createElement("button"), { className: "stage-btn primary", textContent: "Send" });
      ok.style.marginTop = "8px";
      const done = () => {
        const v = field.value.trim();
        if (!v) return;
        if (send) pantheon.send(`Answer to "${question}": ${v}`, { question, answer: v });
        card.remove();
        resolve(v);
      };
      ok.onclick = done;
      field.onkeydown = (e) => e.key === "Enter" && done();
      card.append(field, ok);
      this.panel.appendChild(card);
      field.focus();
    });
  }
}

// --- animations (manim-style) ------------------------------------------------------------

/** Draw a line/plot/shape progressively, or grow other objects in. */
export function create(mob, duration = 1.2) {
  const lines = collectLines(mob);
  if (lines.length) {
    return new Anim(duration, (t) => lines.forEach((l) => l.draw(t)), { begin: () => ((mob.opacity = 1), lines.forEach((l) => l.draw(0))) });
  }
  return grow(mob, duration);
}
export function uncreate(mob, duration = 0.8) {
  const lines = collectLines(mob);
  if (lines.length) return new Anim(duration, (t) => lines.forEach((l) => l.draw(1 - t)));
  return fadeOut(mob, duration);
}
export function grow(mob, duration = 0.8) {
  const s = mob.obj.scale.clone();
  return new Anim(duration, (t) => mob.obj.scale.copy(s).multiplyScalar(Math.max(0.0001, t)), { easing: ease.out, begin: () => ((mob.opacity = 1), mob.obj.scale.setScalar(0.0001)) });
}
export function fadeIn(mob, duration = 0.8) {
  const parts = mob.parts ?? mob.children ?? [];
  return new Anim(duration, (t) => ((mob.opacity = t), parts.forEach((p) => (p.opacity = t))), { begin: () => ((mob.opacity = 0), parts.forEach((p) => (p.opacity = 0))) });
}
export function fadeOut(mob, duration = 0.8) {
  const parts = mob.parts ?? mob.children ?? [];
  return new Anim(duration, (t) => ((mob.opacity = 1 - t), parts.forEach((p) => (p.opacity = 1 - t))));
}
/** Typewriter for text/tex labels (fades in for tex). */
export function write(mob, duration = 1) {
  if (!(mob instanceof LabelMob) || mob.latex) return fadeIn(mob, duration);
  const full = mob.el.textContent;
  return new Anim(duration, (t) => (mob.el.textContent = full.slice(0, Math.round(full.length * t))), { easing: ease.linear, begin: () => ((mob.opacity = 1), (mob.el.textContent = "")) });
}
export function moveTo(mob, at, duration = 1) {
  const from = mob.obj.position.clone();
  const to = toV(at);
  return new Anim(duration, (t) => mob.obj.position.lerpVectors(from, to, t));
}
export function shift(mob, delta, duration = 1) {
  return moveTo(mob, mob.obj.position.clone().add(toV(delta)).toArray(), duration);
}
export function scaleTo(mob, k, duration = 1) {
  const from = mob.obj.scale.clone();
  const to = new THREE.Vector3(k, k, k);
  return new Anim(duration, (t) => mob.obj.scale.lerpVectors(from, to, t));
}
export function rotate(mob, angle = Math.PI, axis = "z", duration = 1) {
  const start = mob.obj.rotation[axis];
  return new Anim(duration, (t) => (mob.obj.rotation[axis] = start + angle * t));
}
export function colorTo(mob, c, duration = 0.8) {
  const mats = mob.materials();
  const from = mats.map((m) => m.color?.clone());
  const to = color(c);
  if (mob instanceof LabelMob) return new Anim(duration, () => {}, { end: () => mob.setColor(c) });
  return new Anim(duration, (t) => mats.forEach((m, i) => from[i] && m.color.copy(from[i]).lerp(to, t)));
}
/** Morph one line/plot into another (manim's Transform); `a` takes b's shape and b is removed. */
export function transform(a, b, duration = 1.4) {
  const la = collectLines(a)[0];
  const lb = collectLines(b)[0];
  if (!la || !lb) return fadeIn(b, duration);
  const n = Math.max(la.points.length, lb.points.length);
  const pa = resample(la.points, n);
  const pb = resample(lb.points, n);
  const ca = la.material.color.clone();
  const cb = lb.material.color.clone();
  return new Anim(
    duration,
    (t) => {
      la.setPoints(pa.map((p, i) => [p[0] + (pb[i][0] - p[0]) * t, p[1] + (pb[i][1] - p[1]) * t, p[2] + (pb[i][2] - p[2]) * t]));
      la.material.color.copy(ca).lerp(cb, t);
    },
    { begin: () => (b.opacity = 0), end: () => (b.remove(), a.f && b.f && (a.f = b.f)) },
  );
}
/** Count a text label from one number to another: countTo(label, 0, 100, v => v.toFixed(0)). */
export function countTo(mob, from, to, format = fmt, duration = 1.5) {
  return new Anim(duration, (t) => (mob.el.textContent = format(from + (to - from) * t)));
}
/** Run a custom update over time: tween(t => ..., seconds). */
export function tween(update, duration = 1, easing = ease.smooth) {
  return new Anim(duration, update, { easing });
}

// --- helpers ------------------------------------------------------------------------

function collectLines(mob) {
  if (mob instanceof LineMob) return [mob];
  if (mob.outline) return [mob.outline];
  if (mob.shaft) return [mob.shaft];
  if (mob.parts) return mob.parts.filter((p) => p instanceof LineMob);
  if (mob.children) return mob.children.flatMap(collectLines);
  return [];
}

function resample(points, n) {
  if (points.length === n) return points.map((p) => [...p]);
  return Array.from({ length: n }, (_, i) => {
    const f = (i / (n - 1)) * (points.length - 1);
    const j = Math.floor(f);
    const k = Math.min(points.length - 1, j + 1);
    const w = f - j;
    return points[j].map((v, d) => v + (points[k][d] - v) * w);
  });
}

export function fmt(v) {
  return Number.isInteger(v) ? String(v) : Number(v.toFixed(2)).toString();
}

/** Text with $inline latex$ segments. */
export function richText(s) {
  return String(s)
    .split(/(\$[^$]+\$)/g)
    .map((part) => (part.startsWith("$") && part.endsWith("$") && part.length > 2 ? katex.renderToString(part.slice(1, -1), { throwOnError: false }) : esc(part)))
    .join("");
}
