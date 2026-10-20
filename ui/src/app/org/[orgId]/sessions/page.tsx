"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { SearchView } from "@/components/sessions/SearchView";
import { StatsView } from "@/components/sessions/StatsView";
import { TurnView } from "@/components/sessions/TurnView";
import { Badge, Button, cx, Empty, Select, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo, tokens, truncate, usd } from "@/lib/format";
import type { Turn } from "@/lib/types";
import { useOrg } from "@/store/org";

const TURN_TONE = { running: "accent", completed: "ok", failed: "bad", awaiting_approval: "warn", stopped: "neutral" } as const;

export default function SessionsPage() {
  return (
    <Suspense>
      <Sessions />
    </Suspense>
  );
}

function Sessions() {
  const params = useSearchParams();
  const router = useRouter();
  const [tab, setTab] = useState<"sessions" | "search" | "stats">("sessions");
  const orgId = useOrg((s) => s.orgId)!;
  const agents = useOrg((s) => s.agents);
  const version = useOrg((s) => s.turnsVersion);
  const agentFilter = params.get("agent") ?? "";
  const selected = params.get("turn");
  const [status, setStatus] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [more, setMore] = useState(true);

  const setParam = useCallback(
    (k: string, v: string | null) => {
      const q = new URLSearchParams(params.toString());
      if (v) q.set(k, v);
      else q.delete(k);
      router.replace(`?${q}`);
    },
    [params, router],
  );

  useEffect(() => {
    api
      .turns(orgId, { agent_id: agentFilter || undefined, status: status || undefined, limit: 60 })
      .then((fresh) => {
        // Merge the newest page with any older pages already loaded.
        setTurns((prev) => {
          const byId = new Map(prev.map((p) => [p.id, p]));
          for (const t of fresh) byId.set(t.id, t);
          return [...byId.values()].sort((a, b) => (b.startedAt ?? "").localeCompare(a.startedAt ?? ""));
        });
        setMore((m) => m || fresh.length === 60);
      })
      .catch(() => {});
  }, [orgId, agentFilter, status, version]);

  useEffect(() => {
    setTurns([]);
  }, [agentFilter, status]);

  const loadMore = async () => {
    const last = turns[turns.length - 1];
    if (!last) return;
    const older = await api.turns(orgId, { agent_id: agentFilter || undefined, status: status || undefined, limit: 60, before: last.id });
    setTurns((t) => [...t, ...older]);
    setMore(older.length === 60);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="no-scrollbar flex items-center gap-3 overflow-x-auto border-b border-line px-4 py-2.5">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: "sessions", label: "Sessions" },
            { value: "search", label: "Search" },
            { value: "stats", label: "Usage & stats" },
          ]}
        />
      </div>
      {tab === "search" ? (
        <SearchView orgId={orgId} onOpenTurn={(id) => (setTab("sessions"), setParam("turn", id))} />
      ) : tab === "stats" ? (
        <StatsView orgId={orgId} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside className={cx("w-full shrink-0 flex-col border-r border-line bg-panel md:flex md:w-[380px]", selected ? "hidden" : "flex")}>
            <div className="flex gap-2 border-b border-line p-3">
              <Select value={agentFilter} onChange={(e) => setParam("agent", e.target.value || null)}>
                <option value="">All agents</option>
                {Object.values(agents).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
              <Select className="w-32 sm:w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">Any status</option>
                {Object.keys(TURN_TONE).map((s) => (
                  <option key={s} value={s}>
                    {s.replace("_", " ")}
                  </option>
                ))}
              </Select>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {turns.length === 0 && <Empty title="No sessions yet">Each time an agent wakes up to handle its inbox, a session is recorded here.</Empty>}
              {turns.map((t) => (
                <TurnRow key={t.id} turn={t} active={t.id === selected} onClick={() => setParam("turn", t.id)} />
              ))}
              {more && turns.length > 0 && (
                <div className="p-3">
                  <Button className="w-full" size="sm" onClick={loadMore}>
                    Load older
                  </Button>
                </div>
              )}
            </div>
          </aside>
          <section className={cx("min-w-0 flex-1 overflow-y-auto md:block", selected ? "block" : "hidden")}>
            {selected && (
              <button
                onClick={() => setParam("turn", null)}
                className="sticky top-0 z-10 flex w-full items-center gap-1 border-b border-line bg-panel/95 px-3 py-2 text-sm text-ink-2 backdrop-blur md:hidden cursor-pointer"
              >
                ‹ All sessions
              </button>
            )}
            {selected ? <TurnView key={selected} turnId={selected} /> : <Empty title="Select a session" icon="🔎">Pick a session to see exactly what the agent received, thought, called and said.</Empty>}
          </section>
        </div>
      )}
    </div>
  );
}

function TurnRow({ turn, active, onClick }: { turn: Turn; active: boolean; onClick: () => void }) {
  const agent = useOrg((s) => s.agents[turn.agentId]);
  return (
    <button onClick={onClick} className={cx("block w-full border-b border-line/70 px-3 py-2.5 text-left transition-colors cursor-pointer", active ? "bg-panel-2" : "hover:bg-panel-2/50")}>
      <div className="flex items-center gap-2">
        <AgentAvatar agent={agent} size={22} />
        <span className="text-sm font-medium">{agent?.name ?? turn.agentId}</span>
        <Badge tone={TURN_TONE[turn.status]}>{turn.status.replace("_", " ")}</Badge>
        <span className="ml-auto text-[11px] text-ink-3">{timeAgo(turn.startedAt)}</span>
      </div>
      <div className="mt-1 line-clamp-2 text-xs text-ink-2">{turn.error ? `⚠ ${turn.error}` : turn.summary ? truncate(turn.summary, 160) : "…"}</div>
      <div className="mt-1 flex gap-3 text-[11px] text-ink-3">
        <span>{turn.steps} steps</span>
        <span>{tokens(turn.inputTokens + turn.outputTokens)} tok</span>
        {turn.costUsd > 0 && <span>{usd(turn.costUsd)}</span>}
        {turn.attempts > 1 && <span className="text-warn">{turn.attempts} attempts</span>}
      </div>
    </button>
  );
}
