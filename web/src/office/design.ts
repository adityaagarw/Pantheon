/**
 * Office designs: what's in the office (items, rooms) — editable in the
 * designer and saved on the org (`org.layout.office`). Orgs without a saved
 * design get a generated, fully furnished default that grows with the team.
 */

import type { Agent } from "@/lib/types";
import { CATALOG, itemBounds, toWorld } from "./catalog";
import { footprint } from "./models";

export type RoomType = "meeting" | "lounge" | "kitchen" | "team" | "focus" | "reception" | "custom";
export type WallKind = "none" | "glass" | "solid";
export type Side = "n" | "s" | "e" | "w";
export type FloorStyle = "oak" | "walnut" | "concrete" | "carpet" | "tile" | "grass" | "asphalt";

export interface PlacedItem {
  id: string;
  kind: string;
  x: number;
  z: number;
  rot: number;
  y?: number;
  agentId?: string | null;
  boardId?: string | null; // whiteboards: which board it shows (default: the org's first)
}

export interface Room {
  id: string;
  name: string;
  type: RoomType;
  x: number;
  z: number;
  w: number;
  d: number;
  walls: WallKind;
  door: Side;
}

export interface OfficeDesign {
  version: 1;
  width: number;
  depth: number;
  floor: FloorStyle;
  items: PlacedItem[];
  rooms: Room[];
  /** false = scenes: agents without a workstation get a spot in their team's room, not a new desk. */
  autoDesks?: boolean;
}

export interface Rect {
  x: number;
  z: number;
  w: number;
  d: number;
}

export interface Spot {
  pos: [number, number];
  facing: number;
}

export interface DeskSpot {
  agentId: string;
  itemId: string;
  seat: [number, number];
  facing: number;
  stand: [number, number];
  screen: [number, number, number];
  virtual: boolean;
}

export interface WallSeg extends Rect {
  kind: Exclude<WallKind, "none">;
  roomId: string;
}

export interface ResolvedOffice {
  design: OfficeDesign; // including any auto-added desks
  width: number;
  depth: number;
  desks: DeskSpot[];
  meetingRooms: { room: Room; seats: Spot[] }[];
  loungeSpots: Spot[];
  obstacles: Rect[];
  walls: WallSeg[];
  autoDesks: number;
  /** Where people can sit or stand in each room (lowercased name), for going somewhere. */
  roomSpots: Record<string, { spots: Spot[]; seated: boolean }>;
}

export const DOOR_WIDTH = 1.4;
const WALL_T = 0.1;
let seq = 0;
export const newId = (p: string) => `${p}_${Date.now().toString(36)}${(seq++).toString(36)}${Math.random().toString(36).slice(2, 5)}`;

export const ROOM_COLORS: Record<RoomType, string> = {
  meeting: "#a9bcd6",
  lounge: "#d9c3a5",
  kitchen: "#e7e2d6",
  team: "#c7d3c0",
  focus: "#c8bfd8",
  reception: "#dfd2c0",
  custom: "#d0d0d0",
};

// --- default generation -------------------------------------------------------------

