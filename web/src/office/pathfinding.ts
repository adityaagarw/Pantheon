/** Grid A* with line-of-sight path smoothing over the office floor. */

import type { Rect } from "./design";

export const CELL = 0.25;
const CLEARANCE = 0.3; // robot radius

export interface Grid {
  w: number;
  h: number;
  blocked: Uint8Array;
}

export function buildGrid(layout: { width: number; depth: number; obstacles: Rect[] }): Grid {
  const w = Math.ceil(layout.width / CELL);
  const h = Math.ceil(layout.depth / CELL);
  const blocked = new Uint8Array(w * h);
  const mark = (r: Rect) => {
    const x0 = Math.max(0, Math.floor((r.x - CLEARANCE) / CELL));
    const z0 = Math.max(0, Math.floor((r.z - CLEARANCE) / CELL));
    const x1 = Math.min(w - 1, Math.ceil((r.x + r.w + CLEARANCE) / CELL));
    const z1 = Math.min(h - 1, Math.ceil((r.z + r.d + CLEARANCE) / CELL));
    for (let z = z0; z <= z1; z++) for (let x = x0; x <= x1; x++) blocked[z * w + x] = 1;
  };
  layout.obstacles.forEach(mark);
  return { w, h, blocked };
}

const cellOf = (g: Grid, p: [number, number]): [number, number] => [
  Math.min(g.w - 1, Math.max(0, Math.floor(p[0] / CELL))),
  Math.min(g.h - 1, Math.max(0, Math.floor(p[1] / CELL))),
];

function nearestFree(g: Grid, c: [number, number]): [number, number] {
  if (!g.blocked[c[1] * g.w + c[0]]) return c;
  for (let r = 1; r < 40; r++) {
    for (let dz = -r; dz <= r; dz++)
      for (let dx = -r; dx <= r; dx++) {
        if (Math.abs(dx) !== r && Math.abs(dz) !== r) continue;
        const x = c[0] + dx;
        const z = c[1] + dz;
        if (x >= 0 && z >= 0 && x < g.w && z < g.h && !g.blocked[z * g.w + x]) return [x, z];
      }
  }
  return c;
}

function lineFree(g: Grid, a: [number, number], b: [number, number]): boolean {
  const steps = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (CELL / 2));
  for (let i = 0; i <= steps; i++) {
    const t = steps === 0 ? 0 : i / steps;
    const [x, z] = cellOf(g, [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    if (g.blocked[z * g.w + x]) return false;
  }
  return true;
}

/** Path from `from` to `to` (world meters). Endpoints may be inside obstacles (seats). */
export function findPath(g: Grid, from: [number, number], to: [number, number]): [number, number][] {
  const s = nearestFree(g, cellOf(g, from));
  const t = nearestFree(g, cellOf(g, to));
  const key = (x: number, z: number) => z * g.w + x;
  const open = new MinHeap();
  const gScore = new Float32Array(g.w * g.h).fill(Infinity);
  const came = new Int32Array(g.w * g.h).fill(-1);
  const h = (x: number, z: number) => Math.hypot(x - t[0], z - t[1]);
  gScore[key(s[0], s[1])] = 0;
  open.push(key(s[0], s[1]), h(s[0], s[1]));
  const dirs = [
    [1, 0, 1],
    [-1, 0, 1],
    [0, 1, 1],
    [0, -1, 1],
    [1, 1, Math.SQRT2],
    [1, -1, Math.SQRT2],
    [-1, 1, Math.SQRT2],
    [-1, -1, Math.SQRT2],
  ];
  let found = false;
  let guard = 0;
  while (open.size && guard++ < 200_000) {
    const cur = open.pop();
    const cx = cur % g.w;
    const cz = (cur - cx) / g.w;
    if (cx === t[0] && cz === t[1]) {
      found = true;
      break;
    }
    for (const [dx, dz, cost] of dirs) {
      const nx = cx + dx;
      const nz = cz + dz;
      if (nx < 0 || nz < 0 || nx >= g.w || nz >= g.h) continue;
      if (g.blocked[key(nx, nz)]) continue;
      if (dx && dz && (g.blocked[key(cx + dx, cz)] || g.blocked[key(cx, cz + dz)])) continue;
      const ng = gScore[cur] + cost;
      const nk = key(nx, nz);
      if (ng < gScore[nk]) {
        gScore[nk] = ng;
        came[nk] = cur;
        open.push(nk, ng + h(nx, nz));
      }
    }
  }
  if (!found) return [from, to];
  const cells: [number, number][] = [];
  for (let k = key(t[0], t[1]); k !== -1; k = came[k]) {
    const x = k % g.w;
    cells.push([(x + 0.5) * CELL, ((k - x) / g.w + 0.5) * CELL]);
  }
  cells.reverse();
  // String-pull: keep only the waypoints needed for line of sight.
  const pts: [number, number][] = [from];
  let anchor = cells[0];
  for (let i = 1; i < cells.length; i++) {
    if (!lineFree(g, anchor, cells[i])) {
      pts.push(cells[i - 1]);
      anchor = cells[i - 1];
    }
  }
  pts.push(cells[cells.length - 1], to);
  return pts.filter((p, i) => i === 0 || Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) > 0.05);
}

class MinHeap {
  private k: number[] = [];
  private p: number[] = [];
  get size() {
    return this.k.length;
  }
  push(key: number, pri: number) {
    this.k.push(key);
    this.p.push(pri);
    let i = this.k.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (this.p[parent] <= this.p[i]) break;
      this.swap(i, parent);
      i = parent;
    }
  }
  pop(): number {
    const top = this.k[0];
    const lk = this.k.pop()!;
    const lp = this.p.pop()!;
    if (this.k.length) {
      this.k[0] = lk;
      this.p[0] = lp;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1;
        const r = l + 1;
        let m = i;
        if (l < this.k.length && this.p[l] < this.p[m]) m = l;
        if (r < this.k.length && this.p[r] < this.p[m]) m = r;
        if (m === i) break;
        this.swap(i, m);
        i = m;
      }
    }
    return top;
  }
  private swap(a: number, b: number) {
    [this.k[a], this.k[b]] = [this.k[b], this.k[a]];
    [this.p[a], this.p[b]] = [this.p[b], this.p[a]];
  }
}
