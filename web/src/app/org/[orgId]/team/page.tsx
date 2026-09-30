"use client";


import { useShallow } from "zustand/react/shallow";
import "@xyflow/react/dist/style.css";
import {
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AgentAvatar, StatusDot } from "@/components/agent";
import { AgentEditor } from "@/components/team/AgentEditor";
import { Button, ErrorNote, Field, Input, Modal, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Agent, Relationship } from "@/lib/types";
import { useOrg } from "@/store/org";

const REL_STYLE: Record<Relationship["kind"], { color: string; dash?: string; label: string }> = {
  manages: { color: "#8b7cff", label: "manages" },
  peer: { color: "#2a9d8f", dash: "6 4", label: "peer" },
  advises: { color: "#f5b454", dash: "2 4", label: "advises" },
  custom: { color: "#6b7282", dash: "4 4", label: "" },
};

export default function TeamPage() {
  return (
    <Suspense>
      <Team />
    </Suspense>
  );
}

type AgentNodeData = { agent: Agent; selected: boolean };

function AgentNode({ data }: NodeProps<Node<AgentNodeData>>) {
  const live = useOrg((s) => s.live[data.agent.id]?.status ?? data.agent.runtimeStatus);
  return (
    <div
      className={`w-52 rounded-xl border bg-panel px-3 py-2.5 shadow-lg transition-colors ${data.selected ? "border-accent" : "border-line-2 hover:border-ink-3"}`}
    >
      <Handle type="target" position={Position.Top} className="!size-2.5 !border-2 !border-panel !bg-ink-3" />
      <div className="flex items-center gap-2.5">
        <AgentAvatar agent={data.agent} size={32} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <span className="truncate">{data.agent.name}</span>
            <StatusDot status={live} />
          </div>
          <div className="truncate text-xs text-ink-3">{data.agent.role || "No role"}</div>
        </div>
      </div>
      {data.agent.team && <div className="mt-1.5 truncate text-[10px] uppercase tracking-wider text-ink-3">{data.agent.team}</div>}
      <Handle type="source" position={Position.Bottom} className="!size-2.5 !border-2 !border-panel !bg-accent" />
    </div>
  );
}
const nodeTypes = { agent: AgentNode };

/** Tree-ish default layout from "manages" edges. */
function autoLayout(agents: Agent[], rels: Relationship[]): Record<string, { x: number; y: number }> {
  const managers = new Map<string, string>();
  for (const r of rels) if (r.kind === "manages") managers.set(r.toId, r.fromId);
  const depth = (id: string, seen = new Set<string>()): number => {
    const m = managers.get(id);
    if (!m || seen.has(m)) return 0;
    seen.add(id);
    return 1 + depth(m, seen);
  };
  const levels = new Map<number, Agent[]>();
  for (const a of agents) {
    const d = depth(a.id);
    levels.set(d, [...(levels.get(d) ?? []), a]);
  }
  const out: Record<string, { x: number; y: number }> = {};
  for (const [d, list] of levels) {
    list.forEach((a, i) => {
      out[a.id] = { x: (i - (list.length - 1) / 2) * 250, y: d * 160 };
    });
  }
  return out;
}