export function generateDesign(agents: Pick<Agent, "id" | "team" | "createdAt">[]): OfficeDesign {
  const items: PlacedItem[] = [];
  const rooms: Room[] = [];
  const add = (kind: string, x: number, z: number, rot = 0, extra: Partial<PlacedItem> = {}) =>
    items.push({ id: newId("it"), kind, x: round(x), z: round(z), rot, ...extra });

  const byTeam = new Map<string, typeof agents>();
  for (const a of [...agents].sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? "") || a.id.localeCompare(b.id))) {
    const t = a.team?.trim() || "Team";
    byTeam.set(t, [...(byTeam.get(t) ?? []), a]);
  }
  const ws = CATALOG.workstation.size; // [w, d]
  const podCols = 2;
  const podW = podCols * ws[0] + 0.1;
  const podD = ws[1] * 2 + 0.05;
  const aisleX = 2.4;
  const aisleZ = 2.6;
  const pods: { team: string; members: number }[] = [];
  for (const [team, members] of byTeam) for (let i = 0; i < members.length; i += 4) pods.push({ team, members: Math.min(4, members.length - i) });
  if (pods.length === 0) pods.push({ team: "Team", members: 4 });
  const perRow = Math.max(1, Math.min(3, Math.ceil(Math.sqrt(pods.length))));
  const rows = Math.ceil(pods.length / perRow);
  const x0 = 2.2;
  const z0 = 3.4;

  const teamBounds = new Map<string, Rect>();
  pods.forEach((pod, i) => {
    const cx = x0 + (i % perRow) * (podW + aisleX) + podW / 2;
    const cz = z0 + Math.floor(i / perRow) * (podD + aisleZ) + podD / 2;
    const seats = pod.members <= 2 ? [0, 1] : [0, 1, 2, 3];
    for (const k of seats) {
      const col = k % 2;
      const north = k < 2;
      const x = cx + (col - 0.5) * (ws[0] + 0.05);
      // North row: desk on the south edge, sitter faces south (rot pi).
      const z = north ? cz - ws[1] / 2 - 0.02 : cz + ws[1] / 2 + 0.02;
      add("workstation", x, z, north ? Math.PI : 0);
    }
    const r = { x: cx - podW / 2 - 0.6, z: cz - podD / 2 - 0.6, w: podW + 1.2, d: podD + 1.2 };
    const prev = teamBounds.get(pod.team);
    teamBounds.set(pod.team, prev ? union(prev, r) : r);
  });
  for (const [team, r] of teamBounds) {
    rooms.push({ id: newId("rm"), name: team, type: "team", ...roundRect(r), walls: "none", door: "s" });
  }
  const workW = perRow * (podW + aisleX) - aisleX;
  const workD = rows * (podD + aisleZ) - aisleZ;

  // Task board + bookshelves on the north wall above the team area.
  add("taskboard", x0 + Math.min(workW, 8) / 2, 0.25, 0);
  add("bookcaseClosedWide", x0 + Math.min(workW, 8) + 1.2, 0.35, 0);
  add("pottedPlant", 0.7, 0.7);

  // Right wing: boardroom, optional huddle room, lounge.
  const wingX = x0 + workW + 2.6;
  const wingW = 9.5;
  const big = agents.length > 6;
  const board: Room = { id: newId("rm"), name: "Boardroom", type: "meeting", x: wingX, z: 0.8, w: wingW, d: 7.2, walls: "glass", door: "w" };
  rooms.push(board);
  add(big ? "conference8" : "conference6", board.x + board.w / 2, board.z + board.d / 2);
  add("televisionModern", board.x + board.w / 2, board.z + 0.35, 0);
  add("whiteboard", board.x + 1.9, board.z + 0.3, 0);
  add("pottedPlant", board.x + board.w - 0.6, board.z + 0.6);
  add("pottedPlant", board.x + 0.6, board.z + board.d - 0.6);
  let nextZ = board.z + board.d + 1.6;
  if (big) {
    const huddle: Room = { id: newId("rm"), name: "Huddle room", type: "meeting", x: wingX, z: nextZ, w: 5.5, d: 5, walls: "glass", door: "w" };
    rooms.push(huddle);
    add("huddleTable", huddle.x + huddle.w / 2, huddle.z + huddle.d / 2);
    add("plantSmall2", huddle.x + huddle.w - 0.5, huddle.z + 0.5);
    nextZ += huddle.d + 1.6;
  }
  const lounge: Room = { id: newId("rm"), name: "Lounge", type: "lounge", x: wingX, z: nextZ, w: wingW, d: 7, walls: "none", door: "w" };
  rooms.push(lounge);
  const lx = lounge.x + lounge.w / 2;
  const lz = lounge.z + lounge.d / 2;
  add("rugRectangle", lx, lz);
  add("tableCoffee", lx, lz);
  add("loungeSofaLong", lx, lz - 1.7, 0);
  add("loungeDesignSofa", lx, lz + 1.7, Math.PI);
  add("loungeChair", lx - 2.6, lz, Math.PI / 2);
  add("cabinetTelevision", lx + 3.6, lz, -Math.PI / 2);
  add("televisionModern", lx + 3.6, lz, -Math.PI / 2, { y: footprint("furniture/cabinetTelevision").h });
  add("lampRoundFloor", lx - 3.8, lz - 2.6);
  add("pottedPlant", lx + 4.1, lz - 2.8);
  add("pottedPlant", lx - 4.1, lz + 2.8);
  add("bookcaseOpen", lx + 2.6, lounge.z + 0.4, 0);

  // Kitchen under the team area.
  const kz = z0 + workD + 2.2;
  const kitchen: Room = { id: newId("rm"), name: "Kitchen", type: "kitchen", x: x0 - 0.6, z: kz, w: Math.max(8, Math.min(workW + 1.2, 11)), d: 4.6, walls: "none", door: "n" };
  rooms.push(kitchen);
  const counterZ = kitchen.z + kitchen.d - 0.5;
  const cab = footprint("furniture/kitchenCabinet").w;
  const cabH = footprint("furniture/kitchenCabinet").h;
  const kinds = ["kitchenFridgeLarge", "kitchenCabinet", "kitchenSink", "kitchenCabinetDrawer", "kitchenCabinet"];
  kinds.forEach((k, i) => add(k, kitchen.x + 0.7 + i * cab, counterZ, Math.PI));
  add("kitchenCoffeeMachine", kitchen.x + 0.7 + 4 * cab, counterZ, Math.PI, { y: cabH });
  add("kitchenMicrowave", kitchen.x + 0.7 + 3 * cab, counterZ, Math.PI, { y: cabH });
  const barX = kitchen.x + kitchen.w - 2.6;
  add("kitchenBar", barX, kitchen.z + 1.8, 0);
  add("kitchenBarEnd", barX + footprint("furniture/kitchenBar").w, kitchen.z + 1.8, 0);
  add("stoolBar", barX - 0.2, kitchen.z + 1.05, 0);
  add("stoolBar", barX + 0.7, kitchen.z + 1.05, 0);
  add("trashcan", kitchen.x + kitchen.w - 0.4, counterZ);
  add("plantSmall1", kitchen.x + 0.4, kitchen.z + 0.4);

  const width = round(wingX + wingW + 1.6);
  const depth = round(Math.max(kitchen.z + kitchen.d + 1.2, nextZ + 7 + 1.2));
  add("pottedPlant", width - 0.7, depth - 0.7);
  add("pottedPlant", 0.7, depth - 0.7);
  return { version: 1, width, depth, floor: "oak", items, rooms };
}

