/**
 * The Canvas: the organization as a living map (docs/DESIGN.md).
 *
 * Agents are nodes placed by the org chart. Every message or task hand-off travels as a
 * pulse from sender to recipient. Zoom is semantic: far out an agent is a mark with its
 * state ring; closer it's a card with its live line of thought; closest, the card shows
 * what it's writing and thinking as it streams.
 *
 * Not a pane any more: the 3D office is an org's main view, and the Team page shows the
 * chart. This becomes the flow (DAG) editor's base in milestone 4 (docs/PLAN.md).
 */
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  getBezierPath,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { memo, useEffect, useMemo, useState } from "react";
import type { Agent, Relationship } from "@/lib/types";
import { Icon } from "./icons";
import { agentHue, hsl, initials, stateLook } from "./look";
import { useView } from "./replay";
import { useStudio } from "./store";

const COL = 300; // horizontal room per agent
const ROW = 230; // vertical room per level

type AgentNodeData = { agent: Agent };
type PulseEdgeData = {
  pulses: { id: string; back: boolean; kind: "message" | "task"; hue: number }[];
  kind: string;
  dim: boolean;
};

/** Place agents as a tree from "manages" links; everyone else in a row of their own. */
function layout(agents: Agent[], rels: Relationship[]): Record<string, { x: number; y: number }> {
  const ids = new Set(agents.map((a) => a.id));
  const children: Record<string, string[]> = {};
  const hasBoss = new Set<string>();
  for (const r of rels) {
    if (r.kind !== "manages" || !ids.has(r.fromId) || !ids.has(r.toId)) continue;
    (children[r.fromId] ??= []).push(r.toId);
    hasBoss.add(r.toId);
  }
  const pos: Record<string, { x: number; y: number }> = {};
  const seen = new Set<string>();
  let cursor = 0;
  const place = (id: string, depth: number): number => {
    if (seen.has(id)) return pos[id]?.x ?? 0;
    seen.add(id);
    const kids = (children[id] ?? []).filter((k) => !seen.has(k));
    let x: number;
    if (kids.length === 0) {
      x = cursor;
      cursor += COL;
    } else {
      const xs = kids.map((k) => place(k, depth + 1));
      x = (Math.min(...xs) + Math.max(...xs)) / 2;
    }
    pos[id] = { x, y: depth * ROW };
    return x;
  };
  const roots = agents.filter((a) => !hasBoss.has(a.id)).sort((a, b) =>
    (children[b.id]?.length ?? 0) - (children[a.id]?.length ?? 0) || a.name.localeCompare(b.name));
  for (const r of roots) place(r.id, 0);
  for (const a of agents) if (!seen.has(a.id)) place(a.id, 0);
  return pos;
}

const AgentNode = memo(function AgentNode({ data, selected }: NodeProps<Node<AgentNodeData>>) {
  const { agent } = data;
  const zoom = useStore((s) => s.transform[2]);
  const live = useView((s) => s.live[agent.id]);
  const look = stateLook(agent.runtimeStatus, agent.status);
  const hue = agentHue(agent);
  const tier = zoom < 0.5 ? 0 : zoom < 1.25 ? 1 : 2;
  const thought = (live?.thinking || live?.stream || live?.lastSaid || agent.runtimeDetail || "").trim();
  const ring = (
    <div
      className={look.motion === "breathe" ? "st-breathe" : look.motion === "glow" ? "st-glow rounded-full" : ""}
      style={{ ["--glow" as string]: look.color }}
    >
      <div
        className="relative grid place-items-center rounded-full"
        style={{
          width: tier === 0 ? 64 : 46,
          height: tier === 0 ? 64 : 46,
          background: `radial-gradient(circle at 30% 25%, ${hsl(hue, 80, 72, 0.95)}, ${hsl(hue, 70, 38, 0.95)})`,
          boxShadow: `0 0 0 2px var(--st-void), 0 0 0 4px ${look.color}, 0 0 28px -4px ${hsl(hue, 90, 60, 0.6)}`,
        }}
      >
        <span className="st-display text-[15px] text-white/95" style={{ fontSize: tier === 0 ? 20 : 15 }}>
          {initials(agent.name)}
        </span>
        {agent.runtimeStatus === "working" && (
          <span
            className="absolute -inset-[7px] rounded-full border-2 border-transparent"
            style={{ borderTopColor: look.color, animation: "st-spin 1.1s linear infinite" }}
          />
        )}
      </div>
    </div>
  );

  return (
    <div className="relative">
      <Handle type="target" position={Position.Top} className="!opacity-0" />
      <Handle type="source" position={Position.Bottom} className="!opacity-0" />
      {tier === 0 ? (
        <div className="flex flex-col items-center gap-2">
          {ring}
          <div className="st-display max-w-[160px] truncate text-center text-[22px]" style={{ color: hsl(hue, 70, 82) }}>
            {agent.name}
          </div>
        </div>
      ) : (
        <div
          className="glass w-[260px] rounded-2xl p-3.5 transition-[border-color,box-shadow] duration-200"
          style={{
            borderColor: selected ? hsl(hue, 80, 70, 0.7) : look.loud ? look.color : undefined,
            boxShadow: selected ? `0 0 0 1px ${hsl(hue, 80, 70, 0.5)}, 0 20px 60px -20px ${hsl(hue, 80, 50, 0.6)}` : undefined,
          }}
        >
          <div className="flex items-center gap-3">
            {ring}
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-semibold" style={{ color: hsl(hue, 70, 84) }}>{agent.name}</div>
              <div className="truncate text-[12px] text-[var(--st-ink-3)]">{agent.role || agent.team || "Agent"}</div>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2 text-[11.5px]">
            <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5" style={{ background: "rgba(255,255,255,0.05)", color: look.color }}>
              <span className="size-1.5 rounded-full" style={{ background: look.color }} />
              {look.label}
            </span>
            {live?.tool && (
              <span className="st-mono truncate rounded-full px-2 py-0.5 text-[var(--st-ink-2)]" style={{ background: "rgba(255,255,255,0.05)" }}>
                {live.tool}
              </span>
            )}
          </div>
          {thought && (
            <div
              className={`mt-2.5 overflow-hidden text-[12px] leading-[1.45] text-[var(--st-ink-2)] ${tier === 2 ? "max-h-[132px]" : "max-h-[36px]"}`}
              style={{ maskImage: "linear-gradient(to bottom, black 60%, transparent)" }}
            >
              {tier === 2 ? thought.slice(-700) : thought.slice(-160)}
            </div>
          )}
        </div>
      )}
    </div>
  );
});

