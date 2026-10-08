/**
 * Office layouts per theme: each theme is its own floor plan with its own furniture,
 * not just a palette. Classic is the original generator (design.ts); the others are
 * built here from the same pieces: a work area (pods, rows facing a wall, or long
 * shared benches) plus the rooms that kind of workplace has.
 */

import type { Agent } from "@/lib/types";
import { CATALOG } from "./catalog";
import { newId, type OfficeDesign, type PlacedItem, type Rect, type Room, type RoomType, type Side, type WallKind } from "./design";
import { footprint } from "./models";
import { themeOf, type ThemeId } from "./themes";

type People = Pick<Agent, "id" | "team" | "createdAt">[];
const round = (v: number) => Math.round(v * 100) / 100;
const S = Math.PI; // a half turn: faces south

class Plan {
  items: PlacedItem[] = [];
  rooms: Room[] = [];
  add(kind: string, x: number, z: number, rot = 0, extra: Partial<PlacedItem> = {}): void {
    if (!CATALOG[kind]) return; // a model that isn't installed
    this.items.push({ id: newId("it"), kind, x: round(x), z: round(z), rot, ...extra });
  }
  room(name: string, type: RoomType, x: number, z: number, w: number, d: number, walls: WallKind, door: Side): Room {
    const r: Room = { id: newId("rm"), name, type, x: round(x), z: round(z), w: round(w), d: round(d), walls, door };
    this.rooms.push(r);
    return r;
  }
  size(kind: string): [number, number] {
    return CATALOG[kind]?.size ?? [1, 1];
  }
}

function teams(people: People): [string, People][] {
  const byTeam = new Map<string, People>();
  for (const a of [...people].sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? "") || a.id.localeCompare(b.id))) {
    const t = a.team?.trim() || "Team";
    byTeam.set(t, [...(byTeam.get(t) ?? []), a]);
  }
  return byTeam.size ? [...byTeam] : [["Team", []]];
}

/**
 * Desks for every team, from (x0, z0). "pods": face-to-face clusters `cols` wide;
 * "rows": rows of `cols` desks all facing north (towards a wall of screens, say).
 * Each team gets an open team room around its desks. Returns the area's size.
 */
function workArea(p: Plan, people: People, o: { kind: string; style: "pods" | "rows"; cols: number; x0: number; z0: number; across?: number }): { w: number; d: number } {
  const [dw, dd] = p.size(o.kind);
  const gapX = 0.12;
  let z = o.z0;
  let maxW = 0;
  const across = o.across ?? 2; // pods side by side
  for (const [team, members] of teams(people)) {
    const n = Math.max(members.length, 2);
    let rect: Rect | null = null;
    if (o.style === "rows") {
      const rows = Math.ceil(n / o.cols);
      for (let r = 0; r < rows; r++) {
        const count = Math.min(o.cols, n - r * o.cols);
        for (let c = 0; c < count; c++) p.add(o.kind, o.x0 + c * (dw + gapX) + dw / 2, z + dd / 2, 0);
        z += dd + 1.5;
      }
      const w = Math.min(n, o.cols) * (dw + gapX);
      rect = { x: o.x0 - 0.6, z: z - rows * (dd + 1.5) - 0.6, w: w + 1.2, d: rows * (dd + 1.5) - 1.5 + 1.2 };
      maxW = Math.max(maxW, w);
    } else {
      const perPod = o.cols * 2;
      const pods = Math.ceil(n / perPod);
      const podW = o.cols * (dw + gapX);
      const podD = dd * 2 + 0.05;
      const podRows = Math.ceil(pods / across);
      for (let i = 0; i < pods; i++) {
        const cx = o.x0 + (i % across) * (podW + 2.4) + podW / 2;
        const cz = z + Math.floor(i / across) * (podD + 2.6) + podD / 2;
        const seats = Math.min(perPod, n - i * perPod);
        for (let k = 0; k < seats; k++) {
          const col = k % o.cols;
          const north = k < o.cols;
          const x = cx - podW / 2 + col * (dw + gapX) + dw / 2;
          p.add(o.kind, x, north ? cz - dd / 2 - 0.02 : cz + dd / 2 + 0.02, north ? S : 0);
        }
      }
      const w = Math.min(pods, across) * (podW + 2.4) - 2.4;
      rect = { x: o.x0 - 0.6, z: z - 0.6, w: w + 1.2, d: podRows * (podD + 2.6) - 2.6 + 1.2 };
      z += podRows * (podD + 2.6);
      maxW = Math.max(maxW, w);
    }
    p.room(team, "team", rect.x, rect.z, rect.w, rect.d, "none", "s");
    z += 0.8; // a gap between teams
  }
  return { w: maxW, d: z - o.z0 };
}

