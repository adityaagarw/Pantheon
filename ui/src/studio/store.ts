/** The Studio's own state: what's selected, the dock, the command bar and the time cursor. */
import type { DockviewApi } from "dockview-react";
import { create } from "zustand";

export interface StudioState {
  /** The agent in focus (picked in the Office; the Console pane's subject). */
  agentId: string | null;
  dock: DockviewApi | null;
  commandOpen: boolean;
  /** Replay: the moment (ms) the org is shown at, or null for live. */
  at: number | null;
  /** Replay speed while playing (org time per real second). */
  speed: number;
  playing: boolean;
  /** The dock group shown maximized (the shelf and timeline step aside), if any. */
  maximized: string | null;
  setMaximized: (groupId: string | null) => void;
  select: (agentId: string | null) => void;
  setDock: (api: DockviewApi | null) => void;
  setCommand: (open: boolean) => void;
  setAt: (t: number | null) => void;
  setSpeed: (speed: number) => void;
  setPlaying: (playing: boolean) => void;
}

export const useStudio = create<StudioState>((set) => ({
  agentId: null,
  dock: null,
  commandOpen: false,
  at: null,
  speed: 30,
  playing: false,
  maximized: null,
  setMaximized: (maximized) => set({ maximized }),
  select: (agentId) => set({ agentId }),
  setDock: (dock) => set({ dock }),
  setCommand: (commandOpen) => set({ commandOpen }),
  setAt: (at) => set(at === null ? { at, playing: false } : { at }),
  setSpeed: (speed) => set({ speed }),
  setPlaying: (playing) => set({ playing }),
}));
