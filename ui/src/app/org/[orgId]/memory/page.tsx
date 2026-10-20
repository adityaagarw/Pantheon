"use client";

import { useShallow } from "zustand/react/shallow";
import { useCallback, useEffect, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { Badge, Button, cx, Empty, ErrorNote, Input, Select, Spinner, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { MemoryItem } from "@/lib/types";
import { useOrg } from "@/store/org";

type Filter = { kind: "all" } | { kind: "shared" } | { kind: "agent"; id: string };

export default function MemoryPage() {
  const orgId = useOrg((s) => s.orgId)!;
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const byId = useOrg((s) => s.agents);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<Awaited<ReturnType<typeof api.memories>> | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    api
      .memories(orgId, { q: query, scope: filter.kind, agent: filter.kind === "agent" ? filter.id : "" })
      .then(setData)
      .catch(() => setData({ items: [], counts: {}, embeddings: { mode: "off", model: "", loaded: false, error: null } }));
  }, [orgId, query, filter]);
  useEffect(load, [load]);
  // Debounced search.
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const counts = data?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const sem = data?.embeddings;
  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 overflow-y-auto border-r border-line bg-panel p-3 md:block">
        <FilterItem active={filter.kind === "all"} onClick={() => setFilter({ kind: "all" })} label="Everything" count={total} />
        <FilterItem active={filter.kind === "shared"} onClick={() => setFilter({ kind: "shared" })} label="Shared with the org" count={counts.shared ?? 0} />
        <div className="mb-1 mt-4 px-2 text-[11px] font-medium uppercase tracking-wider text-ink-3">Private to</div>
        {agents.map((a) => (
          <FilterItem
            key={a.id}
            active={filter.kind === "agent" && filter.id === a.id}
            onClick={() => setFilter({ kind: "agent", id: a.id })}
            label={a.name}
            icon={<AgentAvatar agent={a} size={18} />}
            count={counts[a.id] ?? 0}
          />
        ))}
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <Input className="min-w-0 flex-1 md:max-w-md" placeholder="Search by meaning, e.g. “what does the user like?”" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select
            className="w-40 md:hidden"
            value={filter.kind === "agent" ? filter.id : filter.kind}
            onChange={(e) => setFilter(e.target.value === "all" || e.target.value === "shared" ? { kind: e.target.value } : { kind: "agent", id: e.target.value })}
          >
            <option value="all">Everything</option>
            <option value="shared">Shared</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
          <Button variant="primary" onClick={() => setAdding(true)}>
            + Add
          </Button>
          {sem && (
            <span className="ml-auto text-xs text-ink-3" title={sem.error ?? sem.model}>
              {sem.mode === "off" || sem.error ? "keyword search" : `semantic search · ${sem.model.split("/").pop()}`}
            </span>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {adding && <AddMemory orgId={orgId} agents={agents} preset={filter.kind === "agent" ? filter.id : ""} onDone={() => (setAdding(false), load())} />}
          {!data ? (
            <Spinner />
          ) : data.items.length === 0 ? (
            <Empty title={query ? "Nothing matches" : "No memories yet"} icon="🧠">
              Agents save what matters with <code>remember</code> — decisions, facts, your preferences — and relevant memories are brought back automatically when a new message arrives. You can add and correct them here.
            </Empty>
          ) : (
            <div className="mx-auto max-w-3xl space-y-2">
              {data.items.map((m) => (
                <MemoryCard key={m.id} m={m} owner={m.agentId ? byId[m.agentId] : undefined} onChange={load} />
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function FilterItem({ active, onClick, label, count, icon }: { active: boolean; onClick: () => void; label: string; count: number; icon?: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cx("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm cursor-pointer", active ? "bg-panel-2 text-ink" : "text-ink-2 hover:bg-panel-2/60")}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="text-[11px] text-ink-3">{count}</span>
    </button>
  );
}

function MemoryCard({ m, owner, onChange }: { m: MemoryItem; owner?: import("@/lib/types").Agent; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(m.content);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="rounded-lg border border-line bg-panel p-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-ink-3">
        {m.shared ? <Badge tone="info">shared</Badge> : <span className="flex items-center gap-1.5">{owner && <AgentAvatar agent={owner} size={16} />}{owner?.name ?? "agent"}</span>}
        {m.source === "user" && <Badge>added by you</Badge>}
        <span>{timeAgo(m.updatedAt ?? m.createdAt)}</span>
        {typeof m.score === "number" && m.score > 0 && <span title="similarity to your search">· {Math.round(m.score * 100)}% match</span>}
        <span className="ml-auto flex gap-1">
          {!editing && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {confirm ? (
            <Button size="sm" variant="danger" onClick={() => api.deleteMemory(m.id).then(onChange)}>
              Delete
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>
              ✕
            </Button>
          )}
        </span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <ErrorNote error={error} />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              onClick={() =>
                api
                  .updateMemory(m.id, text)
                  .then(() => (setEditing(false), onChange()))
                  .catch((e: Error) => setError(e.message))
              }
            >
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setText(m.content))}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="whitespace-pre-wrap text-sm text-ink">{m.content}</div>
      )}
    </div>
  );
}

function AddMemory({ orgId, agents, preset, onDone }: { orgId: string; agents: import("@/lib/types").Agent[]; preset: string; onDone: () => void }) {
  const [content, setContent] = useState("");
  const [who, setWho] = useState(preset);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="mx-auto mb-4 max-w-3xl space-y-2 rounded-lg border border-accent/40 bg-accent/5 p-3">
      <Textarea rows={3} autoFocus value={content} onChange={(e) => setContent(e.target.value)} placeholder="Something the agents should know, e.g. “I learn best with visuals and short quizzes.”" />
      <div className="flex flex-wrap items-center gap-2">
        <Select className="w-56" value={who} onChange={(e) => setWho(e.target.value)}>
          <option value="">Shared with everyone</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              Only {a.name}
            </option>
          ))}
        </Select>
        <ErrorNote error={error} />
        <span className="ml-auto flex gap-2">
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!content.trim()}
            onClick={() =>
              api
                .addMemory(orgId, content, who || null)
                .then(onDone)
                .catch((e: Error) => setError(e.message))
            }
          >
            Save
          </Button>
        </span>
      </div>
    </div>
  );
}