// --- resolution ----------------------------------------------------------------------

export function resolveDesign(design: OfficeDesign, agents: Pick<Agent, "id" | "team" | "createdAt">[]): ResolvedOffice {
  const items = [...design.items];
  const ids = new Set(agents.map((a) => a.id));
  const stations = items.filter((i) => CATALOG[i.kind]?.workstation);
  const taken = new Map<string, PlacedItem>();
  for (const s of stations) if (s.agentId && ids.has(s.agentId) && !taken.has(s.agentId)) taken.set(s.agentId, s);
  const free = stations.filter((s) => !s.agentId || !ids.has(s.agentId) || taken.get(s.agentId) !== s);
  const sorted = [...agents].sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? "") || a.id.localeCompare(b.id));
  const teamRooms = design.rooms.filter((r) => r.type === "team");
  let autoDesks = 0;
  const homeless: typeof agents = [];

  const obstaclesFor = (list: PlacedItem[]) =>
    list.filter((i) => CATALOG[i.kind]?.blocking !== false).map((i) => shrink(itemBounds(i), 0.04));

  for (const a of sorted) {
    if (taken.has(a.id)) continue;
    const room = teamRooms.find((r) => r.name.toLowerCase() === (a.team || "").toLowerCase());
    let pick = room ? free.find((s) => inside(room, s.x, s.z)) : undefined;
    pick = pick ?? free[0];
    if (pick) {
      free.splice(free.indexOf(pick), 1);
      taken.set(a.id, pick);
      continue;
    }
    if (design.autoDesks === false) {
      homeless.push(a);
      continue;
    }
    // No free desk: grow the office with a new workstation where there's room.
    const spot = findFreeSpot(design, [...obstaclesFor(items), ...roomRects(design)], CATALOG.workstation.size);
    const item: PlacedItem = { id: `auto_${a.id}`, kind: "workstation", x: spot[0], z: spot[1], rot: 0, agentId: a.id };
    items.push(item);
    taken.set(a.id, item);
    autoDesks += 1;
  }

  const desks: DeskSpot[] = [];
  for (const [agentId, it] of taken) {
    const e = CATALOG[it.kind]!;
    const seat = e.seats[0];
    const seatW = toWorld(it, seat.x, seat.z);
    const standW = toWorld(it, seat.x + 0.45, seat.z + 0.55);
    const scr = e.screen ?? [0, 1, -0.5];
    const scrW = toWorld(it, scr[0], scr[2]);
    desks.push({
      agentId,
      itemId: it.id,
      seat: seatW,
      facing: seat.facing + it.rot,
      stand: standW,
      screen: [scrW[0], scr[1] + (it.y ?? 0), scrW[1]],
      virtual: it.id.startsWith("auto_"),
    });
  }

  // Scenes without desks: each person's home spot is in their team's room.
  homeless.forEach((a, n) => {
    const room = design.rooms.find((r) => r.name.toLowerCase() === (a.team || "").toLowerCase()) ?? design.rooms[n % Math.max(1, design.rooms.length)];
    const cx = room ? room.x + room.w / 2 : design.width / 2;
    const cz = room ? room.z + room.d / 2 : design.depth / 2;
    const k = homeless.filter((b, j) => j < n && b.team === a.team).length;
    const ang = (k / 6) * Math.PI * 2 + 0.4;
    const rr = room ? Math.max(0.6, Math.min(room.w, room.d) / 2 - 1.2) : 2;
    const seat: [number, number] = [round(cx + Math.sin(ang) * rr), round(cz + Math.cos(ang) * rr)];
    desks.push({ agentId: a.id, itemId: "", seat, facing: ang + Math.PI, stand: seat, screen: [0, -5, 0], virtual: true });
  });

  const walls: WallSeg[] = [];
  for (const r of design.rooms) if (r.walls !== "none") walls.push(...roomWalls(r));

  const seatsIn = (room: Room): Spot[] => {
    const out: Spot[] = [];
    for (const it of items) {
      const e = CATALOG[it.kind];
      if (!e || e.workstation || !e.seats.length || !inside(room, it.x, it.z)) continue;
      for (const s of e.seats) out.push({ pos: toWorld(it, s.x, s.z), facing: s.facing + it.rot });
    }
    return out;
  };
  const ring = (room: Room, n: number): Spot[] =>
    Array.from({ length: n }, (_, i) => {
      const a = (i / n) * Math.PI * 2;
      const cx = room.x + room.w / 2;
      const cz = room.z + room.d / 2;
      const rr = Math.min(room.w, room.d) / 2 - 0.9;
      return { pos: [cx + Math.sin(a) * rr, cz + Math.cos(a) * rr] as [number, number], facing: a + Math.PI };
    });

  const meetingRooms = design.rooms
    .filter((r) => r.type === "meeting")
    .map((room) => {
      const seats = seatsIn(room);
      return { room, seats: seats.length ? seats : ring(room, 8) };
    });
  let loungeSpots = design.rooms
    .filter((r) => r.type === "lounge" || r.type === "kitchen")
    .flatMap((r) => {
      const s = seatsIn(r);
      return s.length ? s : ring(r, 6);
    });
  if (!loungeSpots.length) {
    loungeSpots = items.flatMap((it) => {
      const e = CATALOG[it.kind];
      if (!e || e.workstation || e.category !== "Lounge") return [];
      return e.seats.map((s) => ({ pos: toWorld(it, s.x, s.z), facing: s.facing + it.rot }));
    });
  }

  const roomSpots: ResolvedOffice["roomSpots"] = {};
  for (const r of design.rooms) {
    const s = seatsIn(r);
    roomSpots[r.name.toLowerCase()] = s.length ? { spots: s, seated: true } : { spots: ring(r, 8), seated: false };
  }
  const W = design.width;
  const D = design.depth;
  const obstacles: Rect[] = [
    ...obstaclesFor(items),
    ...walls,
    { x: -1, z: -1, w: W + 2, d: 1.12 },
    { x: -1, z: -1, w: 1.12, d: D + 2 },
    { x: W - 0.12, z: -1, w: 1.12, d: D + 2 },
    { x: -1, z: D - 0.12, w: W + 2, d: 1.12 },
  ];
  return { design: { ...design, items }, width: W, depth: D, desks, meetingRooms, loungeSpots, obstacles, walls, autoDesks, roomSpots };
}

