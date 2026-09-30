"use client";

/**
 * Runtime 3D assets (built by Zeus, imported from the web, or shipped by
 * plugins). Loaded once per page and registered into the catalog; `version`
 * bumps whenever the set changes so scenes can re-resolve their designs.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { api } from "@/lib/api";
import type { AssetInfo } from "@/lib/types";
import { registerAssets } from "./catalog";

interface AssetStore {
  list: AssetInfo[];
  version: number;
  loading: boolean;
  load: (force?: boolean) => Promise<void>;
}

export const useAssets = create<AssetStore>((set, get) => ({
  list: [],
  version: 0,
  loading: false,
  load: async (force = false) => {
    if (get().loading || (get().version > 0 && !force)) return;
    set({ loading: true });
    try {
      const list = await api.assets();
      registerAssets(list);
      set((s) => ({ list, version: s.version + 1 }));
    } catch {
      /* the office works without custom assets */
    } finally {
      set({ loading: false });
    }
  },
}));

/** Load runtime assets; returns a version that changes when they do. */
export function useAssetVersion(): number {
  const version = useAssets((s) => s.version);
  const load = useAssets((s) => s.load);
  useEffect(() => {
    void load();
  }, [load]);
  return version;
}
