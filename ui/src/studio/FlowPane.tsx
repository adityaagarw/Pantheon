/**
 * Flows (engine services/flows.py): work as a graph of steps instead of free-form chatter.
 * Draw steps, give each an agent and instructions, connect them, run with an input, and
 * watch each step light up as it runs and hands its output on.
 */
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AnimatePresence, motion } from "motion/react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import { api } from "@/lib/api";
import type { Agent, Flow, FlowGraph, FlowNode, FlowRun, StepStatus } from "@/lib/types";
import { useOrg } from "@/store/org";
import { Icon } from "./icons";
import { agentHue, hsl, initials } from "./look";

const ink3 = "text-[var(--st-ink-3)]";
const field =
  "rounded-lg border border-[var(--st-edge)] bg-black/20 px-3 py-2 text-[13px] text-[var(--st-ink)] outline-none focus:border-[var(--st-edge-2)]";
const btn =
  "inline-flex h-8 whitespace-nowrap items-center gap-1.5 rounded-lg border border-[var(--st-edge)] bg-[var(--st-glass-2)] px-3 text-[12.5px] text-[var(--st-ink-2)] transition-colors hover:border-[var(--st-edge-2)] hover:text-[var(--st-ink)] disabled:opacity-40";
const primary = "inline-flex h-8 whitespace-nowrap items-center gap-1.5 rounded-lg px-3 text-[12.5px] text-white disabled:opacity-40";

const STATUS: Record<StepStatus, { color: string; label: string }> = {
  pending: { color: "var(--st-ink-3)", label: "Waiting" },
  running: { color: "var(--st-violet)", label: "Working" },
  done: { color: "var(--st-green)", label: "Done" },
  failed: { color: "var(--st-red)", label: "Failed" },
  skipped: { color: "var(--st-ink-3)", label: "Skipped" },
};

export function FlowPane() {
  return (
    <ReactFlowProvider>
      <FlowEditor />
    </ReactFlowProvider>
  );
}

type StepData = { node: FlowNode; agent?: Agent; status?: StepStatus; selected: boolean };

const StepCard = memo(function StepCard({ data }: NodeProps<Node<StepData>>) {
  const { node, agent, status, selected } = data;
  const hue = agent ? agentHue(agent) : 250;
  const look = status ? STATUS[status] : null;
  return (
    <div
      className="relative w-[230px] rounded-2xl border bg-[var(--st-glass-hi)] px-3.5 py-3 backdrop-blur-xl transition-shadow"
      style={{
        borderColor: selected ? "rgba(157,140,255,0.7)" : look ? look.color : "var(--st-edge-2)",
        boxShadow: status === "running" ? `0 0 0 3px rgba(157,140,255,0.18), 0 0 28px -6px ${hsl(hue, 80, 60)}` : undefined,
        opacity: status === "skipped" ? 0.45 : 1,
      }}
    >
      <Handle type="target" position={Position.Left} className="!size-2.5 !border-0 !bg-[var(--st-ink-3)]" />
      <div className="flex items-center gap-2">
        <span className="grid size-7 shrink-0 place-items-center rounded-lg text-[11px] font-semibold text-white" style={{ background: hsl(hue, 65, 50) }}>
          {agent ? initials(agent.name) : "?"}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium text-[var(--st-ink)]">{node.title}</div>
          <div className={`truncate text-[11px] ${ink3}`}>{agent?.name ?? "no agent"}</div>
        </div>
        {look && (
          <span className="flex items-center gap-1 text-[10.5px]" style={{ color: look.color }}>
            <span className={`size-1.5 rounded-full ${status === "running" ? "st-breathe" : ""}`} style={{ background: look.color }} />
            {look.label}
          </span>
        )}
      </div>
      {node.instructions && <div className={`mt-2 line-clamp-2 text-[11.5px] leading-snug ${ink3}`}>{node.instructions}</div>}
      <Handle type="source" position={Position.Right} className="!size-2.5 !border-0 !bg-[var(--st-violet)]" />
    </div>
  );
});

const nodeTypes = { step: StepCard };