/** A kitchen counter along the room's south wall, with coffee. */
function kitchenCounter(p: Plan, r: Room, kinds = ["kitchenFridgeLarge", "kitchenCabinet", "kitchenSink", "kitchenCabinetDrawer", "kitchenCabinet"]): void {
  const cab = footprint("furniture/kitchenCabinet").w;
  const cabH = footprint("furniture/kitchenCabinet").h;
  const z = r.z + r.d - 0.5;
  kinds.forEach((k, i) => p.add(k, r.x + 0.7 + i * cab, z, S));
  p.add("kitchenCoffeeMachine", r.x + 0.7 + (kinds.length - 1) * cab, z, S, { y: cabH });
}

/** A table with chairs on two sides. */
function tableSet(p: Plan, x: number, z: number, table = "tableRound"): void {
  p.add(table, x, z);
  p.add("chairModernCushion", x - 1.0, z, Math.PI / 2);
  p.add("chairModernCushion", x + 1.0, z, -Math.PI / 2);
}

function finish(p: Plan, theme: ThemeId, width: number, depth: number): OfficeDesign {
  return { version: 1, width: round(width), depth: round(depth), floor: themeOf(theme).floor, items: p.items, rooms: p.rooms, theme };
}

// --- the themes ------------------------------------------------------------------------------

/** Corporate HQ: reception, rows of dual-monitor desks, a boardroom, private offices, phone booths, a café. */
function corporate(people: People): OfficeDesign {
  const p = new Plan();
  const rec = p.room("Reception", "reception", 0.8, 0.8, 11, 6.4, "none", "s");
  p.add("plantWall", rec.x + rec.w / 2, rec.z + 0.3);
  p.add("receptionDesk", rec.x + rec.w / 2, rec.z + 2.6);
  p.add("loungeSofa", rec.x + 1.8, rec.z + 5.0);
  p.add("loungeSofa", rec.x + rec.w - 1.8, rec.z + 5.0);
  p.add("pottedPlant", rec.x + 0.5, rec.z + 0.6);
  p.add("pottedPlant", rec.x + rec.w - 0.5, rec.z + 0.6);
  const area = workArea(p, people, { kind: "workstationDual", style: "rows", cols: 5, x0: 1.8, z0: 9.4 });
  const wingX = Math.max(1.8 + area.w, rec.x + rec.w) + 2.8;
  const board = p.room("Boardroom", "meeting", wingX, 0.8, 10, 7.4, "solid", "w");
  p.add(people.length > 6 ? "conference8" : "conference6", board.x + board.w / 2, board.z + board.d / 2 + 0.3);
  p.add("dashboardWall", board.x + board.w / 2, board.z + 0.35);
  p.add("pottedPlant", board.x + board.w - 0.6, board.z + board.d - 0.6);
  ["Corner office", "Director's office"].forEach((name, i) => {
    const r = p.room(name, "focus", wingX + i * 5.2, 10, 4.8, 4.8, "glass", "s");
    p.add("desk", r.x + r.w / 2, r.z + 1.5, S);
    p.add("chairDesk", r.x + r.w / 2, r.z + 2.5, S);
    p.add("bookcaseClosedWide", r.x + 1.1, r.z + 0.35);
    p.add("pottedPlant", r.x + r.w - 0.5, r.z + 0.5);
    p.add("loungeChair", r.x + r.w - 1.0, r.z + r.d - 1.0);
  });
  for (let i = 0; i < 3; i++) p.add("phoneBooth", wingX + 1.0 + i * 1.6, 16.6);
  const cafe = p.room("Café", "kitchen", wingX, 18.6, 10.4, 5.6, "none", "n");
  kitchenCounter(p, cafe);
  tableSet(p, cafe.x + 2.2, cafe.z + 1.6);
  tableSet(p, cafe.x + 6.4, cafe.z + 1.6);
  const width = wingX + 10.4 + 1.6;
  return finish(p, "corporate", width, Math.max(9.4 + area.d + 1.4, cafe.z + cafe.d + 1.2));
}