/** A link between two agents, carrying the pulses of the messages and tasks moving on it. */
function PulseEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps<Edge<PulseEdgeData>>) {
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition: Position.Bottom, targetX, targetY, targetPosition: Position.Top });
  const dashed = data?.kind !== "manages";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: data?.kind === "flow" ? "rgba(148,160,220,0.0)" : `rgba(148,160,220,${data?.dim ? 0.06 : 0.22})`,
          transition: "stroke 0.35s",
          strokeWidth: 1.4,
          strokeDasharray: dashed ? "4 6" : undefined,
        }}
      />
      {data?.pulses.map((p) => (
        <g key={p.id}>
          {/* the edge lights up along the way, then fades */}
          <path d={path} fill="none" stroke={hsl(p.hue, 90, 66, 0.55)} strokeWidth={p.kind === "task" ? 3.5 : 2.5}
            strokeLinecap="round" pathLength={1} strokeDasharray="0.22 1">
            <animate attributeName="stroke-dashoffset" values={p.back ? "-0.78;0.22" : "0.22;-0.78"} dur="1.6s" fill="freeze" />
            <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.1;0.8;1" dur="1.6s" fill="freeze" />
          </path>
          <circle r={p.kind === "task" ? 14 : 11} fill={hsl(p.hue, 95, 70, 0.18)}>
            <animateMotion dur="1.6s" fill="freeze" path={path}
              keyPoints={p.back ? "1;0" : "0;1"} keyTimes="0;1" calcMode="linear" />
          </circle>
          <circle r={p.kind === "task" ? 6.5 : 5} fill={hsl(p.hue, 100, 80)} style={{ filter: `drop-shadow(0 0 8px ${hsl(p.hue, 95, 60)})` }}>
            <animateMotion dur="1.6s" fill="freeze" path={path}
              keyPoints={p.back ? "1;0" : "0;1"} keyTimes="0;1" calcMode="linear" />
          </circle>
        </g>
      ))}
    </>
  );
}

const nodeTypes = { agent: AgentNode };
const edgeTypes = { pulse: PulseEdge };

