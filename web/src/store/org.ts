"use client";

import { useEffect } from "react";
import { create } from "zustand";
import { api } from "@/lib/api";
import type { Board, Message, Org, PantheonEvent } from "@/lib/types";
import { EventStream, type StreamState } from "@/lib/ws";
import { emptyState, pruneBeams, reduce, type OrgState } from "./reduce";

interface Store extends OrgState {
  orgId: string | null;
  org: Org | null;
  boards: Board[];
  stream: StreamState;
  loading: boolean;
  error: string | null;
  apply: (e: PantheonEvent) => void;
  loadChannel: (channelId: string) => Promise<Message[]>;
  refresh: () => Promise<void>;
  setOrg: (o: Org) => void;
}

export const useOrg = create<Store>((set, get) => ({
  ...emptyState(),
  orgId: null,
  org: null,
  boards: [],
  stream: "closed",
  loading: false,
  error: null,
  apply: (e) => set((s) => reduce(s, e)),
  setOrg: (o) => set({ org: o }),
  loadChannel: async (channelId) => {
    const msgs = await api.channelMessages(channelId);
    set((s) => {
      const live = s.messagesByChannel[channelId] ?? [];
      const ids = new Set(msgs.map((m) => m.id));
      return { messagesByChannel: { ...s.messagesByChannel, [channelId]: [...msgs, ...live.filter((m) => !ids.has(m.id))] } };
    });
    return get().messagesByChannel[channelId];
  },
  refresh: async () => {
    const id = get().orgId;
    if (!id) return;
    const [snap, tasks, channels, approvals, meetings, history, world, stagePages, boards] = await Promise.all([
      api.org(id),
      api.tasks(id),
      api.channels(id),
      api.approvals(id),
      api.meetings(id).catch(() => []),
      api.recentEvents(id, 250).catch(() => []),
      api.world(id).catch(() => null),
      api.stagePages(id).catch(() => []),
      api.whiteboards(id).catch(() => []),
    ]);
    const base: Partial<OrgState> = {
      agents: Object.fromEntries(snap.agents.map((a) => [a.id, a])),
      relationships: snap.relationships,
      channels: Object.fromEntries(channels.map((c) => [c.id, c])),
      tasks: Object.fromEntries(tasks.map((t) => [t.id, t])),
      approvals: Object.fromEntries(approvals.map((a) => [a.id, a])),
      objects: Object.fromEntries((world?.objects ?? []).map((o) => [o.id, o])),
      stagePages: Object.fromEntries(stagePages.map((pg) => [pg.id, pg])),
      whiteboards: Object.fromEntries(boards.map((b) => [b.id, b])),
    };
    // Rebuild the activity feed from recent history (only the feed: live
    // status comes from the snapshot, which is authoritative).
    let replay: OrgState = { ...emptyState(), ...base } as OrgState;
    for (const e of history) replay = reduce(replay, e, e.ts);
    set({
      org: snap.org,
      boards: snap.boards,
      ...base,
      feed: replay.feed,
      meetings: Object.fromEntries(
        meetings
          .filter((m) => m.status === "running")
          .map((m) => [
            m.id,
            { id: m.id, agenda: m.agenda, facilitatorId: m.facilitatorId, participants: m.participants, speakerId: null, lastText: "", room: m.options.room ?? null, style: m.style },
          ]),
      ),
    });
  },
}));

/** Load an org and keep it live over the event stream. */
export function useOrgConnection(orgId: string): void {
  useEffect(() => {
    let stream: EventStream | null = null;
    let cancelled = false;
    const buffer: PantheonEvent[] = [];
    let ready = false;
    useOrg.setState({ ...emptyState(), orgId, org: null, loading: true, error: null, stream: "connecting" });

    // Subscribe first (buffering), load the snapshot once the subscription is
    // live, then replay the buffer: nothing that happens during the load is lost.
    let loadStarted = false;
    const load = () => {
      loadStarted = true;
      useOrg
        .getState()
        .refresh()
        .then(() => {
          if (cancelled) return;
          ready = true;
          for (const e of buffer) useOrg.getState().apply(e);
          buffer.length = 0;
          useOrg.setState({ loading: false });
        })
        .catch((err: Error) => !cancelled && useOrg.setState({ loading: false, error: err.message }));
    };
    stream = new EventStream(
      orgId,
      (e) => {
        if (e.type === "org.updated") {
          useOrg.getState().setOrg(e.payload as unknown as Org);
          return;
        }
        if (!ready) buffer.push(e);
        else useOrg.getState().apply(e);
      },
      (st) => {
        useOrg.setState({ stream: st });
        if (st === "live" && !loadStarted) load();
      },
    );
    // If the stream can't connect, still show the (static) snapshot.
    const fallback = setTimeout(() => !loadStarted && load(), 4000);

    const gc = setInterval(() => useOrg.setState((s) => pruneBeams(s)), 1000);
    // A message in a channel we haven't seen (e.g. a brand-new DM) -> refetch channels.
    let seenChannelsVersion = 0;
    const unsub = useOrg.subscribe((s) => {
      if (s.channelsVersion !== seenChannelsVersion) {
        seenChannelsVersion = s.channelsVersion;
        api
          .channels(orgId)
          .then((chs) => useOrg.setState({ channels: Object.fromEntries(chs.map((c) => [c.id, c])) }))
          .catch(() => {});
      }
    });
    return () => {
      cancelled = true;
      clearInterval(gc);
      clearTimeout(fallback);
      unsub();
      stream?.close();
    };
  }, [orgId]);
}

export function agentName(id: string | null | undefined): string {
  if (!id) return "—";
  if (id === "user") return "You";
  if (id === "system") return "System";
  return useOrg.getState().agents[id]?.name ?? id;
}
