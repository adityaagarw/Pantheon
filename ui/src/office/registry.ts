/**
 * The office's content comes from packs (engine app/packs.py): furniture, the models it's
 * built from, characters and their rigs, and themes. The original office is the
 * office-classic pack; packs the user adds can bring more. Everything is loaded once
 * from the engine and registered here. Each pack's files are under its own `base`, and a
 * later pack's key wins.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { engineHttp } from "@/lib/engine";
import type { PrimitivePart } from "@/lib/types";
import { CATALOG, CATEGORIES, type CatalogEntry } from "./catalog";
import { registerCharacters, type CharacterDef, type RigDef } from "./characters";
import { clearPackModels, registerModel } from "./models";
import { THEMES, type Theme } from "./themes";

export interface OfficeItem {
  key: string;
  label: string;
  category: string;
  parts: { model: string; x: number; z: number; y?: number; rot?: number }[];
  size: [number, number];
  blocking?: boolean;
  seats?: { x: number; z: number; facing: number }[];
  workstation?: boolean;
  screen?: [number, number, number];
  thumb?: string;
  special?: "taskboard" | "whiteboard";
  procedural?: PrimitivePart[];
}

export interface OfficePack {
  id: string;
  name: string;
  /** Where the pack's files are served, e.g. /api/v1/packs/office-classic/files. */
  base: string;
  kits: Record<string, { scale: number }>;
  models: Record<string, { min: number[]; max: number[] }>;
  items: OfficeItem[];
  rigs: Record<string, RigDef>;
  characters: CharacterDef[];
  themes: Theme[];
}

/** Put the packs' content in the catalog, model, character and theme registries. */
export function registerOffice(packs: OfficePack[], origin = ""): void {
  clearPackModels();
  for (const key of Object.keys(CATALOG)) if (!CATALOG[key].asset) delete CATALOG[key];
  const rigs: Record<string, RigDef> = {};
  const characters: CharacterDef[] = [];
  for (const p of packs) {
    const url = (rel: string) => `${origin}${p.base}/${rel}`;
    for (const [key, b] of Object.entries(p.models)) {
      registerModel(key, { url: url(`models/${key}.glb`), min: b.min, max: b.max, scale: p.kits[key.split("/")[0]]?.scale ?? 1 }, true);
    }
    for (const it of p.items) {
      CATALOG[it.key] = {
        key: it.key,
        label: it.label,
        category: it.category,
        parts: it.parts,
        size: it.size,
        blocking: it.blocking !== false,
        seats: it.seats ?? [],
        workstation: it.workstation,
        screen: it.screen,
        thumb: it.thumb ? url(it.thumb) : "",
        special: it.special,
        procedural: it.procedural,
      } satisfies CatalogEntry;
      if (!CATEGORIES.includes(it.category)) CATEGORIES.splice(CATEGORIES.length - 2, 0, it.category);
    }
    for (const [name, rig] of Object.entries(p.rigs)) rigs[`${p.id}/${name}`] = rig;
    for (const c of p.characters) {
      characters.push({ ...c, rig: `${p.id}/${c.rig}`, model: url(c.model), thumb: c.thumb ? url(c.thumb) : undefined });
    }
    for (const t of p.themes) THEMES[t.id] = t;
  }
  registerCharacters(characters, rigs);
}

interface OfficeStore {
  ready: boolean;
  error: string | null;
  /** Bumps whenever the registries change, so designs re-resolve. */
  version: number;
  load: (force?: boolean) => Promise<void>;
}

let inflight: Promise<void> | null = null;

export const useOffice = create<OfficeStore>((set, get) => ({
  ready: false,
  error: null,
  version: 0,
  load: (force = false) => {
    if (get().ready && !force) return Promise.resolve();
    inflight ??= fetch(`${engineHttp()}/api/v1/office`, { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`the office packs didn't load (HTTP ${res.status})`);
        const data = (await res.json()) as { packs: OfficePack[] };
        registerOffice(data.packs, engineHttp());
        set((s) => ({ ready: true, error: null, version: s.version + 1 }));
      })
      .catch((e: Error) => set({ error: e.message }))
      .finally(() => {
        inflight = null;
      });
    return inflight;
  },
}));

/** Load the office packs (once) and say whether they're in. */
export function useOfficeReady(): boolean {
  const ready = useOffice((s) => s.ready);
  const load = useOffice((s) => s.load);
  useEffect(() => {
    void load();
  }, [load]);
  return ready;
}
