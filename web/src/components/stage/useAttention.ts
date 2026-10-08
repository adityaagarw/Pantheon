"use client";

import { useEffect, useRef } from "react";
import { api, type AttentionView } from "@/lib/api";

/**
 * Teach-along: tell the agent that owns the current view what the user is
 * looking at (debounced). Clears it when the view goes away, so an agent the
 * user switched away from isn't told they're still with it.
 */
export function useAttention(agentId: string | null | undefined, view: AttentionView | null, deps: unknown[], delayMs = 400): void {
  const idRef = useRef<string | null>(null);
  const viewRef = useRef<AttentionView | null>(null);
  useEffect(() => {
    idRef.current = agentId ?? null;
    viewRef.current = view;
  });
  useEffect(() => {
    if (!agentId || !view) return;
    const t = setTimeout(() => {
      void api.agentAttention(agentId, view).catch(() => {});
    }, delayMs);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  // The server forgets after 15 minutes; keep telling it while the user is still here.
  useEffect(() => {
    const id = setInterval(() => {
      if (idRef.current && viewRef.current) void api.agentAttention(idRef.current, viewRef.current).catch(() => {});
    }, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    return () => {
      if (idRef.current) void api.clearAttention(idRef.current).catch(() => {});
    };
  }, []);
}