function CanvasInner({ orgId }: { orgId: string }) {
  const agentsById = useView((s) => s.agents);
  const rels = useView((s) => s.relationships);
  const allBeams = useView((s) => s.beams);
  const at = useStudio((s) => s.at);
  const speed = useStudio((s) => s.speed);
  // Replaying: the pulses of the last moments before the cursor (scaled to the speed,
  // so a fast replay still shows its traffic); live, the store keeps only fresh ones.
  const beams = useMemo(
    () => (at === null ? allBeams : allBeams.filter((b) => b.at <= at && at - b.at < 1600 * Math.max(1, speed))),
    [allBeams, at, speed],
  );
  const select = useStudio((s) => s.select);
  const selectedId = useStudio((s) => s.agentId);
  const flow = useReactFlow();
  const agents = useMemo(
    () => Object.values(agentsById).filter((a) => !a.isSupervisor && !a.metaRole),
    [agentsById],
  );
  const placed = useMemo(() => layout(agents, rels), [agents, rels]);
  // Positions the user dragged to, kept per org.
  const [moved, setMoved] = useState<Record<string, { x: number; y: number }>>(() => {
    try {
      return JSON.parse(localStorage.getItem(`studio.canvas.${orgId}`) || "{}");
    } catch {
      return {};
    }
  });

  // The focus lens: with an agent selected, everyone it doesn't work with fades back.
  const near = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set([selectedId]);
    for (const r of rels) {
      if (r.fromId === selectedId) set.add(r.toId);
      if (r.toId === selectedId) set.add(r.fromId);
    }
    for (const b of beams) if (b.from === selectedId || b.to === selectedId) set.add(b.from === selectedId ? b.to : b.from);
    return set;
  }, [selectedId, rels, beams]);

  const nodes: Node<AgentNodeData>[] = useMemo(
    () => agents.map((a) => ({
      id: a.id,
      type: "agent",
      position: moved[a.id] ?? placed[a.id] ?? { x: 0, y: 0 },
      data: { agent: a },
      selected: a.id === selectedId,
      style: { opacity: near && !near.has(a.id) ? 0.28 : 1, transition: "opacity 0.35s" },
    })),
    [agents, placed, moved, selectedId, near],
  );

  const edges: Edge<PulseEdgeData>[] = useMemo(() => {
    const key = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);
    const out = new Map<string, Edge<PulseEdgeData>>();
    for (const r of rels) {
      if (!agentsById[r.fromId] || !agentsById[r.toId]) continue;
      out.set(key(r.fromId, r.toId), {
        id: r.id, source: r.fromId, target: r.toId, type: "pulse",
        data: { kind: r.kind, pulses: [], dim: !!selectedId && r.fromId !== selectedId && r.toId !== selectedId },
      });
    }
    for (const b of beams) {
      if (!nodes.find((n) => n.id === b.from) || !nodes.find((n) => n.id === b.to)) continue;
      const k = key(b.from, b.to);
      let e = out.get(k);
      if (!e) {
        e = { id: `flow:${k}`, source: b.from, target: b.to, type: "pulse", data: { kind: "flow", pulses: [], dim: false } };
        out.set(k, e);
      }
      e.data!.pulses.push({ id: b.id, back: e.source !== b.from, kind: b.kind, hue: agentHue(agentsById[b.from]) });
    }
    return [...out.values()];
  }, [rels, beams, agentsById, nodes, selectedId]);

  useEffect(() => {
    const t = setTimeout(() => flow.fitView({ padding: 0.25, duration: 600 }), 80);
    return () => clearTimeout(t);
  }, [agents.length, flow]);

  if (agents.length === 0) {
    return (
      <div className="grid h-full place-items-center text-sm text-[var(--st-ink-3)]">
        {at === null ? "This organization has no agents yet." : "At this moment the org had no agents yet. Drag the timeline forward."}
      </div>
    );
  }

  return (
    <ReactFlow
      className="st-canvas"
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      minZoom={0.2}
      maxZoom={2.2}
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, n) => select(n.id)}
      onPaneClick={() => select(null)}
      onNodeDragStop={(_, n) => {
        const next = { ...moved, [n.id]: n.position };
        setMoved(next);
        try {
          localStorage.setItem(`studio.canvas.${orgId}`, JSON.stringify(next));
        } catch {
          // storage unavailable: positions last for this session
        }
      }}
      onNodeDoubleClick={(_, n) => {
        select(n.id);
        flow.setCenter(n.position.x + 130, n.position.y + 60, { zoom: 1.6, duration: 700 });
      }}
    >
      <Background variant={BackgroundVariant.Dots} gap={28} size={1.2} color={at === null ? "rgba(148,160,220,0.16)" : "rgba(76,201,240,0.22)"} />
      {at !== null && (
        <div className="pointer-events-none absolute inset-0 z-[5]" style={{
          boxShadow: "inset 0 0 120px rgba(76,201,240,0.10)",
          background: "linear-gradient(180deg, rgba(76,201,240,0.05), transparent 30%)",
        }}>
          <div className="st-mono absolute left-1/2 top-3 -translate-x-1/2 rounded-full border border-[rgba(76,201,240,0.4)] bg-[rgba(8,20,30,0.7)] px-3 py-1 text-[11px] tracking-wide text-[var(--st-cyan)]">
            REPLAY · {new Date(at).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </div>
        </div>
      )}
      <Controls showInteractive={false} position="bottom-right" />
      <div className="pointer-events-none absolute left-4 top-3 z-10 flex items-center gap-2 text-[11px] text-[var(--st-ink-3)]">
        <Icon name="canvas" size={13} /> Scroll to zoom into an agent · double-click to dive in · drag to arrange
      </div>
      <button
        className="absolute right-4 top-3 z-10 rounded-lg border border-[var(--st-edge)] bg-[var(--st-glass-2)] px-2.5 py-1 text-[11px] text-[var(--st-ink-2)] hover:text-[var(--st-ink)]"
        onClick={() => {
          setMoved({});
          localStorage.removeItem(`studio.canvas.${orgId}`);
          setTimeout(() => flow.fitView({ padding: 0.25, duration: 600 }), 30);
        }}
      >
        Re-arrange
      </button>
    </ReactFlow>
  );
}

export function Canvas({ orgId }: { orgId: string }) {
  return (
    <div className="relative h-full w-full">
      <ReactFlowProvider>
        <CanvasInner orgId={orgId} />
      </ReactFlowProvider>
    </div>
  );
}
