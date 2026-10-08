/**
 * Placeable office items. Kenney models face +z; an item's `rot` rotates it
 * around y, so its front points along (sin rot, cos rot). Positions are the
 * item's footprint center, in meters.
 */

import type { AssetInfo, PrimitivePart } from "@/lib/types";
import { footprint, kitModels, registerModel } from "./models";

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
  | "Industrial"
  | "Sci-fi"
  | "Outdoor"
  | "Vehicles"
  | "Home"
  | "Nature"
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

/** An item from one of the extra CC0 Kenney kits (factory, space, city, cars). */
function kit(dir: string, name: string, category: Category, opts: Partial<CatalogEntry> = {}): CatalogEntry {
  const model = `${dir}/${name}`;
  const fp = footprint(model);
  return {
    key: `${dir}:${name}`,
    label: label(name.replace(/[-_]/g, " ").replace(/ ([a-z])/g, (_, c: string) => c.toUpperCase())),
    category,
    parts: [{ model, x: 0, z: 0 }],
    size: [fp.w, fp.d],
    blocking: true,
    seats: [],
    thumb: `/thumbs/${dir}/${name}.png`,
    ...opts,
  };
}
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

// --- enterprise and tech props (built from primitives) --------------------------------

type Opts = { emissive?: string; opacity?: number };
const box = (size: [number, number, number], pos: [number, number, number], color: string, o: Opts = {}): PrimitivePart => ({
  shape: "box",
  size,
  pos,
  rot: [0, 0, 0],
  color,
  ...o,
});
const cyl = (size: [number, number, number], pos: [number, number, number], color: string, o: Opts = {}): PrimitivePart => ({
  shape: "cylinder",
  size,
  pos,
  rot: [0, 0, 0],
  color,
  ...o,
});

/** One 42U-style rack: dark cabinet, rows of drive bays with status lights. */
function rackParts(x = 0): PrimitivePart[] {
  const parts = [box([0.62, 2.05, 1.0], [x, 1.025, 0], "#1b1f27"), box([0.56, 1.9, 0.02], [x, 1.03, 0.505], "#262b35")];
  for (let i = 0; i < 9; i++) {
    const y = 0.25 + i * 0.2;
    parts.push(box([0.5, 0.13, 0.02], [x, y, 0.52], "#323844"));
    parts.push(box([0.04, 0.025, 0.01], [x - 0.19, y, 0.535], "#3ecf8e", { emissive: i % 3 === 2 ? "#f5b454" : "#3ecf8e" }));
    parts.push(box([0.04, 0.025, 0.01], [x - 0.13, y, 0.535], "#5cc8ff", { emissive: "#5cc8ff" }));
  }
  return parts;
}

/** A panel of glowing "charts" for dashboard screens. */
function screenParts(cx: number, cy: number, w: number, h: number, z: number, hue: string): PrimitivePart[] {
  const parts = [box([w, h, 0.02], [cx, cy, z], "#0c1424", { emissive: "#0b1730" })];
  const bars = 6;
  for (let i = 0; i < bars; i++) {
    const bh = h * (0.2 + ((i * 37) % 60) / 100);
    parts.push(box([w / (bars * 2), bh, 0.01], [cx - w / 2 + (w / bars) * (i + 0.5), cy - h / 2 + bh / 2 + h * 0.08, z + 0.015], hue, { emissive: hue }));
  }
  parts.push(box([w * 0.8, 0.02, 0.01], [cx, cy + h * 0.38, z + 0.015], "#e1e6f0", { emissive: "#9fb3d1" }));
  return parts;
}

const PROC = (key: string, label: string, category: Category, size: [number, number], procedural: PrimitivePart[], opts: Partial<CatalogEntry> = {}): CatalogEntry => ({
  key,
  label,
  category,
  parts: [],
  size,
  blocking: true,
  seats: [],
  thumb: "",
  procedural,
  ...opts,
});

const dualWorkstation: CatalogEntry = {
  ...workstation("workstationDual", "Workstation (dual monitors)", "monitor"),
  parts: [
    { model: M("desk"), x: 0, z: -0.35 },
    { model: M("chairDesk"), x: 0, z: 0.62, rot: Math.PI },
    { model: M("computerScreen"), x: -0.32, z: -0.62, y: deskFp.h, rot: 0.18 },
    { model: M("computerScreen"), x: 0.32, z: -0.62, y: deskFp.h, rot: -0.18 },
    { model: M("computerKeyboard"), x: -0.05, z: -0.22, y: deskFp.h },
    { model: M("computerMouse"), x: 0.42, z: -0.22, y: deskFp.h },
  ],
};