/** Tech campus: long shared benches with laptops, a hangout, huddle rooms, a café bar, standing tables. */
function tech(people: People): OfficeDesign {
  const p = new Plan();
  p.add("taskboard", 6, 0.25);
  p.add("plantWall", 11.5, 0.3);
  const area = workArea(p, people, { kind: "workstationLaptop", style: "pods", cols: 3, x0: 2.2, z0: 3.2 });
  const wingX = Math.max(2.2 + area.w, 14) + 3.2;
  const hang = p.room("Hangout", "lounge", wingX, 0.8, 10.5, 8, "none", "w");
  p.add("rugRound", hang.x + 4.6, hang.z + 4.2);
  p.add("loungeDesignSofaCorner", hang.x + 2.4, hang.z + 2.2);
  p.add("loungeChairRelax", hang.x + 6.8, hang.z + 3.0, -Math.PI / 2);
  p.add("loungeChairRelax", hang.x + 6.8, hang.z + 5.4, -Math.PI / 2);
  p.add("tableCoffeeGlass", hang.x + 4.4, hang.z + 4.2);
  p.add("cabinetTelevision", hang.x + hang.w - 0.6, hang.z + 4.2, -Math.PI / 2);
  p.add("televisionModern", hang.x + hang.w - 0.6, hang.z + 4.2, -Math.PI / 2, { y: footprint("furniture/cabinetTelevision").h });
  p.add("speaker", hang.x + hang.w - 0.6, hang.z + 2.4, -Math.PI / 2);
  p.add("pottedPlant", hang.x + 0.6, hang.z + hang.d - 0.6);
  ["Huddle A", "Huddle B"].forEach((name, i) => {
    const r = p.room(name, "meeting", wingX + i * 5.4, 10.4, 5, 5, "glass", "s");
    p.add("huddleTable", r.x + r.w / 2, r.z + r.d / 2 - 0.2);
    p.add("whiteboard", r.x + r.w / 2, r.z + 0.3);
  });
  p.add("standingTable", wingX + 1.6, 17.4);
  p.add("standingTable", wingX + 4.6, 17.4);
  p.add("phoneBooth", wingX + 7.6, 17.4);
  p.add("phoneBooth", wingX + 9.2, 17.4);
  const cafe = p.room("Café bar", "kitchen", wingX, 19.4, 10.8, 5.4, "none", "n");
  kitchenCounter(p, cafe, ["kitchenFridge", "kitchenCabinet", "kitchenSink", "kitchenCabinet", "kitchenCabinetDrawer"]);
  p.add("kitchenBar", cafe.x + 4.2, cafe.z + 1.6);
  p.add("kitchenBarEnd", cafe.x + 4.2 + footprint("furniture/kitchenBar").w, cafe.z + 1.6);
  p.add("stoolBar", cafe.x + 4.0, cafe.z + 0.8);
  p.add("stoolBar", cafe.x + 4.9, cafe.z + 0.8);
  tableSet(p, cafe.x + 8.8, cafe.z + 1.6, "tableCoffeeSquare");
  for (const [x, z] of [[0.7, 0.7], [wingX - 1.4, 0.7]]) p.add("pottedPlant", x, z);
  return finish(p, "tech", wingX + 10.8 + 1.6, Math.max(3.2 + area.d + 1.2, cafe.z + cafe.d + 1.2));
}

/** Operations center: rows facing a wall of dashboards, a supervisor console, a server room, a war room. */
function operations(people: People): OfficeDesign {
  const p = new Plan();
  const area = workArea(p, people, { kind: "workstationDual", style: "rows", cols: 4, x0: 1.8, z0: 3.0 });
  const screens = Math.max(1, Math.round((area.w + 0.4) / 4.5));
  for (let i = 0; i < screens; i++) p.add("dashboardWall", 1.8 + area.w / 2 + (i - (screens - 1) / 2) * 4.5, 0.35);
  const consoleZ = 3.0 + area.d + 1.4;
  p.add("nocConsole", 1.8 + area.w / 2, consoleZ);
  const wingX = Math.max(1.8 + area.w, 9) + 3;
  const server = p.room("Server room", "custom", wingX, 0.8, 8, 6.4, "solid", "w");
  p.add("serverRow", server.x + server.w / 2, server.z + 1.6);
  p.add("serverRow", server.x + server.w / 2, server.z + 4.4, S);
  const war = p.room("War room", "meeting", wingX, 8.6, 8, 7, "glass", "w");
  p.add("conference6", war.x + war.w / 2, war.z + war.d / 2 + 0.4);
  p.add("dashboardWall", war.x + war.w / 2, war.z + 0.35);
  const brk = p.room("Break room", "kitchen", wingX, 17.6, 8, 5, "none", "w");
  kitchenCounter(p, brk, ["kitchenFridge", "kitchenCabinet", "kitchenSink", "kitchenCabinet"]);
  p.add("loungeSofa", brk.x + brk.w / 2, brk.z + 1.2);
  return finish(p, "operations", wingX + 8 + 1.6, Math.max(consoleZ + 2.6, brk.z + brk.d + 1.2));
}