export function roomWalls(r: Room): WallSeg[] {
  const kind = r.walls as WallSeg["kind"];
  const sides: Record<Side, Rect> = {
    n: { x: r.x, z: r.z, w: r.w, d: WALL_T },
    s: { x: r.x, z: r.z + r.d - WALL_T, w: r.w, d: WALL_T },
    w: { x: r.x, z: r.z, w: WALL_T, d: r.d },
    e: { x: r.x + r.w - WALL_T, z: r.z, w: WALL_T, d: r.d },
  };
  const out: WallSeg[] = [];
  for (const side of ["n", "s", "e", "w"] as Side[]) {
    const s = sides[side];
    if (side !== r.door) {
      out.push({ ...s, kind, roomId: r.id });
      continue;
    }
    const horizontal = side === "n" || side === "s";
    const len = horizontal ? s.w : s.d;
    const gap = Math.min(DOOR_WIDTH, len - 0.4);
    const a = (len - gap) / 2;
    if (horizontal) {
      out.push({ ...s, w: a, kind, roomId: r.id }, { ...s, x: s.x + a + gap, w: a, kind, roomId: r.id });
    } else {
      out.push({ ...s, d: a, kind, roomId: r.id }, { ...s, z: s.z + a + gap, d: a, kind, roomId: r.id });
    }
  }
  return out;
}

