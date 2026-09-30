"use client";

import { create } from "zustand";
import type { OfficeDesign, PlacedItem, Room } from "./design";

export type Tool = { kind: "select" } | { kind: "place"; key: string; rot: number } | { kind: "room" };
export type Selection = { type: "item" | "room"; id: string } | null;

interface DesignerState {
  active: boolean;
  draft: OfficeDesign | null;
  selection: Selection;
  tool: Tool;
  history: OfficeDesign[];
  future: OfficeDesign[];
  dirty: boolean;
  topDown: boolean;
  begin: (design: OfficeDesign) => void;
  exit: () => void;
  update: (fn: (d: OfficeDesign) => OfficeDesign, record?: boolean) => void;
  checkpoint: () => void;
  undo: () => void;
  redo: () => void;
  select: (s: Selection) => void;
  setTool: (t: Tool) => void;
  setTopDown: (v: boolean) => void;
  markSaved: () => void;
}

const LIMIT = 100;

export const useDesigner = create<DesignerState>((set, get) => ({
  active: false,
  draft: null,
  selection: null,
  tool: { kind: "select" },
  history: [],
  future: [],
  dirty: false,
  topDown: true,
  begin: (design) =>
    set({ active: true, draft: structuredClone(design), selection: null, tool: { kind: "select" }, history: [], future: [], dirty: false }),
  exit: () => set({ active: false, draft: null, selection: null, history: [], future: [], dirty: false }),
  checkpoint: () => {
    const d = get().draft;
    if (d) set((s) => ({ history: [...s.history.slice(-LIMIT), structuredClone(d)], future: [] }));
  },
  update: (fn, record = true) => {
    const d = get().draft;
    if (!d) return;
    if (record) get().checkpoint();
    set({ draft: fn(d), dirty: true });
  },
  undo: () => {
    const { history, draft } = get();
    if (!history.length || !draft) return;
    set((s) => ({ draft: history[history.length - 1], history: history.slice(0, -1), future: [structuredClone(draft), ...s.future], dirty: true }));
  },
  redo: () => {
    const { future, draft } = get();
    if (!future.length || !draft) return;
    set((s) => ({ draft: future[0], future: future.slice(1), history: [...s.history, structuredClone(draft)], dirty: true }));
  },
  select: (selection) => set({ selection }),
  setTool: (tool) => set({ tool, selection: tool.kind === "select" ? get().selection : null }),
  setTopDown: (topDown) => set({ topDown }),
  markSaved: () => set({ dirty: false }),
}));

export const snap = (v: number, step = 0.25) => Math.round(v / step) * step;

export function patchItem(d: OfficeDesign, id: string, patch: Partial<PlacedItem>): OfficeDesign {
  return { ...d, items: d.items.map((i) => (i.id === id ? { ...i, ...patch } : i)) };
}

export function patchRoom(d: OfficeDesign, id: string, patch: Partial<Room>): OfficeDesign {
  return { ...d, rooms: d.rooms.map((r) => (r.id === id ? { ...r, ...patch } : r)) };
}
