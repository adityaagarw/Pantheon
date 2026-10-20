/**
 * Environment themes: the look of the whole space (floor, walls, glass, signs, room
 * tints, light and sky). They come from packs (registry.ts; the original seven are the
 * office-classic pack). A theme with a floor plan of its own in layouts.ts gets that
 * plan when an office is generated or rebuilt in it; any other theme gets the classic one.
 */

import type { FloorStyle, RoomType } from "./design";

export type ThemeId = string;

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

/** The classic look, for the moment before the packs load and for anything a pack's
 * theme leaves out. */
const FALLBACK: Theme = {
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
};

export const THEMES: Record<ThemeId, Theme> = {};

export const themeIds = (): ThemeId[] => Object.keys(THEMES);

export function themeOf(id: string | undefined | null): Theme {
  return { ...FALLBACK, ...(THEMES[id ?? "classic"] ?? THEMES.classic) };
}