const ENTERPRISE: CatalogEntry[] = [
  dualWorkstation,
  PROC("serverRack", "Server rack", "Tech", [0.62, 1.0], rackParts()),
  PROC("serverRow", "Server rack row (4)", "Tech", [2.6, 1.0], [-0.975, -0.325, 0.325, 0.975].flatMap((x) => rackParts(x))),
  PROC("dashboardWall", "Dashboard wall (3 screens)", "Tech", [4.3, 0.3], [
    box([4.3, 1.5, 0.08], [0, 1.75, 0], "#20252e"),
    ...screenParts(-1.4, 1.75, 1.3, 1.25, 0.05, "#5cc8ff"),
    ...screenParts(0, 1.75, 1.3, 1.25, 0.05, "#3ecf8e"),
    ...screenParts(1.4, 1.75, 1.3, 1.25, 0.05, "#f5b454"),
    box([0.1, 1.0, 0.1], [-1.9, 0.5, -0.05], "#3a404b"),
    box([0.1, 1.0, 0.1], [1.9, 0.5, -0.05], "#3a404b"),
  ]),
  PROC("nocConsole", "Operations console (3 seats)", "Tech", [3.6, 1.7], [
    box([3.6, 0.06, 0.9], [0, 0.76, -0.3], "#2b313c"),
    box([3.5, 0.72, 0.08], [0, 0.37, -0.72], "#20252e"),
    ...[-1.2, 0, 1.2].flatMap((x) => [
      box([0.05, 0.3, 0.05], [x, 0.95, -0.55], "#3a404b"),
      ...screenParts(x - 0.27, 1.25, 0.5, 0.32, -0.52, "#5cc8ff"),
      ...screenParts(x + 0.27, 1.25, 0.5, 0.32, -0.52, "#3ecf8e"),
    ]),
  ], { seats: [-1.2, 0, 1.2].map((x) => ({ x, z: 0.45, facing: Math.PI })) }),
  PROC("receptionDesk", "Reception desk", "Workstations", [2.8, 1.6], [
    box([2.8, 1.08, 0.5], [0, 0.54, 0.25], "#f4f5f7"),
    box([2.9, 0.05, 0.6], [0, 1.1, 0.25], "#2b313c"),
    box([2.4, 0.06, 0.5], [0, 0.74, -0.15], "#d9dde4"),
    box([1.2, 0.18, 0.02], [0, 0.72, 0.51], "#6d5dfc", { emissive: "#6d5dfc" }),
  ], { seats: [{ x: 0, z: -0.5, facing: 0 }] }),
  PROC("phoneBooth", "Phone booth (1 seat)", "Meeting", [1.2, 1.2], [
    box([1.2, 0.08, 1.2], [0, 0.04, 0], "#2b313c"),
    box([1.2, 0.08, 1.2], [0, 2.26, 0], "#2b313c"),
    box([1.2, 2.2, 0.06], [0, 1.15, -0.57], "#3a404b"),
    box([0.06, 2.2, 1.2], [-0.57, 1.15, 0], "#bcd7ee", { opacity: 0.3 }),
    box([0.06, 2.2, 1.2], [0.57, 1.15, 0], "#bcd7ee", { opacity: 0.3 }),
    box([1.2, 2.2, 0.04], [0, 1.15, 0.58], "#bcd7ee", { opacity: 0.22 }),
    box([0.7, 0.45, 0.45], [0, 0.3, -0.25], "#6d5dfc"),
    box([0.8, 0.04, 0.3], [0, 1.0, -0.4], "#d9dde4"),
  ], { seats: [{ x: 0, z: -0.1, facing: 0 }] }),
  PROC("plantWall", "Plant wall", "Decor", [2.4, 0.3], [
    box([2.4, 2.2, 0.12], [0, 1.15, 0], "#3d5a3a"),
    ...Array.from({ length: 24 }, (_, i): PrimitivePart => ({
      shape: "sphere",
      size: [0.38, 0.3, 0.16],
      pos: [-1.05 + (i % 6) * 0.42, 0.35 + Math.floor(i / 6) * 0.52, 0.09],
      rot: [0, 0, 0],
      color: ["#5f9e4f", "#4e8a41", "#76b25f"][i % 3],
    })),
  ]),
  PROC("standingTable", "Standing table", "Meeting", [1.6, 0.8], [
    box([1.6, 0.05, 0.8], [0, 1.05, 0], "#e9e4dc"),
    cyl([0.08, 1.02, 0.08], [0, 0.51, 0], "#3a404b"),
    box([0.7, 0.04, 0.5], [0, 0.02, 0], "#3a404b"),
  ]),
];

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
  ...ENTERPRISE,
  ...[
    "machine", "machine-window", "machine-fortified", "machine-connection-pipe", "machine-bed", "conveyor", "conveyor-long",
    "conveyor-corner", "conveyor-stripe", "conveyor-sides", "box-small", "box-large", "box-long", "box-wide", "crane",
    "crane-lift", "cone", "catwalk-straight", "catwalk-corner", "catwalk-stairs", "hopper-square", "hopper-round",
    "lever-single", "lever-double", "door", "door-wide-closed", "door-wide-open", "pipe-glass-large-long",
    "pipe-glass-large-curve", "robot-arm-a", "robot-arm-b", "scanner-high", "scanner-low",
  ].map((n) => kit("factory", n, "Industrial", n.startsWith("door") ? { blocking: false } : {})),
  ...["indicator-special-area", "indicator-special-lines"].map((n) => kit("factory", n, "Industrial", { blocking: false })),
  ...[
    "gate", "gate-door", "gate-door-window", "gate-lasers", "cables", "stairs", "stairs-wide", "template-wall",
    "template-wall-detail-a", "template-wall-half", "template-wall-corner", "template-detail", "template-corner",
  ].map((n) => kit("space", n, "Sci-fi", n.startsWith("gate") ? { blocking: false } : {})),
  ...["template-floor", "template-floor-detail", "template-floor-detail-a", "template-floor-big", "template-floor-layer-raised"].map(
    (n) => kit("space", n, "Sci-fi", { blocking: false }),
  ),
  ...[
    "building-a", "building-b", "building-c", "building-d", "building-e", "building-f", "building-g", "building-h",
    "building-skyscraper-a", "building-skyscraper-b", "building-skyscraper-c", "building-skyscraper-d", "building-skyscraper-e",
    "detail-awning", "detail-awning-wide", "detail-parasol-a", "detail-parasol-b",
  ].map((n) => kit("city", n, "Outdoor")),
  ...["sedan", "sedan-sports", "suv", "suv-luxury", "hatchback-sports", "van", "taxi", "truck", "delivery", "police", "ambulance", "firetruck"].map(
    (n) => kit("cars", n, "Vehicles"),
  ),
  ...["cone", "box"].map((n) => kit("cars", n, "Outdoor")),
  // Homes: bedrooms, washrooms, laundry (Kenney Furniture Kit, CC0)
  ...["bedSingle", "bedDouble", "bedBunk", "cabinetBed", "cabinetBedDrawer", "cabinetBedDrawerTable", "toilet", "toiletSquare", "shower", "showerRound",
    "bathtub", "bathroomSink", "bathroomSinkSquare", "bathroomCabinet", "bathroomCabinetDrawer", "bathroomMirror", "washer", "dryer", "washerDryerStacked",
    "pillow", "pillowLong", "lampWall", "lampSquareCeiling"].map((n) => single(n, "Home", /^(pillow|lamp|bathroomMirror|bathroomCabinet$)/.test(n) ? { blocking: false } : {})),
  // Nature: trees, rocks, rivers, paths, crops, tents, campfires (Kenney Nature Kit, CC0).
  // Things you walk on or through don't block.
  ...kitModels("nature").map((n) =>
    kit("nature", n, "Nature", /^(ground_|path_|grass|flower|crops?_|lily|hanging_moss|platform_|mushroom|bed_floor|campfire)/.test(n) ? { blocking: false } : {}),
  ),
];

export const CATALOG: Record<string, CatalogEntry> = Object.fromEntries(ENTRIES.map((e) => [e.key, e]));
export const CATEGORIES: Category[] = [
  "Workstations", "Meeting", "Seating", "Tables", "Lounge", "Kitchen", "Storage", "Tech", "Decor", "Walls",
  "Home", "Nature", "Industrial", "Sci-fi", "Outdoor", "Vehicles", "Props", "Custom",
];

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
