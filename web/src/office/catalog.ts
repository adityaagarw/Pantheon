/**
 * Placeable office items. Kenney models face +z; an item's `rot` rotates it
 * around y, so its front points along (sin rot, cos rot). Positions are the
 * item's footprint center, in meters.
 */

import type { AssetInfo, PrimitivePart } from "@/lib/types";
import { footprint, registerModel } from "./models";

export type Category =
  | "Workstations"
  | "Meeting"
  | "Seating"
  | "Tables"
  | "Lounge"
  | "Kitchen"
  | "Storage"
  | "Tech"
  | "Decor"
  | "Walls"
  | "Props"
  | "Custom";

export interface Part {
  model: string;
  x: number;
  z: number;
  y?: number;
  rot?: number;
}

export interface Seat {
  x: number;
  z: number;
  facing: number; // rotation of the sitter (0 = facing +z)
}

export interface CatalogEntry {
  key: string;
  label: string;
  category: Category;
  parts: Part[];
  size: [number, number]; // footprint w x d (meters, local frame)
  blocking: boolean;
  seats: Seat[];
  workstation?: boolean; // assignable desk (sitter = seats[0])
  screen?: [number, number, number]; // local position of the monitor (status glow)
  thumb: string;
  special?: "taskboard" | "whiteboard";
  procedural?: PrimitivePart[]; // runtime assets built from primitives
  asset?: AssetInfo; // runtime assets: where they came from
}

const M = (name: string) => `furniture/${name}`;
const label = (name: string) =>
  name
    .replace(/([a-z])([A-Z0-9])/g, "$1 $2")
    .replace(/^./, (c) => c.toUpperCase());

function single(name: string, category: Category, opts: Partial<CatalogEntry> = {}): CatalogEntry {
  const fp = footprint(M(name));
  return {
    key: name,
    label: label(name),
    category,
    parts: [{ model: M(name), x: 0, z: 0 }],
    size: [fp.w, fp.d],
    blocking: true,
    seats: [],
    thumb: `/thumbs/furniture/${name}.png`,
    ...opts,
  };
}

const chairSeat = (): Seat[] => [{ x: 0, z: 0.05, facing: 0 }];
const sofaSeats = (n: number, w: number): Seat[] =>
  Array.from({ length: n }, (_, i) => ({ x: (i - (n - 1) / 2) * (w / n), z: 0.12, facing: 0 }));

// --- composites -------------------------------------------------------------------

const deskFp = footprint(M("desk"));
const workstation = (key: string, labelText: string, screen: "monitor" | "laptop"): CatalogEntry => ({
  key,
  label: labelText,
  category: "Workstations",
  parts: [
    { model: M("desk"), x: 0, z: -0.35 },
    { model: M("chairDesk"), x: 0, z: 0.62, rot: Math.PI },
    ...(screen === "monitor"
      ? [
          { model: M("computerScreen"), x: 0, z: -0.62, y: deskFp.h },
          { model: M("computerKeyboard"), x: -0.05, z: -0.22, y: deskFp.h },
          { model: M("computerMouse"), x: 0.42, z: -0.22, y: deskFp.h },
        ]
      : [{ model: M("laptop"), x: 0, z: -0.35, y: deskFp.h, rot: Math.PI }]),
    { model: M("lampSquareTable"), x: -0.55, z: -0.6, y: deskFp.h },
  ],
  size: [deskFp.w, deskFp.d + 0.95],
  blocking: true,
  seats: [{ x: 0, z: 0.62, facing: Math.PI }],
  workstation: true,
  screen: [0, deskFp.h + 0.3, -0.58],
  thumb: `/thumbs/furniture/desk.png`,
});

