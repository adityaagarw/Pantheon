/**
 * Time travel: the org as it was at any moment (docs/DESIGN.md, "Time is a dimension").
 *
 * Every event is in the engine's gap-free log. To show the org at time T, the same
 * reducer that applies live events is folded over the stored events up to T, each with
 * its own timestamp as "now", starting from the org's agents with nothing going on.
 * Checkpoints every few hundred events keep scrubbing quick.
 */
import { create } from "zustand";
import { api } from "@/lib/api";
import type { PantheonEvent } from "@/lib/types";
import { emptyState, reduce, type OrgState } from "@/store/reduce";
import { useOrg } from "@/store/org";
import { useStudio } from "./store";

const PAGE = 1000; // the engine's page of events
const CHECKPOINT_EVERY = 400;

interface ReplayStore {
  orgId: string | null;
  events: PantheonEvent[];
  loading: boolean;
  /** The org at the time cursor (when replaying). */
  view: OrgState | null;
  /** The view's message beams, timed for animating now (see show). */
  beams: OrgState["beams"];
  load: (orgId: string) => Promise<void>;
  show: (t: number) => void;
}

let checkpoints: { index: number; state: OrgState }[] = [];

/** The org's structure with nothing happening: agents idle, no pulses, no feed. */
function baseState(): OrgState {
  const live = useOrg.getState();
  const agents = Object.fromEntries(
    Object.entries(live.agents).map(([id, a]) => [id, { ...a, runtimeStatus: "idle" as const, runtimeDetail: "" }]),
  );
  return { ...emptyState(), agents, relationships: live.relationships, channels: live.channels };
}

export const useReplay = create<ReplayStore>((set, get) => ({
  orgId: null,
  events: [],
  loading: false,
  view: null,
  beams: [],
  load: async (orgId) => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const events: PantheonEvent[] = [];
      let after = 0;
      for (;;) {
        const page = await api.events(orgId, after);
        events.push(...page);
        if (page.length < PAGE) break;
        after = page[page.length - 1]!.seq ?? after;
      }
      events.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
      checkpoints = [{ index: 0, state: baseState() }];
      let s = checkpoints[0]!.state;
      events.forEach((e, i) => {
        s = reduce(s, e, e.ts);
        if ((i + 1) % CHECKPOINT_EVERY === 0) checkpoints.push({ index: i + 1, state: s });
      });
      set({ orgId, events, loading: false });
    } catch {
      set({ loading: false });
    }
  },
  show: (t) => {
    const { events } = get();
    let lo = 0, hi = events.length; // first event after t
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (events[mid]!.ts <= t) lo = mid + 1;
      else hi = mid;
    }
    let cp = checkpoints[0]!;
    for (const c of checkpoints) if (c.index <= lo) cp = c;
    let s = cp.state;
    for (let i = cp.index; i < lo; i++) s = reduce(s, events[i]!, events[i]!.ts);
    // Agents that didn't exist yet aren't shown.
    const agents = Object.fromEntries(Object.entries(s.agents).filter(([, a]) => !a.createdAt || Date.parse(a.createdAt) <= t));
    // The office animates a beam by its age in real time: a replayed one is aged by how
    // long before the cursor it was sent, at the replay speed.
    const speed = useStudio.getState().speed;
    const now = Date.now();
    set({ view: { ...s, agents }, beams: s.beams.map((b) => ({ ...b, at: now - (t - b.at) / speed })) });
  },
}));

/** Read org state: the replayed moment when the time cursor is set, otherwise live. */
export function useView<T>(select: (s: OrgState) => T): T {
  const replaying = useStudio((s) => s.at !== null);
  const live = useOrg(select as (s: ReturnType<typeof useOrg.getState>) => T);
  const replayed = useReplay((r) => (r.view ? select(r.view) : undefined));
  return replaying && replayed !== undefined ? replayed : live;
}