/** Industrial: a production floor with machines and a conveyor line, the site office, a loading bay. */
function industrial(people: People): OfficeDesign {
  const p = new Plan();
  const floor = p.room("Production floor", "custom", 0.8, 0.8, 22, 10.5, "none", "s");
  const lineZ = floor.z + 5;
  const conv = p.size("factory:conveyor-long")[0];
  p.add("factory:machine", floor.x + 2.2, lineZ, Math.PI / 2);
  for (let i = 0; i < 5; i++) p.add("factory:conveyor-long", floor.x + 4 + conv / 2 + i * conv, lineZ, Math.PI / 2);
  p.add("factory:machine-window", floor.x + 4 + 5 * conv + 1.6, lineZ, Math.PI / 2);
  p.add("factory:robot-arm-a", floor.x + 6.2, lineZ - 2.2);
  p.add("factory:robot-arm-b", floor.x + 10.2, lineZ + 2.2);
  p.add("factory:scanner-high", floor.x + 12.4, lineZ - 2.2);
  for (let i = 0; i < 3; i++) p.add("factory:box-large", floor.x + floor.w - 1.2, floor.z + 1.2 + i * 1.3);
  p.add("factory:box-wide", floor.x + floor.w - 2.8, floor.z + 1.4);
  p.add("factory:hopper-round", floor.x + 1.4, floor.z + 1.4);
  p.add("factory:crane", floor.x + floor.w - 2.0, floor.z + floor.d - 1.6);
  p.add("factory:indicator-special-lines", floor.x + 9, floor.z + floor.d - 0.6);
  for (let i = 0; i < 4; i++) p.add("factory:cone", floor.x + 3 + i * 4.5, floor.z + floor.d - 0.5);
  const area = workArea(p, people, { kind: "workstation", style: "rows", cols: 4, x0: 2.2, z0: 14.2 });
  const wingX = Math.max(2.2 + area.w, 10) + 3;
  const meet = p.room("Site meeting room", "meeting", wingX, 13.4, 8, 6.4, "glass", "w");
  p.add("conference6", meet.x + meet.w / 2, meet.z + meet.d / 2 + 0.3);
  p.add("whiteboard", meet.x + meet.w / 2, meet.z + 0.3);
  const brk = p.room("Break room", "kitchen", wingX, 21, 8, 5, "none", "w");
  kitchenCounter(p, brk, ["kitchenFridgeLarge", "kitchenCabinet", "kitchenSink", "kitchenCabinet"]);
  p.add("bench", brk.x + 2.4, brk.z + 1.3);
  p.add("table", brk.x + 2.4, brk.z + 2.3);
  const bayX = Math.max(floor.x + floor.w, wingX + 8) + 1.6;
  const bay = p.room("Loading bay", "custom", bayX, 0.8, 9, 14, "none", "w");
  p.add("cars:truck", bay.x + 3, bay.z + 4, Math.PI / 2);
  p.add("cars:delivery", bay.x + 3, bay.z + 10, Math.PI / 2);
  for (let i = 0; i < 4; i++) p.add("factory:box-small", bay.x + 7.4, bay.z + 3 + i * 1.1);
  return finish(p, "industrial", bayX + 9 + 1.2, Math.max(14.2 + area.d + 1.2, brk.z + brk.d + 1.2));
}