const tableFp = footprint(M("table"));
const conference = (n: 4 | 6 | 8): CatalogEntry => {
  const perSide = n / 2;
  const tables = Math.ceil(perSide / 2);
  const len = tableFp.w * tables;
  const parts: Part[] = [];
  for (let i = 0; i < tables; i++) parts.push({ model: M("table"), x: (i - (tables - 1) / 2) * tableFp.w, z: 0 });
  const seats: Seat[] = [];
  for (let i = 0; i < perSide; i++) {
    const x = (i - (perSide - 1) / 2) * (len / perSide);
    parts.push({ model: M("chairCushion"), x, z: -tableFp.d / 2 - 0.35, rot: 0 });
    parts.push({ model: M("chairCushion"), x, z: tableFp.d / 2 + 0.35, rot: Math.PI });
    seats.push({ x, z: -tableFp.d / 2 - 0.35, facing: 0 });
    seats.push({ x, z: tableFp.d / 2 + 0.35, facing: Math.PI });
  }
  return {
    key: `conference${n}`,
    label: `Conference table (${n} seats)`,
    category: "Meeting",
    parts,
    size: [len + 0.2, tableFp.d + 1.4],
    blocking: true,
    seats,
    thumb: `/thumbs/furniture/table.png`,
  };
};

const roundFp = footprint(M("tableRound"));
const huddle: CatalogEntry = {
  key: "huddleTable",
  label: "Huddle table (4 seats)",
  category: "Meeting",
  parts: [
    { model: M("tableRound"), x: 0, z: 0 },
    ...[0, 1, 2, 3].map((i) => {
      const a = (i * Math.PI) / 2;
      const r = roundFp.w / 2 + 0.35;
      return { model: M("chairModernCushion"), x: Math.sin(a) * r, z: Math.cos(a) * r, rot: a + Math.PI };
    }),
  ],
  size: [roundFp.w + 1.3, roundFp.d + 1.3],
  blocking: true,
  seats: [0, 1, 2, 3].map((i) => {
    const a = (i * Math.PI) / 2;
    const r = roundFp.w / 2 + 0.35;
    return { x: Math.sin(a) * r, z: Math.cos(a) * r, facing: a + Math.PI };
  }),
  thumb: `/thumbs/furniture/tableRound.png`,
};

const taskboard: CatalogEntry = {
  key: "taskboard",
  label: "Task board (live)",
  category: "Tech",
  parts: [],
  size: [4, 0.15],
  blocking: true,
  seats: [],
  thumb: `/thumbs/furniture/paneling.png`,
  special: "taskboard",
};

const whiteboard: CatalogEntry = {
  key: "whiteboard",
  label: "Whiteboard (live)",
  category: "Tech",
  parts: [],
  size: [2.6, 0.5],
  blocking: true,
  seats: [],
  thumb: `/thumbs/furniture/paneling.png`,
  special: "whiteboard",
};

// --- the catalog ---------------------------------------------------------------------