/** Items placed by name (org.layout.extras: by Zeus or plugins), put at a free spot in their room. */
export interface Extra {
  id: string;
  kind: string;
  room: string;
  label?: string | null;
}

export function withExtras(design: OfficeDesign, extras: unknown): OfficeDesign {
  if (!Array.isArray(extras) || !extras.length) return design;
  const items = [...design.items];
  const have = new Set(items.map((i) => i.id));
  for (const raw of extras as Extra[]) {
    if (!raw || have.has(raw.id) || !CATALOG[raw.kind]) continue;
    const room = design.rooms.find((r) => r.name.toLowerCase() === String(raw.room).toLowerCase());
    if (!room) continue;
    const [w, d] = CATALOG[raw.kind]!.size;
    const blockers = items.filter((i) => CATALOG[i.kind]?.blocking !== false).map(itemBounds);
    let spot: [number, number] | null = null;
    for (let z = room.z + d / 2 + 0.5; !spot && z + d / 2 < room.z + room.d - 0.3; z += 0.35) {
      for (let x = room.x + w / 2 + 0.5; x + w / 2 < room.x + room.w - 0.3; x += 0.35) {
        const r = { x: x - w / 2 - 0.25, z: z - d / 2 - 0.25, w: w + 0.5, d: d + 0.5 };
        if (!blockers.some((b) => overlap(b, r))) {
          spot = [round(x), round(z)];
          break;
        }
      }
    }
    spot = spot ?? [round(room.x + room.w / 2), round(room.z + room.d / 2)];
    items.push({ id: raw.id, kind: raw.kind, x: spot[0], z: spot[1], rot: 0 });
    have.add(raw.id);
  }
  return { ...design, items };
}

function findFreeSpot(design: OfficeDesign, blockers: Rect[], size: [number, number]): [number, number] {
  const [w, d] = size;
  for (let z = 2.5; z + d / 2 < design.depth - 1; z += 0.5) {
    for (let x = 1.2 + w / 2; x + w / 2 < design.width - 1; x += 0.5) {
      const r = { x: x - w / 2 - 0.5, z: z - d / 2 - 0.5, w: w + 1, d: d + 1 };
      if (!blockers.some((b) => overlap(b, r))) return [round(x), round(z)];
    }
  }
  return [design.width - 2, design.depth - 2];
}

const roomRects = (design: OfficeDesign): Rect[] => design.rooms.filter((r) => r.type !== "team").map((r) => ({ x: r.x, z: r.z, w: r.w, d: r.d }));
export const inside = (r: Rect, x: number, z: number) => x >= r.x && x <= r.x + r.w && z >= r.z && z <= r.z + r.d;
export const overlap = (a: Rect, b: Rect) => a.x < b.x + b.w && b.x < a.x + a.w && a.z < b.z + b.d && b.z < a.z + a.d;
const union = (a: Rect, b: Rect): Rect => {
  const x = Math.min(a.x, b.x);
  const z = Math.min(a.z, b.z);
  return { x, z, w: Math.max(a.x + a.w, b.x + b.w) - x, d: Math.max(a.z + a.d, b.z + b.d) - z };
};
const shrink = (r: Rect, m: number): Rect => ({ x: r.x + m, z: r.z + m, w: Math.max(0.05, r.w - 2 * m), d: Math.max(0.05, r.d - 2 * m) });
const round = (v: number) => Math.round(v * 100) / 100;
const roundRect = (r: Rect): Rect => ({ x: round(r.x), z: round(r.z), w: round(r.w), d: round(r.d) });

export function isDesign(v: unknown): v is OfficeDesign {
  const d = v as OfficeDesign;
  return !!d && d.version === 1 && Array.isArray(d.items) && Array.isArray(d.rooms) && d.width > 0 && d.depth > 0;
}
