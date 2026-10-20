"use client";

/** Benchmarks: run SwarmBench tasks on copies of an organization, and compare organizations. */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, cx, Empty, ErrorNote, Field, Input, Select, Spinner } from "@/components/ui";
import { api, type BenchImport, type BenchResult, type BenchRun, type BenchTask, type BenchWorkflow, type Tuning } from "@/lib/api";
import type { Org } from "@/lib/types";

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "neutral" | "info"> = {
  running: "info",
  done: "ok",
  cancelled: "neutral",
  failed: "bad",
};

const money = (v: number | null | undefined) => (v ? `$${v < 1 ? v.toFixed(3) : v.toFixed(2)}` : "–");
const kTokens = (v: number | null | undefined) => (v ? (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : String(v)) : "–");
const minutes = (s: number) => (s >= 60 ? `${(s / 60).toFixed(1)} min` : `${Math.round(s)} s`);
const scoreColor = (s: number) => (s >= 90 ? "text-ok" : s >= 50 ? "text-warn" : "text-bad");

export default function BenchmarksPage() {
  const [tasks, setTasks] = useState<BenchTask[]>([]);
  const [workflows, setWorkflows] = useState<BenchWorkflow[]>([]);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [runs, setRuns] = useState<BenchRun[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [compare, setCompare] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.benchRuns().then(setRuns).catch((e) => setError((e as Error).message));
  }, []);
  useEffect(() => {
    api.benchTasks().then((c) => {
      setTasks(c.tasks);
      setWorkflows(c.workflows);
    });
    api.orgs().then(setOrgs).catch(() => {});
    load();
  }, [load]);
  const running = runs.some((r) => r.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  const chosen = runs.filter((r) => compare.includes(r.id));
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6 md:px-6 md:py-8">
        <h1 className="text-xl font-semibold">Benchmarks</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink-2">
          Measure how well an organization works. Each task runs on a temporary copy of the organization (same agents, models, tools and reporting
          lines, every tool approved automatically) in a fresh work folder; the lead gets the task, and when the team goes quiet SwarmBench grades
          the folder: with hidden tests, or for imported professional tasks (JobBench, LAB) with a judge that checks each rubric item. Tick runs to
          compare them.
        </p>
        <ErrorNote error={error} />
        <NewRun tasks={tasks} workflows={workflows} orgs={orgs} onStarted={load} />
        <Imports onImported={() => void api.benchTasks().then((c) => setTasks(c.tasks))} />
        <Tunings tasks={tasks} orgs={orgs} />
        {chosen.length >= 2 && <Compare runs={chosen} onClear={() => setCompare([])} />}
        <div className="mt-6 space-y-2">
          {runs.length === 0 ? (
            <Empty title="No benchmark runs yet">Pick an organization and some tasks above.</Empty>
          ) : (
            runs.map((r) => (
              <RunRow
                key={r.id}
                run={r}
                open={open === r.id}
                onToggle={() => setOpen(open === r.id ? null : r.id)}
                selected={compare.includes(r.id)}
                onSelect={(on) => setCompare((c) => (on ? [...c, r.id] : c.filter((x) => x !== r.id)))}
                onChanged={load}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function NewRun({ tasks, workflows, orgs, onStarted }: { tasks: BenchTask[]; workflows: BenchWorkflow[]; orgs: Org[]; onStarted: () => void }) {
  const [orgId, setOrgId] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [workflowPick, setWorkflow] = useState<string | null>(null);
  const [repeat, setRepeat] = useState(1);
  const [jobs, setJobs] = useState(1);
  const [timeout, setTimeoutMin] = useState("");
  const [lead, setLead] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const org = orgId || orgs[0]?.id || "";
  const solo = (orgs.find((o) => o.id === org)?.agentCount ?? 0) === 1;
  // One agent can't fill a team workflow's roles: default to the solo workflow.
  const workflow = workflowPick ?? (solo ? "solo" : "task");
  const minutesBudget = tasks.filter((t) => picked.includes(t.id)).reduce((s, t) => s + t.minutes, 0) * repeat;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.startBenchRun({
        orgId: org,
        tasks: picked,
        workflow,
        repeat,
        jobs,
        timeoutMinutes: timeout ? Number(timeout) : undefined,
        lead: lead.trim() || undefined,
      });
      onStarted();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="mt-5 space-y-4 p-5 text-sm">
      <div className="font-semibold">New run</div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Organization">
          <Select
            value={org}
            onChange={(e) => {
              setOrgId(e.target.value);
              setWorkflow(null);
            }}
          >
            {orgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Workflow"
          hint={
            solo
              ? "One agent: the solo workflow (team workflows ask for roles it doesn't have)"
              : "To compare with one agent, use the same workflow for both: team workflows ask for extra write-ups, and those are scored"
          }
        >
          <Select value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
            <option value="task">Each task&apos;s own workflow</option>
            <option value="none">None (their own way)</option>
            {workflows.map((w) => (
              <option key={w.name} value={w.name}>
                {w.name} ({w.roles.join(", ")})
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Lead" hint="Who gets the task (default: the top of the org chart)">
          <Input value={lead} onChange={(e) => setLead(e.target.value)} placeholder="automatic" />
        </Field>
        <Field label="Repeats" hint="Agents vary; repeat to see the spread">
          <Input type="number" min={1} max={10} value={repeat} onChange={(e) => setRepeat(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} />
        </Field>
        <Field label="At once" hint="Tasks running in parallel (each on its own copy)">
          <Input type="number" min={1} max={8} value={jobs} onChange={(e) => setJobs(Math.max(1, Math.min(8, Number(e.target.value) || 1)))} />
        </Field>
        <Field label="Time limit per task (min)" hint="Default: twice the task's budget">
          <Input type="number" min={1} value={timeout} onChange={(e) => setTimeoutMin(e.target.value)} placeholder="automatic" />
        </Field>
      </div>
      <div>
        <div className="mb-1.5 flex items-center gap-3 text-xs text-ink-3">
          <span>Tasks</span>
          <button className="text-info hover:underline cursor-pointer" onClick={() => setPicked(tasks.map((t) => t.id))}>
            all
          </button>
          <button className="text-info hover:underline cursor-pointer" onClick={() => setPicked([])}>
            none
          </button>
        </div>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {tasks.map((t) => (
            <label key={t.id} className={cx("flex cursor-pointer gap-2 rounded-md border px-3 py-2", picked.includes(t.id) ? "border-accent/60 bg-accent/10" : "border-line hover:border-line-2")}>
              <input
                type="checkbox"
                className="mt-0.5 accent-[#8b7cff]"
                checked={picked.includes(t.id)}
                onChange={(e) => setPicked((p) => (e.target.checked ? [...p, t.id] : p.filter((x) => x !== t.id)))}
              />
              <span className="min-w-0">
                <span className="font-medium">{t.title}</span>
                <span className="ml-2 text-xs text-ink-3">
                  {t.source && t.source !== "SwarmBench" ? `${t.source} · ` : ""}
                  {t.category} · {t.difficulty} · ~{t.minutes} min
                </span>
                <span className="block text-xs text-ink-3">{t.summary}</span>
              </span>
            </label>
          ))}
        </div>
      </div>
      <ErrorNote error={error} />
      <div className="flex items-center gap-3">
        <Button variant="primary" loading={busy} disabled={!org || picked.length === 0} onClick={() => void start()}>
          Start benchmark
        </Button>
        {picked.length > 0 && (
          <span className="text-xs text-ink-3">
            {picked.length * repeat} task run{picked.length * repeat === 1 ? "" : "s"}, about {Math.round(minutesBudget / Math.max(1, jobs))} min of budget. Uses
            your model provider like normal work.
          </span>
        )}
      </div>
    </Card>
  );
}

function RunRow({
  run,
  open,
  onToggle,
  selected,
  onSelect,
  onChanged,
}: {
  run: BenchRun;
  open: boolean;
  onToggle: () => void;
  selected: boolean;
  onSelect: (on: boolean) => void;
  onChanged: () => void;
}) {
  const s = run.summary;
  return (
    <Card className="text-sm">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <input type="checkbox" className="accent-[#8b7cff]" title="Compare" checked={selected} onChange={(e) => onSelect(e.target.checked)} />
        <button className="flex min-w-0 flex-1 items-center gap-3 text-left cursor-pointer" onClick={onToggle}>
          <span className="truncate font-medium">{run.orgName}</span>
          <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>
          <span className="text-xs text-ink-3">
            {run.config.workflow === "task" ? "task workflows" : run.config.workflow} · {run.progress.done}/{run.progress.total} tasks
            {run.config.repeat > 1 ? ` · ×${run.config.repeat}` : ""}
          </span>
          {run.status === "running" && <Spinner className="size-3.5" />}
        </button>
        <span className={cx("w-16 text-right font-semibold tabular-nums", s.meanScore != null && scoreColor(s.meanScore))}>{s.meanScore ?? "–"}</span>
        <span className="w-16 text-right tabular-nums text-ink-2">{money(s.costUsd)}</span>
        <span className="w-14 text-right tabular-nums text-ink-3">{kTokens(s.tokens)}</span>
        <span className="w-20 text-right text-xs text-ink-3">{run.createdAt ? new Date(run.createdAt).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : ""}</span>
      </div>
      {open && (
        <div className="space-y-3 border-t border-line px-4 py-3">
          {run.progress.current.map((a) => (
            <div key={a.id} className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
              <Spinner className="size-3" /> <b>{a.label}</b> {a.status}: {a.detail} · {minutes(a.seconds)} · {a.metrics.turns ?? 0} turns ·{" "}
              {a.metrics.tool_calls ?? 0} tool calls · {money(a.metrics.cost_usd)}
              {a.cloneId && (
                <Link href={`/org/${a.cloneId}`} target="_blank" className="text-info hover:underline">
                  Watch live ↗
                </Link>
              )}
            </div>
          ))}
          {run.status === "running" && (
            <div className="text-xs text-ink-3">
              Tasks run on a hidden copy of {run.orgName}, so your own agents stay idle. Use Watch live to see the copy&apos;s office, chats and board.
            </div>
          )}
          {run.detail && run.status !== "running" && <div className="text-xs text-ink-3">{run.detail}</div>}
          <Results results={run.results} />
          <div className="flex gap-2">
            {run.status === "running" ? (
              <Button size="sm" variant="danger" onClick={() => void api.cancelBenchRun(run.id).then(onChanged)}>
                Cancel
              </Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => void api.deleteBenchRun(run.id).then(onChanged)}>
                Delete
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

/** What graded the work: hidden tests passed, or a rubric's items fully met. */
function checked(r: BenchResult) {
  const h = r.parts.hidden_tests;
  if (h) return `${h.passed}/${h.total} tests`;
  const rub = r.parts.rubric as { items_met?: number; items?: unknown[] } | null | undefined;
  return rub ? `${rub.items_met ?? 0}/${rub.items?.length ?? 0} rubric items` : "–";
}

function part(r: BenchResult, name: string) {
  const p = r.parts[name];
  return p == null || !(name in r.weights) ? "–" : String(Math.round(p.score * 100));
}

function Results({ results }: { results: BenchResult[] }) {
  if (!results.length) return <div className="text-xs text-ink-3">No tasks graded yet.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-ink-3">
          <tr className="text-left">
            {["Task", "Workflow", "Score", "Checked", "Agent tests", "Safety", "Process", "Time", "Turns", "Agents", "Tokens", "Cost", "Ended"].map((h) => (
              <th key={h} className="px-2 py-1 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.task_key} className="border-t border-line align-top">
              <td className="px-2 py-1.5">
                <div className="font-medium">{r.title}</div>
                <div className="text-ink-3">{r.task_key}</div>
              </td>
              <td className="px-2 py-1.5 text-ink-2">{r.workflow ?? "none"}</td>
              <td className={cx("px-2 py-1.5 font-semibold tabular-nums", scoreColor(r.score))}>{r.score.toFixed(1)}</td>
              <td className="px-2 py-1.5 tabular-nums">{checked(r)}</td>
              <td className="px-2 py-1.5 tabular-nums">{part(r, "agent_tests")}</td>
              <td className="px-2 py-1.5 tabular-nums">{part(r, "safety")}</td>
              <td className="px-2 py-1.5 tabular-nums">{part(r, "process")}</td>
              <td className="px-2 py-1.5 tabular-nums">{minutes(r.run.wall_seconds)}</td>
              <td className="px-2 py-1.5 tabular-nums">{r.metrics.turns ?? "–"}</td>
              <td className="px-2 py-1.5 tabular-nums">
                {r.metrics.agents_active ?? "–"}/{r.metrics.agents ?? "–"}
              </td>
              <td className="px-2 py-1.5 tabular-nums">{kTokens((r.metrics.input_tokens ?? 0) + (r.metrics.output_tokens ?? 0))}</td>
              <td className="px-2 py-1.5 tabular-nums">{money(r.metrics.cost_usd)}</td>
              <td className="px-2 py-1.5">
                <Badge tone={r.run.status === "done" ? "ok" : r.run.status === "timeout" ? "warn" : "bad"}>{r.run.status}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Importing professional benchmarks (JobBench, LAB): their tasks are graded by a rubric. */
function Imports({ onImported }: { onImported: () => void }) {
  const [state, setState] = useState<BenchImport | null>(null);
  const [limit, setLimit] = useState("");
  const [areas, setAreas] = useState("contracts");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.benchImport().then(setState).catch(() => {});
  }, []);
  useEffect(() => {
    if (!state?.running) return;
    const t = setInterval(() => void api.benchImport().then(setState), 2000);
    return () => clearInterval(t);
  }, [state?.running]);

  const start = async (benchmark: "jobbench" | "lab") => {
    setError(null);
    const req = api.startBenchImport({ benchmark, limit: limit ? Number(limit) : undefined, areas: benchmark === "lab" ? areas : undefined });
    setState({ running: true, detail: `importing ${benchmark}`, made: [] });
    try {
      setState(await req);
      onImported();
    } catch (e) {
      setError((e as Error).message);
      void api.benchImport().then(setState);
    }
  };

  return (
    <Card className="mt-4 space-y-3 p-5 text-sm">
      <div className="font-semibold">Professional benchmarks</div>
      <p className="max-w-3xl text-xs text-ink-2">
        JobBench (65 tasks from about 35 professions: reconciliations, reports, analyses) and LAB (Harvey&apos;s legal tasks by practice area), both
        MIT-licensed. A judge on your checker model (or Zeus&apos;s) checks each rubric item; the rubrics stay out of the agents&apos; folders, and
        the hosts where they are public are blocked during runs. LAB is about 2 GB, so import practice areas.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="At most (tasks)">
          <Input type="number" min={1} value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="all" className="w-28" />
        </Field>
        <Button size="sm" loading={state?.running} onClick={() => void start("jobbench")}>
          Import JobBench
        </Button>
        <Field label="LAB practice areas">
          <Input value={areas} onChange={(e) => setAreas(e.target.value)} placeholder="contracts,tax" className="w-48" />
        </Field>
        <Button size="sm" loading={state?.running} disabled={!areas.trim()} onClick={() => void start("lab")}>
          Import LAB
        </Button>
      </div>
      {state?.detail && <div className="text-xs text-ink-3">{state.detail}</div>}
      <ErrorNote error={error} />
    </Card>
  );
}

const TUNE_TONE: Record<string, "ok" | "warn" | "bad" | "neutral" | "info"> = { ...STATUS_TONE, applied: "ok" };

/** Team tuning (propose, probe, compose): the team as it is, two edited versions, the composed one. */
function Tunings({ tasks, orgs }: { tasks: BenchTask[]; orgs: Org[] }) {
  const [list, setList] = useState<Tuning[]>([]);
  const [orgId, setOrgId] = useState("");
  const [task, setTask] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<Record<string, string>>({});
  const load = useCallback(() => void api.tunings().then(setList).catch(() => {}), []);
  useEffect(load, [load]);
  const running = list.some((t) => t.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [running, load]);
  const org = orgId || orgs[0]?.id || "";
  const pick = task || tasks[0]?.id || "";

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.startTuning({ orgId: org, task: pick });
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const apply = async (id: string) => {
    try {
      const r = await api.applyTuning(id);
      setApplied((a) => ({ ...a, [id]: r.changed.join("; ") || "nothing differed" }));
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card className="mt-4 space-y-3 p-5 text-sm">
      <div className="font-semibold">Tune a team</div>
      <p className="max-w-3xl text-xs text-ink-2">
        Runs the organization on one task as it is, has two editors propose changes from what the run showed (one on how the team works, one on
        the field&apos;s know-how as skills), runs both changed teams, adopts the changes the runs support and stores the lesson in the experience
        library Zeus designs from. About four runs of the task. Your organization changes only when you apply the result. (After harness2, Google
        Research.)
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Organization">
          <Select value={org} onChange={(e) => setOrgId(e.target.value)}>
            {orgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Task">
          <Select value={pick} onChange={(e) => setTask(e.target.value)} className="max-w-sm">
            {tasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title} ({t.source && t.source !== "SwarmBench" ? t.source : t.category})
              </option>
            ))}
          </Select>
        </Field>
        <Button variant="primary" size="sm" loading={busy} disabled={!org || !pick || running} onClick={() => void start()}>
          Start tuning
        </Button>
      </div>
      <ErrorNote error={error} />
      {list.map((t) => (
        <div key={t.id} className="space-y-2 rounded-md border border-line px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{t.org_name}</span>
            <span className="text-xs text-ink-3">on {t.task_id}</span>
            <Badge tone={TUNE_TONE[t.status] ?? "neutral"}>{t.status === "running" ? t.stage : t.status}</Badge>
            {t.status === "running" && <Spinner className="size-3.5" />}
            <span className="text-xs text-ink-3">{t.detail}</span>
          </div>
          <div className="flex flex-wrap gap-4 text-xs">
            {(["base", "inv", "dom", "final"] as const)
              .filter((k) => t.runs[k])
              .map((k) => (
                <span key={k} title={t.runs[k].detail}>
                  <b>{{ base: "As it is", inv: "How it works", dom: "Field know-how", final: "Composed" }[k]}</b>{" "}
                  <span className={cx("tabular-nums", t.runs[k].score != null && scoreColor(t.runs[k].score!))}>{t.runs[k].score ?? "…"}</span>
                  {t.runs[k].minutes != null && <span className="text-ink-3"> · {t.runs[k].minutes} min</span>}
                </span>
              ))}
          </div>
          {t.edits.length > 0 && (
            <ul className="space-y-0.5 text-xs">
              {t.edits.map((e) => (
                <li key={e.id} className={cx(t.adopted.includes(e.id) ? "text-ink" : "text-ink-3")}>
                  <b>{t.adopted.includes(e.id) ? "✓" : "·"}</b> {e.id} {e.kind}
                  {e.agent ? ` (${e.agent})` : ""}: {e.summary} {t.verdicts[e.id] && <span className="text-ink-3">· {t.verdicts[e.id]}</span>}
                </li>
              ))}
            </ul>
          )}
          {t.why && <div className="text-xs text-ink-2">{t.why}</div>}
          {t.lesson?.lesson && (
            <div className="text-xs text-ink-2">
              <b>Lesson</b> ({t.lesson.confidence ?? "?"}): when {t.lesson.cue}, {t.lesson.lesson}
            </div>
          )}
          <div className="flex items-center gap-2">
            {t.status === "running" && (
              <Button size="sm" variant="danger" onClick={() => void api.cancelTuning(t.id).then(load)}>
                Cancel
              </Button>
            )}
            {t.status === "done" && t.adopted.length > 0 && (
              <Button size="sm" onClick={() => void apply(t.id)}>
                Apply to {t.org_name}
              </Button>
            )}
            {applied[t.id] && <span className="text-xs text-ok">Applied: {applied[t.id]}</span>}
          </div>
        </div>
      ))}
    </Card>
  );
}

function Compare({ runs, onClear }: { runs: BenchRun[]; onClear: () => void }) {
  const taskIds = useMemo(() => [...new Set(runs.flatMap((r) => r.results.map((x) => x.task_id)))].sort(), [runs]);
  const mean = (r: BenchRun, task: string) => {
    const xs = r.results.filter((x) => x.task_id === task).map((x) => x.score);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const best = Math.max(...runs.map((r) => r.summary.meanScore ?? 0), 1);
  return (
    <Card className="mt-5 space-y-4 p-5 text-sm">
      <div className="flex items-center justify-between">
        <div className="font-semibold">Comparison</div>
        <Button size="sm" variant="ghost" onClick={onClear}>
          Clear
        </Button>
      </div>
      <div className="space-y-1.5">
        {runs.map((r, i) => (
          <div key={r.id} className="flex items-center gap-3">
            <span className="w-40 truncate text-xs">
              <b>R{i + 1}</b> {r.orgName}
            </span>
            <div className="h-3 flex-1 overflow-hidden rounded bg-panel-2">
              <div className="h-full rounded bg-accent" style={{ width: `${((r.summary.meanScore ?? 0) / best) * 100}%` }} />
            </div>
            <span className="w-12 text-right font-semibold tabular-nums">{r.summary.meanScore ?? "–"}</span>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-ink-3">
            <tr className="text-left">
              <th className="px-2 py-1 font-medium">Task</th>
              {runs.map((r, i) => (
                <th key={r.id} className="px-2 py-1 text-right font-medium" title={r.orgName}>
                  R{i + 1}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {taskIds.map((t) => {
              const values = runs.map((r) => mean(r, t));
              const top = Math.max(...values.map((v) => v ?? -1));
              return (
                <tr key={t} className="border-t border-line">
                  <td className="px-2 py-1.5">{t}</td>
                  {values.map((v, i) => (
                    <td key={i} className={cx("px-2 py-1.5 text-right tabular-nums", v != null && v === top && "font-semibold text-ok")}>
                      {v == null ? "–" : v.toFixed(1)}
                    </td>
                  ))}
                </tr>
              );
            })}
            {(
              [
                ["Mean score", (r: BenchRun) => r.summary.meanScore?.toFixed(1) ?? "–"],
                ["Full scores", (r: BenchRun) => `${r.summary.fullScores}/${r.results.length}`],
                ["Time", (r: BenchRun) => minutes(r.summary.wallSeconds)],
                ["Tokens", (r: BenchRun) => kTokens(r.summary.tokens)],
                ["Cost", (r: BenchRun) => money(r.summary.costUsd)],
                [
                  "Points per $",
                  (r: BenchRun) => (r.summary.costUsd ? Math.round(r.results.reduce((a, x) => a + x.score, 0) / r.summary.costUsd).toString() : "–"),
                ],
                ["Timeouts", (r: BenchRun) => String(r.summary.timeouts)],
                ["Model calls", (r: BenchRun) => String(r.summary.modelCalls ?? "–")],
                ["Model time", (r: BenchRun) => (r.summary.modelMinutes != null ? `${r.summary.modelMinutes} min` : "–")],
                ["From the server's cache", (r: BenchRun) => (r.summary.cachedPct != null ? `${r.summary.cachedPct}%` : "–")],
                ["Tool calls", (r: BenchRun) => String(r.summary.toolCalls ?? "–")],
                ["Messages", (r: BenchRun) => String(r.summary.messages ?? "–")],
                ["Edits failed", (r: BenchRun) => (r.summary.edits ? `${r.summary.editFailures} of ${r.summary.edits}` : "–")],
                ["Subagents", (r: BenchRun) => String(r.summary.subagents ?? "–")],
                ["Acceptance checks", (r: BenchRun) => String(r.summary.acceptanceChecks ?? "–")],
              ] as [string, (r: BenchRun) => string][]
            ).map(([label, fn]) => (
              <tr key={label} className="border-t border-line bg-panel-2/40">
                <td className="px-2 py-1.5 font-medium">{label}</td>
                {runs.map((r) => (
                  <td key={r.id} className="px-2 py-1.5 text-right tabular-nums">
                    {fn(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