function FlowEditor() {
  const { orgId } = useParams<{ orgId: string }>();
  const agentsMap = useOrg((s) => s.agents);
  const agents = useMemo(() => Object.values(agentsMap).filter((a) => !a.isSupervisor && !a.metaRole), [agentsMap]);
  const [flows, setFlows] = useState<Flow[] | null>(null);
  const [flowId, setFlowId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ name: string; graph: FlowGraph } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const rf = useReactFlow();

  const flow = flows?.find((f) => f.id === flowId) ?? null;
  const run = runs.find((r) => r.id === runId) ?? null;

  const loadFlows = useCallback(() => {
    api.flows(orgId!).then((fs) => {
      setFlows(fs);
      setFlowId((id) => id ?? fs[0]?.id ?? null);
    });
  }, [orgId]);
  useEffect(loadFlows, [loadFlows]);

  // The open flow's draft, reset when another is picked.
  useEffect(() => {
    if (!flow) return setDraft(null);
    setDraft({ name: flow.name, graph: flow.graph });
    setDirty(false);
    setSelected(null);
  }, [flow?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadRuns = useCallback(() => {
    if (!flowId) return;
    api.flowRuns(flowId).then((rs) => {
      setRuns(rs);
      setRunId((id) => (id && rs.some((r) => r.id === id) ? id : null));
    });
  }, [flowId]);
  useEffect(loadRuns, [loadRuns]);
  // Follow a run while it's going.
  useEffect(() => {
    if (run?.status !== "running") return;
    const t = setInterval(loadRuns, 1200);
    return () => clearInterval(t);
  }, [run?.status, loadRuns]);

  const graph = run ? run.graph : draft?.graph;
  const nodes: Node<StepData>[] = useMemo(
    () =>
      (graph?.nodes ?? []).map((n) => ({
        id: n.id,
        type: "step",
        position: { x: n.x, y: n.y },
        draggable: !run,
        selected: selected === n.id,
        data: { node: n, agent: agentsMap[n.agentId], status: run?.steps[n.id]?.status, selected: selected === n.id },
      })),
    [graph, agentsMap, run, selected],
  );
  const edges: Edge[] = useMemo(
    () =>
      (graph?.edges ?? []).map((e) => {
        const st = run?.steps[e.from]?.status;
        const live = st === "done" && run?.steps[e.to]?.status === "running";
        return {
          id: `${e.from}->${e.to}`,
          source: e.from,
          target: e.to,
          selected: selectedEdge === `${e.from}->${e.to}`,
          animated: live,
          markerEnd: { type: MarkerType.ArrowClosed, color: st === "done" ? "#3ddc97" : "#6b7290" },
          style: {
            stroke: selectedEdge === `${e.from}->${e.to}` ? "#9d8cff" : st === "done" ? "#3ddc97" : "#6b7290",
            strokeWidth: live || selectedEdge === `${e.from}->${e.to}` ? 2.4 : 1.5,
          },
        };
      }),
    [graph, run, selectedEdge],
  );

  const deleteStep = (id: string) => {
    edit((g) => ({ nodes: g.nodes.filter((n) => n.id !== id), edges: g.edges.filter((e) => e.from !== id && e.to !== id) }));
    setSelected(null);
  };
  const deleteEdge = (id: string) => {
    edit((g) => ({ ...g, edges: g.edges.filter((e) => `${e.from}->${e.to}` !== id) }));
    setSelectedEdge(null);
  };

  const edit = (fn: (g: FlowGraph) => FlowGraph) => {
    if (!draft || run) return;
    setDraft({ ...draft, graph: fn(draft.graph) });
    setDirty(true);
  };

  const addStep = () => {
    const center = rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
    const id = `step${Date.now().toString(36)}`;
    edit((g) => ({ ...g, nodes: [...g.nodes, { id, title: `Step ${g.nodes.length + 1}`, agentId: agents[0]?.id ?? "", instructions: "", x: center.x - 115, y: center.y - 40 }] }));
    setSelected(id);
  };

  const save = async () => {
    if (!flow || !draft) return;
    setError(null);
    try {
      const f = await api.updateFlow(flow.id, draft);
      setFlows((fs) => fs?.map((x) => (x.id === f.id ? f : x)) ?? null);
      setDraft({ name: f.name, graph: f.graph });
      setDirty(false);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const create = async () => {
    setError(null);
    try {
      const f = await api.createFlow(orgId!, { name: `Flow ${(flows?.length ?? 0) + 1}` });
      setFlows((fs) => [...(fs ?? []), f]);
      setFlowId(f.id);
      setRunId(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const start = async () => {
    if (!flow) return;
    setError(null);
    try {
      if (dirty) await save();
      const r = await api.startFlowRun(flow.id, input);
      setRuns((rs) => [r, ...rs]);
      setRunId(r.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Fit the steps to the pane when another flow or run is shown, steps come and go, or
  // the pane changes size (opened, maximized, split).
  const box = useRef<HTMLDivElement>(null);
  const count = graph?.nodes.length ?? 0;
  useEffect(() => {
    const fit = () => rf.fitView({ padding: 0.3, maxZoom: 1.1, duration: 250 });
    const t = setTimeout(fit, 60);
    let pending: ReturnType<typeof setTimeout> | undefined;
    const ro = new ResizeObserver(() => {
      clearTimeout(pending);
      pending = setTimeout(fit, 120);
    });
    if (box.current) ro.observe(box.current);
    return () => {
      clearTimeout(t);
      clearTimeout(pending);
      ro.disconnect();
    };
  }, [rf, flowId, runId, count]);

  const sel = graph?.nodes.find((n) => n.id === selected) ?? null;

  if (!flows) return <div className={`p-6 text-sm ${ink3}`}>Loading flows…</div>;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--st-edge)] px-3 py-2">
        <select className={`${field} h-8 w-48 py-0`} value={flowId ?? ""} onChange={(e) => (setFlowId(e.target.value || null), setRunId(null))}>
          {flows.length === 0 && <option value="">No flows yet</option>}
          {flows.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
        <button className={btn} onClick={create}>
          <Icon name="plus" size={13} /> New flow
        </button>
        {flow && draft && !run && (
          <>
            <span className="mx-1 h-5 w-px bg-[var(--st-edge)]" />
            <input className={`${field} h-8 w-44 py-0`} value={draft.name} onChange={(e) => (setDraft({ ...draft, name: e.target.value }), setDirty(true))} />
            <button className={btn} onClick={addStep} disabled={!agents.length} title={agents.length ? "Add a step" : "Hire agents first"}>
              <Icon name="plus" size={13} /> Step
            </button>
            <button className={btn} onClick={save} disabled={!dirty}>
              {dirty ? "Save" : "Saved"}
            </button>
          </>
        )}
        <div className="ml-auto flex items-center gap-2">
          {run ? (
            <>
              <span className="text-[12.5px]" style={{ color: run.status === "done" ? "var(--st-green)" : run.status === "running" ? "var(--st-violet)" : "var(--st-red)" }}>
                Run {run.number}: {run.status}
              </span>
              {run.status === "running" && (
                <button className={btn} onClick={() => api.cancelFlowRun(run.id).then(loadRuns)}>
                  Stop
                </button>
              )}
              <button className={btn} onClick={() => setRunId(null)}>
                Back to editing
              </button>
            </>
          ) : (
            flow && (
              <>
                <input className={`${field} h-8 w-64 py-0`} placeholder="The run's input (a question, a brief…)" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && start()} />
                <button className={primary} style={{ background: "linear-gradient(140deg,#9d8cff,#6c5cff)" }} onClick={start} disabled={!draft?.graph.nodes.length}>
                  <Icon name="play" size={12} /> Run
                </button>
              </>
            )
          )}
          {runs.length > 0 && (
            <select className={`${field} h-8 w-32 py-0`} value={runId ?? ""} onChange={(e) => setRunId(e.target.value || null)} title="Past runs">
              <option value="">Runs…</option>
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  Run {r.number} · {r.status}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
      <AnimatePresence>
        {error && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="border-b border-[var(--st-edge)] bg-[rgba(255,93,108,0.08)] px-4 py-2 text-[12.5px] text-[var(--st-red)]">
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex min-h-0 flex-1">
        <div ref={box} className="relative min-w-0 flex-1">
          {!flow ? (
            <Empty onCreate={create} />
          ) : (
            <ReactFlow
              className="st-canvas"
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.3, maxZoom: 1.1 }}
              minZoom={0.2}
              proOptions={{ hideAttribution: true }}
              nodesConnectable={!run}
              deleteKeyCode={run ? null : ["Backspace", "Delete"]}
              onNodeClick={(_, n) => (setSelected(n.id), setSelectedEdge(null))}
              onEdgeClick={(_, e) => (setSelectedEdge(e.id), setSelected(null))}
              onPaneClick={() => (setSelected(null), setSelectedEdge(null))}
              onNodeDragStop={(_, n) => edit((g) => ({ ...g, nodes: g.nodes.map((x) => (x.id === n.id ? { ...x, x: n.position.x, y: n.position.y } : x)) }))}
              onConnect={(c: Connection) =>
                c.source && c.target && c.source !== c.target && edit((g) => ({ ...g, edges: g.edges.some((e) => e.from === c.source && e.to === c.target) ? g.edges : [...g.edges, { from: c.source!, to: c.target! }] }))
              }
              onNodesDelete={(del) => del.forEach((d) => deleteStep(d.id))}
              onEdgesDelete={(del) => del.forEach((d) => deleteEdge(d.id))}
            >
              {selectedEdge && !run && (
                <div className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2">
                  <button className={btn} onClick={() => deleteEdge(selectedEdge)}>
                    Delete connection
                  </button>
                </div>
              )}
              <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="rgba(148,160,220,0.18)" />
              <Controls showInteractive={false} position="bottom-left" />
            </ReactFlow>
          )}
          {flow && !run && draft && draft.graph.nodes.length === 0 && (
            <div className={`pointer-events-none absolute inset-0 grid place-items-center text-center text-[13px] ${ink3}`}>
              <div>
                Add steps, give each an agent and what to do,
                <br />
                and drag from a step&apos;s right dot to the next step to hand its output on.
              </div>
            </div>
          )}
        </div>
        <AnimatePresence>
          {sel && (
            <motion.aside
              key="side"
              initial={{ x: 40, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 40, opacity: 0 }}
              transition={{ type: "spring", stiffness: 420, damping: 36 }}
              className="w-[340px] shrink-0 overflow-y-auto border-l border-[var(--st-edge)] p-4"
            >
              {run ? <StepResult run={run} node={sel} agent={agentsMap[sel.agentId]} /> : <StepEditor node={sel} agents={agents} onDelete={() => deleteStep(sel.id)} onChange={(patch) => edit((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === sel.id ? { ...n, ...patch } : n)) }))} />}
            </motion.aside>
          )}
        </AnimatePresence>
      </div>
      {run && <RunInput run={run} />}
    </div>
  );
}

function Empty({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="grid h-full place-items-center">
      <div className="max-w-sm text-center">
        <div className="st-display text-[18px]">Flows</div>
        <p className={`mt-2 text-[13px] leading-relaxed ${ink3}`}>
          Work as a graph of steps: each step is one agent doing one thing, and hands its result to the next. No noise, no loops, and you see every step as it runs.
        </p>
        <button className={`${primary} mt-4`} style={{ background: "linear-gradient(140deg,#9d8cff,#6c5cff)" }} onClick={onCreate}>
          <Icon name="plus" size={13} /> New flow
        </button>
      </div>
    </div>
  );
}

function StepEditor({ node, agents, onChange, onDelete }: { node: FlowNode; agents: Agent[]; onChange: (p: Partial<FlowNode>) => void; onDelete: () => void }) {
  return (
    <div className="grid gap-3">
      <div className={`text-[10.5px] uppercase tracking-[0.12em] ${ink3}`}>Step</div>
      <label className="grid gap-1 text-[12px] text-[var(--st-ink-2)]">
        Title
        <input className={`${field} w-full`} value={node.title} onChange={(e) => onChange({ title: e.target.value })} />
      </label>
      <label className="grid gap-1 text-[12px] text-[var(--st-ink-2)]">
        Who does it
        <select className={`${field} w-full`} value={node.agentId} onChange={(e) => onChange({ agentId: e.target.value })}>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} · {a.role}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1 text-[12px] text-[var(--st-ink-2)]">
        What to do
        <textarea className={`${field} w-full min-h-[180px] resize-y leading-relaxed`} value={node.instructions} onChange={(e) => onChange({ instructions: e.target.value })} placeholder="What this step does, and what its result should be. It gets the run's input and the results of the steps that feed it." />
      </label>
      <button className={`${btn} justify-center text-[var(--st-bad,#f2667a)]`} onClick={onDelete}>
        Delete step
      </button>
      <div className={`text-[11.5px] leading-relaxed ${ink3}`}>
        Or select a step or a connection (click it) and press Delete or Backspace. Save the flow to keep changes.
      </div>
    </div>
  );
}

function StepResult({ run, node, agent }: { run: FlowRun; node: FlowNode; agent?: Agent }) {
  const s = run.steps[node.id];
  const look = STATUS[s?.status ?? "pending"];
  return (
    <div className="grid gap-3">
      <div className={`text-[10.5px] uppercase tracking-[0.12em] ${ink3}`}>Run {run.number} · step</div>
      <div>
        <div className="text-[15px] font-medium">{node.title}</div>
        <div className={`text-[12px] ${ink3}`}>{agent?.name}</div>
      </div>
      <div className="flex items-center gap-2 text-[12.5px]" style={{ color: look.color }}>
        <span className="size-2 rounded-full" style={{ background: look.color }} /> {look.label}
        {s?.startedAt && <span className={`ml-auto st-mono text-[11px] ${ink3}`}>{new Date(s.startedAt).toLocaleTimeString()}</span>}
      </div>
      {s?.error && <div className="rounded-lg bg-[rgba(255,93,108,0.08)] p-3 text-[12.5px] text-[var(--st-red)]">{s.error}</div>}
      {s?.output ? (
        <div className="whitespace-pre-wrap rounded-lg border border-[var(--st-edge)] bg-black/20 p-3 text-[12.5px] leading-relaxed text-[var(--st-ink-2)]">{s.output}</div>
      ) : (
        !s?.error && <div className={`text-[12.5px] ${ink3}`}>{s?.status === "running" ? "Working on it…" : "No output yet."}</div>
      )}
    </div>
  );
}

function RunInput({ run }: { run: FlowRun }) {
  if (!run.input) return null;
  return (
    <div className={`border-t border-[var(--st-edge)] px-4 py-2 text-[12px] ${ink3}`}>
      Input: <span className="text-[var(--st-ink-2)]">{run.input}</span>
    </div>
  );
}