function Team() {
  const params = useSearchParams();
  const router = useRouter();
  const orgId = useOrg((s) => s.orgId)!;
  const org = useOrg((s) => s.org);
  const agentsMap = useOrg((s) => s.agents);
  const rels = useOrg((s) => s.relationships);
  const selected = params.get("agent");
  const [hiring, setHiring] = useState(false);
  const [pending, setPending] = useState<Connection | null>(null);
  const [edgeMenu, setEdgeMenu] = useState<Relationship | null>(null);
  const agents = useMemo(() => Object.values(agentsMap).filter((a) => !a.isSupervisor), [agentsMap]);

  const saved = (org?.layout?.team as Record<string, { x: number; y: number }> | undefined) ?? {};
  // React Flow owns node state (including measured sizes); we sync agent data into it.
  const [nodes, setNodes] = useState<Node<AgentNodeData>[]>([]);
  useEffect(() => {
    const auto = autoLayout(agents, rels);
    setNodes((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]));
      return agents.map((a) => {
        const old = byId.get(a.id);
        return {
          ...(old ?? {}),
          id: a.id,
          type: "agent",
          position: old?.position ?? saved[a.id] ?? auto[a.id] ?? { x: 0, y: 0 },
          data: { agent: a, selected: a.id === selected },
        } as Node<AgentNodeData>;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agents, rels.length, org?.id, selected]);

  const select = useCallback(
    (id: string | null) => {
      const q = new URLSearchParams(params.toString());
      if (id) q.set("agent", id);
      else q.delete("agent");
      router.replace(`?${q}`);
    },
    [params, router],
  );

  const edges: Edge[] = rels.map((r) => {
    const st = REL_STYLE[r.kind];
    return {
      id: r.id,
      source: r.fromId,
      target: r.toId,
      label: r.kind === "custom" ? r.label : r.kind === "manages" ? undefined : st.label,
      style: { stroke: st.color, strokeWidth: 1.8, strokeDasharray: st.dash },
      markerEnd: r.kind === "manages" || r.kind === "advises" ? { type: MarkerType.ArrowClosed, color: st.color } : undefined,
      labelStyle: { fill: "#a4aab8", fontSize: 11 },
      labelBgStyle: { fill: "#12141a" },
    };
  });

  const persist = useCallback(
    (next: Record<string, { x: number; y: number }>) => {
      if (!org) return;
      void api.updateOrg(org.id, { layout: { ...(org.layout ?? {}), team: next } }).then((o) => useOrg.getState().setOrg(o));
    },
    [org],
  );

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          colorMode="dark"
          onNodeClick={(_, n) => select(n.id)}
          onPaneClick={() => select(null)}
          onNodesChange={(changes) => setNodes((ns) => applyNodeChanges(changes, ns))}
          onNodeDragStop={() =>
            setNodes((ns) => {
              persist(Object.fromEntries(ns.map((n) => [n.id, n.position])));
              return ns;
            })
          }
          onConnect={(c) => c.source !== c.target && setPending(c)}
          onEdgeClick={(_, e) => setEdgeMenu(rels.find((r) => r.id === e.id) ?? null)}
        >
          <Background color="#242833" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
        <div className="absolute left-4 top-4 flex gap-2">
          <Button variant="primary" onClick={() => setHiring(true)}>
            + Hire agent
          </Button>
        </div>
        <div className="absolute bottom-4 left-1/2 hidden -translate-x-1/2 gap-4 md:flex rounded-lg border border-line bg-panel/90 px-4 py-2 text-xs text-ink-2 backdrop-blur">
          {(["manages", "peer", "advises"] as const).map((k) => (
            <span key={k} className="flex items-center gap-1.5">
              <svg width="26" height="6">
                <line x1="0" y1="3" x2="26" y2="3" stroke={REL_STYLE[k].color} strokeWidth="2" strokeDasharray={REL_STYLE[k].dash} />
              </svg>
              {k}
            </span>
          ))}
          <span className="text-ink-3">Drag from a card&apos;s bottom handle to another card to relate them</span>
        </div>
      </div>
      {selected && agentsMap[selected] && (
        <aside className="fixed inset-0 z-30 bg-panel md:static md:z-auto md:w-[520px] md:shrink-0 md:border-l md:border-line">
          <AgentEditor key={selected} agentId={selected} onClose={() => select(null)} />
        </aside>
      )}
      {hiring && <HireModal orgId={orgId} onClose={() => setHiring(false)} onHired={(id) => select(id)} />}
      {pending && (
        <RelationshipModal
          orgId={orgId}
          conn={pending}
          onClose={() => setPending(null)}
        />
      )}
      {edgeMenu && (
        <Modal
          open
          onClose={() => setEdgeMenu(null)}
          title="Relationship"
          footer={
            <>
              <Button variant="ghost" onClick={() => setEdgeMenu(null)}>
                Close
              </Button>
              <Button variant="danger" onClick={() => api.removeRelationship(orgId, edgeMenu.id).then(() => setEdgeMenu(null))}>
                Remove
              </Button>
            </>
          }
        >
          <div className="text-sm">
            <b>{agentsMap[edgeMenu.fromId]?.name}</b> {edgeMenu.kind === "custom" ? edgeMenu.label : edgeMenu.kind} <b>{agentsMap[edgeMenu.toId]?.name}</b>
          </div>
        </Modal>
      )}
    </div>
  );
}

function RelationshipModal({ orgId, conn, onClose }: { orgId: string; conn: Connection; onClose: () => void }) {
  const agents = useOrg((s) => s.agents);
  const [kind, setKind] = useState<Relationship["kind"]>("manages");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    try {
      await api.setRelationship(orgId, { fromId: conn.source!, toId: conn.target!, kind, label });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Modal open onClose={onClose} title="Relate agents" footer={<Button variant="primary" onClick={submit}>Save</Button>}>
      <div className="grid gap-4">
        <div className="text-sm">
          <b>{agents[conn.source!]?.name}</b> → <b>{agents[conn.target!]?.name}</b>
        </div>
        <Field label="Relationship">
          <Select value={kind} onChange={(e) => setKind(e.target.value as Relationship["kind"])}>
            <option value="manages">manages (delegates to, reviews)</option>
            <option value="peer">peer (collaborates with)</option>
            <option value="advises">advises</option>
            <option value="custom">custom…</option>
          </Select>
        </Field>
        {kind === "custom" && (
          <Field label="Label">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. mentors, audits" />
          </Field>
        )}
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}

function HireModal({ orgId, onClose, onHired }: { orgId: string; onClose: () => void; onHired: (id: string) => void }) {
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const [form, setForm] = useState({ name: "", role: "", team: "", manager: "", preset: "worker", persona: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const a = await api.createAgent(orgId, {
        name: form.name,
        role: form.role,
        team: form.team,
        persona: form.persona,
        preset: form.preset,
        manager: form.manager || undefined,
      });
      onHired(a.id);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      title="Hire an agent"
      footer={
        <Button variant="primary" onClick={submit} loading={busy} disabled={!form.name.trim()}>
          Hire
        </Button>
      }
    >
      <div className="grid gap-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name">
            <Input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Nova" />
          </Field>
          <Field label="Role">
            <Input value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} placeholder="Data analyst" />
          </Field>
          <Field label="Team">
            <Input value={form.team} onChange={(e) => setForm({ ...form, team: e.target.value })} placeholder="Analytics" />
          </Field>
          <Field label="Reports to">
            <Select value={form.manager} onChange={(e) => setForm({ ...form, manager: e.target.value })}>
              <option value="">Nobody</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Starting toolset" hint="Fully editable afterwards">
          <Select value={form.preset} onChange={(e) => setForm({ ...form, preset: e.target.value })}>
            <option value="worker">Worker — collaboration + files, shell (approval), web</option>
            <option value="collaborator">Collaborator — messaging, tasks, memory only</option>
          </Select>
        </Field>
        <Field label="Persona" hint="Their system prompt — you can refine it later">
          <textarea
            className="h-32 w-full rounded-md border border-line-2 bg-bg px-3 py-2 text-sm"
            value={form.persona}
            onChange={(e) => setForm({ ...form, persona: e.target.value })}
            placeholder="Who they are, what they own, how they work, and their quality bar."
          />
        </Field>
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}