/** Research lab: long benches, an experiment bay, a server room and a seminar room. */
function lab(people: People): OfficeDesign {
  const p = new Plan();
  p.add("taskboard", 5.5, 0.25);
  const area = workArea(p, people, { kind: "workstationDual", style: "pods", cols: 3, x0: 2.2, z0: 3.2 });
  const wingX = Math.max(2.2 + area.w, 12) + 3;
  const bay = p.room("Experiment bay", "custom", wingX, 0.8, 10.5, 7.4, "glass", "w");
  for (let i = 0; i < 3; i++) {
    const x = bay.x + 2 + i * 3.2;
    p.add("table", x, bay.z + 1.4);
    p.add("computerScreen", x, bay.z + 1.2, 0, { y: footprint("furniture/table").h });
  }
  p.add("factory:scanner-high", bay.x + 2.2, bay.z + 4.8);
  p.add("factory:scanner-low", bay.x + 5.2, bay.z + 4.8);
  p.add("factory:pipe-glass-large-long", bay.x + 8.6, bay.z + 4.8);
  const server = p.room("Server room", "custom", wingX, 9.8, 5, 5.4, "solid", "w");
  for (let i = 0; i < 4; i++) p.add("serverRack", server.x + 1 + i * 1.0, server.z + 1.2);
  const sem = p.room("Seminar room", "meeting", wingX + 5.6, 9.8, 5, 5.4, "glass", "s");
  p.add("conference4", sem.x + sem.w / 2, sem.z + sem.d / 2 + 0.3);
  p.add("whiteboard", sem.x + sem.w / 2, sem.z + 0.3);
  const k = p.room("Kitchen", "kitchen", wingX, 17.2, 10.5, 5, "none", "n");
  kitchenCounter(p, k);
  tableSet(p, k.x + 7.6, k.z + 1.6);
  return finish(p, "lab", wingX + 10.5 + 1.6, Math.max(3.2 + area.d + 1.2, k.z + k.d + 1.2));
}

/** Space station: pods under a command deck, an airlock with laser gates, crew quarters and a galley. */
function station(people: People): OfficeDesign {
  const p = new Plan();
  const [panelW, panelD] = p.size("space:template-wall-detail-a");
  for (let i = 0; i < 4; i++) p.add("space:template-wall-detail-a", 1.6 + panelW / 2 + i * (panelW + 0.1), panelD / 2 + 0.05);
  const area = workArea(p, people, { kind: "workstationDual", style: "pods", cols: 2, x0: 2.2, z0: Math.max(3.4, panelD + 1.8) });
  const wingX = Math.max(2.2 + area.w, 11) + 3;
  const cmd = p.room("Command deck", "meeting", wingX, 0.8, 11, 8, "glass", "w");
  p.add("dashboardWall", cmd.x + 3, cmd.z + 0.35);
  p.add("dashboardWall", cmd.x + 7.8, cmd.z + 0.35);
  p.add("conference6", cmd.x + cmd.w / 2, cmd.z + 4.8);
  const lock = p.room("Airlock", "custom", wingX, 10.4, 11, 6, "none", "w");
  p.add("space:gate-lasers", lock.x + 3, lock.z + 3);
  p.add("space:gate-door", lock.x + 8, lock.z + 3);
  p.add("space:cables", lock.x + 5.5, lock.z + 5.2);
  const crew = p.room("Crew quarters", "lounge", wingX, 18, 11, 6.4, "none", "w");
  p.add("rugRounded", crew.x + 4, crew.z + 3.2);
  p.add("loungeSofaLong", crew.x + 4, crew.z + 1.4);
  p.add("loungeChair", crew.x + 1.6, crew.z + 3.4, Math.PI / 2);
  p.add("tableCoffeeGlass", crew.x + 4, crew.z + 3.2);
  p.add("plantWall", crew.x + 8.8, crew.z + 0.3);
  const galley = p.room("Galley", "kitchen", 0.8, 3.4 + area.d + 1.6, Math.max(9, area.w + 2.8), 4.8, "none", "n");
  kitchenCounter(p, galley);
  return finish(p, "station", wingX + 11 + 1.6, Math.max(galley.z + galley.d + 1.2, crew.z + crew.d + 1.2));
}

const LAYOUTS: Partial<Record<ThemeId, (people: People) => OfficeDesign>> = { corporate, tech, operations, industrial, lab, station };

/** The office for a theme, or undefined for the classic one (design.ts builds that). */
export function themedLayout(people: People, theme: string | undefined): OfficeDesign | undefined {
  const build = LAYOUTS[theme as ThemeId];
  return build ? build(people) : undefined;
}