const ENTRIES: CatalogEntry[] = [
  workstation("workstation", "Workstation (monitor)", "monitor"),
  workstation("workstationLaptop", "Workstation (laptop)", "laptop"),
  conference(4),
  conference(6),
  conference(8),
  huddle,
  taskboard,
  whiteboard,
  ...["chair", "chairCushion", "chairDesk", "chairModernCushion", "chairModernFrameCushion", "chairRounded", "stoolBar", "stoolBarSquare"].map(
    (n) => single(n, "Seating", { seats: chairSeat() }),
  ),
  single("bench", "Seating", { seats: sofaSeats(2, 1.4) }),
  single("benchCushion", "Seating", { seats: sofaSeats(2, 1.4) }),
  single("loungeChair", "Lounge", { seats: chairSeat() }),
  single("loungeChairRelax", "Lounge", { seats: chairSeat() }),
  single("loungeDesignChair", "Lounge", { seats: chairSeat() }),
  single("loungeSofa", "Lounge", { seats: sofaSeats(2, 1.8) }),
  single("loungeSofaLong", "Lounge", { seats: sofaSeats(3, 2.4) }),
  single("loungeDesignSofa", "Lounge", { seats: sofaSeats(2, 1.8) }),
  single("loungeSofaCorner", "Lounge", { seats: sofaSeats(2, 1.6) }),
  single("loungeDesignSofaCorner", "Lounge", { seats: sofaSeats(2, 1.6) }),
  single("loungeSofaOttoman", "Lounge"),
  ...["table", "tableRound", "tableGlass", "tableCross", "tableCloth", "tableCoffee", "tableCoffeeGlass", "tableCoffeeSquare", "tableCoffeeGlassSquare", "sideTable", "sideTableDrawers", "desk", "deskCorner"].map(
    (n) => single(n, "Tables"),
  ),
  ...["kitchenBar", "kitchenBarEnd", "kitchenCabinet", "kitchenCabinetDrawer", "kitchenCabinetCornerInner", "kitchenCabinetCornerRound", "kitchenSink", "kitchenStove", "kitchenFridge", "kitchenFridgeLarge", "kitchenFridgeSmall", "kitchenCoffeeMachine", "kitchenMicrowave", "kitchenBlender", "toaster", "trashcan"].map(
    (n) => single(n, "Kitchen"),
  ),
  ...["bookcaseOpen", "bookcaseOpenLow", "bookcaseClosed", "bookcaseClosedDoors", "bookcaseClosedWide", "cabinetTelevision", "cabinetTelevisionDoors", "cardboardBoxClosed", "cardboardBoxOpen", "coatRackStanding", "books"].map(
    (n) => single(n, "Storage"),
  ),
  ...["computerScreen", "laptop", "televisionModern", "televisionVintage", "speaker", "speakerSmall", "radio"].map((n) =>
    single(n, "Tech"),
  ),
  ...["pottedPlant", "plantSmall1", "plantSmall2", "plantSmall3", "lampRoundFloor", "lampSquareFloor", "lampRoundTable", "lampSquareTable", "bear"].map(
    (n) => single(n, "Decor"),
  ),
  ...["rugRectangle", "rugRound", "rugRounded", "rugSquare", "rugDoormat"].map((n) => single(n, "Decor", { blocking: false })),
  ...["wall", "wallWindow", "wallWindowSlide", "wallHalf", "wallDoorway", "wallDoorwayWide", "paneling"].map((n) =>
    single(n, "Walls", n.startsWith("wallDoorway") ? { blocking: false } : {}),
  ),
];

export const CATALOG: Record<string, CatalogEntry> = Object.fromEntries(ENTRIES.map((e) => [e.key, e]));
export const CATEGORIES: Category[] = ["Workstations", "Meeting", "Seating", "Tables", "Lounge", "Kitchen", "Storage", "Tech", "Decor", "Walls", "Props", "Custom"];

/**
 * Add runtime assets (built by Zeus, imported, or from plugins) to the catalog
 * so designs, the designer and world objects can use them like furniture.
 */
export function registerAssets(assets: AssetInfo[]): void {
  for (const a of assets) {
    const glb = a.kind === "glb" && a.url && a.bounds;
    if (glb) registerModel(a.key, { url: a.url!, min: a.bounds!.min, max: a.bounds!.max, scale: a.scale });
    CATALOG[a.key] = {
      key: a.key,
      label: a.label,
      category: (CATEGORIES as string[]).includes(a.category) ? (a.category as Category) : "Props",
      parts: glb ? [{ model: a.key, x: 0, z: 0 }] : [],
      size: [Math.max(0.05, a.size[0]), Math.max(0.05, a.size[1])],
      blocking: a.blocking,
      seats: [],
      thumb: "",
      procedural: a.kind === "procedural" ? a.parts : undefined,
      asset: a,
    };
  }
}

export function entry(kind: string): CatalogEntry | undefined {
  return CATALOG[kind];
}

/** Local -> world transform of a point for an item at (x, z, rot). */
export function toWorld(item: { x: number; z: number; rot: number }, lx: number, lz: number): [number, number] {
  const c = Math.cos(item.rot);
  const s = Math.sin(item.rot);
  return [item.x + lx * c + lz * s, item.z - lx * s + lz * c];
}

/** Axis-aligned bounds of an item's rotated footprint. */
export function itemBounds(item: { kind: string; x: number; z: number; rot: number }) {
  const e = CATALOG[item.kind];
  const [w, d] = e?.size ?? [0.5, 0.5];
  const c = Math.abs(Math.cos(item.rot));
  const s = Math.abs(Math.sin(item.rot));
  const W = w * c + d * s;
  const D = w * s + d * c;
  return { x: item.x - W / 2, z: item.z - D / 2, w: W, d: D };
}
