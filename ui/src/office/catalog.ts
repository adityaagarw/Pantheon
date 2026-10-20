/**
 * Placeable office items. The furniture comes from packs (registry.ts; the original set
 * is the office-classic pack), and runtime assets (built by Zeus, imported, or from a
 * pack's assets) are added by registerAssets. Kenney models face +z; an item's `rot`
 * rotates it around y, so its front points along (sin rot, cos rot). Positions are the
 * item's footprint center, in meters.
 */

import type { AssetInfo, PrimitivePart } from "@/lib/types";
import { registerModel } from "./models";

/** The designer's groups; a pack can add its own (placed before Props and Custom). */
export type Category = string;

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

export const CATALOG: Record<string, CatalogEntry> = {};
export const CATEGORIES: Category[] = [
  "Workstations", "Meeting", "Seating", "Tables", "Lounge", "Kitchen", "Storage", "Tech", "Decor", "Walls",
  "Home", "Nature", "Industrial", "Sci-fi", "Outdoor", "Vehicles", "Props", "Custom",
];

/**
 * Add runtime assets (built by Zeus, imported, or from packs) to the catalog so designs,
 * the designer and world objects can use them like furniture.
 */
export function registerAssets(assets: AssetInfo[]): void {
  for (const a of assets) {
    const glb = a.kind === "glb" && a.url && a.bounds;
    if (glb) registerModel(a.key, { url: a.url!, min: a.bounds!.min, max: a.bounds!.max, scale: a.scale });
    CATALOG[a.key] = {
      key: a.key,
      label: a.label,
      category: CATEGORIES.includes(a.category) ? a.category : "Props",
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
