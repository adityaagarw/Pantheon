/**
 * Environment themes: the look of the whole space (floor, walls, glass, signs,
 * room tints, light and sky). Each also has its own floor plan and furniture
 * (layouts.ts), used when an office is generated or rebuilt in that theme.
 */

import type { FloorStyle, RoomType } from "./design";

export type ThemeId = "classic" | "corporate" | "tech" | "operations" | "industrial" | "lab" | "station";

export interface Theme {
  id: ThemeId;
  label: string;
  description: string;
  floor: FloorStyle; // suggested floor when the theme is picked
  sky: string; // background
  hemi: [string, string, number]; // sky color, ground color, intensity
  sun: [string, number]; // color, intensity
  ambient: number;
  wall: string; // solid room walls
  glass: string;
  trim: string; // glass frames, skirting
  sign: { bg: string; fg: string; rule: string };
  rooms?: Partial<Record<RoomType, string>>; // room floor tints
}

export const THEMES: Record<ThemeId, Theme> = {
  classic: {
    id: "classic",
    label: "Classic studio",
    description: "Warm wood, bright and friendly",
    floor: "oak",
    sky: "#dfe5ec",
    hemi: ["#ffffff", "#b9a88f", 1.15],
    sun: ["#fff3df", 2.1],
    ambient: 0.25,
    wall: "#eef0f2",
    glass: "#bcd7ee",
    trim: "#c3cad4",
    sign: { bg: "#1f2733", fg: "#f3eee4", rule: "#c9a45c" },
  },
  corporate: {
    id: "corporate",
    label: "Corporate HQ",
    description: "Dark slate, glass and navy: a serious enterprise floor",
    floor: "slate",
    sky: "#cfd6df",
    hemi: ["#f4f7fb", "#5d6878", 1.05],
    sun: ["#f6f8ff", 2.0],
    ambient: 0.3,
    wall: "#e4e8ee",
    glass: "#9fc0dd",
    trim: "#2c3646",
    sign: { bg: "#0f1d33", fg: "#e9eef6", rule: "#5c8fd6" },
    rooms: { meeting: "#a9bad1", lounge: "#b9c4d2", kitchen: "#c7ced8", reception: "#9fb0c8", focus: "#b4bfcd" },
  },
  tech: {
    id: "tech",
    label: "Tech campus",
    description: "Polished concrete, white and violet accents",
    floor: "polished",
    sky: "#e8ebf2",
    hemi: ["#ffffff", "#a9a4c2", 1.15],
    sun: ["#ffffff", 2.0],
    ambient: 0.3,
    wall: "#f6f6f9",
    glass: "#c6d4f2",
    trim: "#8b7cff",
    sign: { bg: "#1b1530", fg: "#ffffff", rule: "#8b7cff" },
    rooms: { meeting: "#cfc8f2", lounge: "#d8d2f5", kitchen: "#e0dcf0", focus: "#d4dbf3" },
  },
  operations: {
    id: "operations",
    label: "Operations center",
    description: "Dim control room: dark floors and glowing screens",
    floor: "slate",
    sky: "#11151d",
    hemi: ["#8ea2c4", "#1a1f29", 0.65],
    sun: ["#c9d6ff", 1.2],
    ambient: 0.35,
    wall: "#2a303b",
    glass: "#5f7fa8",
    trim: "#5cc8ff",
    sign: { bg: "#081221", fg: "#9fe3ff", rule: "#5cc8ff" },
    rooms: { meeting: "#2e3a4f", lounge: "#2a3242", kitchen: "#2c3340", team: "#243049", focus: "#283247", reception: "#26334a" },
  },
  industrial: {
    id: "industrial",
    label: "Industrial",
    description: "Epoxy floors, steel and safety yellow",
    floor: "epoxy",
    sky: "#d6d8d4",
    hemi: ["#f2f2ee", "#6f7366", 1.1],
    sun: ["#fff6e6", 2.0],
    ambient: 0.28,
    wall: "#c9ccc6",
    glass: "#aac3c9",
    trim: "#e0b23a",
    sign: { bg: "#232526", fg: "#f2d16b", rule: "#e0b23a" },
    rooms: { meeting: "#b8bcb0", lounge: "#c2c4ba", kitchen: "#cfd0c8" },
  },
  lab: {
    id: "lab",
    label: "Research lab",
    description: "Clinical white tile, teal glass and cool light",
    floor: "tile",
    sky: "#e7f0f2",
    hemi: ["#ffffff", "#9ab7bb", 1.2],
    sun: ["#f2fbff", 2.0],
    ambient: 0.3,
    wall: "#f7fafb",
    glass: "#9fd8d8",
    trim: "#3fb5b0",
    sign: { bg: "#0f2a2c", fg: "#e8fbfa", rule: "#3fb5b0" },
    rooms: { meeting: "#c4e6e5", lounge: "#d2ecea", kitchen: "#dceeee", focus: "#cfe7ea" },
  },
  station: {
    id: "station",
    label: "Space station",
    description: "Command deck, airlock and crew quarters under cold blue light",
    floor: "slate",
    sky: "#05070d",
    hemi: ["#9fb6ff", "#0d1220", 0.7],
    sun: ["#cfe0ff", 1.3],
    ambient: 0.35,
    wall: "#232a3a",
    glass: "#4d7cc4",
    trim: "#7aa2ff",
    sign: { bg: "#070b16", fg: "#cfe0ff", rule: "#7aa2ff" },
    rooms: { meeting: "#2a3554", lounge: "#262f47", kitchen: "#283149", team: "#212b44", custom: "#1f2740" },
  },
};

export const THEME_IDS = Object.keys(THEMES) as ThemeId[];

export function themeOf(id: string | undefined | null): Theme {
  return THEMES[(id as ThemeId) ?? "classic"] ?? THEMES.classic;
}
