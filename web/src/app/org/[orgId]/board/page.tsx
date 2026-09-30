"use client";


import { useShallow } from "zustand/react/shallow";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { AgentAvatar, UserAvatar } from "@/components/agent";
import { TaskDrawer } from "@/components/TaskDrawer";
import { Badge, Button, cx, ErrorNote, Field, Input, Modal, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Task, TaskStatus } from "@/lib/types";
import { useOrg } from "@/store/org";

const PRIORITY_TONE = { low: "neutral", normal: "info", high: "warn", urgent: "bad" } as const;

export default function BoardPage() {
  return (
    <Suspense>
      <Board />
    </Suspense>
  );
}

function Board() {
  const params = useSearchParams();
  const router = useRouter();
  const orgId = useOrg((s) => s.orgId)!;
  const boards = useOrg((s) => s.boards);
  const tasksMap = useOrg((s) => s.tasks);
  const agents = useOrg((s) => s.agents);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [assignee, setAssignee] = useState("");
  const [creating, setCreating] = useState<TaskStatus | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const openTask = params.get("task");

  const board = boards.find((b) => b.id === boardId) ?? boards[0];
  const tasks = useMemo(
    () =>
      Object.values(tasksMap)
        .filter((t) => !board || t.boardId === board.id)
        .filter((t) => !assignee || t.assigneeId === assignee)
        .filter((t) => !filter || `${t.ref} ${t.title} ${t.description} ${t.labels.join(" ")}`.toLowerCase().includes(filter.toLowerCase()))
        .sort((a, b) => a.position - b.position),
    [tasksMap, board, filter, assignee],
  );
  if (!board) return null;

  const move = async (taskId: string, status: TaskStatus) => {
    const t = tasksMap[taskId];
    if (!t || t.status === status) return;
    setError(null);
    useOrg.setState((s) => ({ tasks: { ...s.tasks, [taskId]: { ...t, status } } })); // optimistic
    try {
      await api.updateTask(taskId, { status });
    } catch (e) {
      useOrg.setState((s) => ({ tasks: { ...s.tasks, [taskId]: t } }));
      setError((e as Error).message);
    }
  };

  const setOpen = (id: string | null) => {
    const q = new URLSearchParams(params.toString());
    if (id) q.set("task", id);
    else q.delete("task");
    router.replace(`?${q}`);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5 md:flex-nowrap md:gap-3 md:px-5 md:py-3">
        {boards.length > 1 && (
          <Select className="w-full sm:w-44" value={board.id} onChange={(e) => setBoardId(e.target.value)}>
            {boards.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>
        )}
        <Input className="min-w-0 flex-1 md:w-64 md:flex-none" placeholder="Filter tasks…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <Select className="w-32 md:w-48" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">Everyone</option>
          {Object.values(agents)
            .filter((a) => !a.isSupervisor)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </Select>
        <div className="ml-auto flex items-center gap-2">
          <span className="whitespace-nowrap text-xs text-ink-3">{tasks.length} tasks</span>
          <Button variant="primary" onClick={() => setCreating("todo")}>
            New task
          </Button>
        </div>
      </div>
      {error && (
        <div className="px-5 pt-3">
          <ErrorNote error={error} />
        </div>
      )}
      <div className="flex min-h-0 flex-1 snap-x snap-mandatory gap-3 overflow-x-auto p-3 md:snap-none md:p-4">
        {board.columns.map((col) => {
          const items = tasks.filter((t) => t.status === col.key);
          return (
            <div
              key={col.key}
              className={cx("flex w-[84vw] shrink-0 snap-center flex-col rounded-xl border bg-panel/60 md:w-auto md:min-w-[200px] md:flex-1 md:shrink", dragging ? "border-line-2" : "border-line")}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                const id = e.dataTransfer.getData("text/task");
                if (id) void move(id, col.key);
                setDragging(null);
              }}
            >
              <div className="flex items-center justify-between px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <span className={cx("size-2 rounded-full", COLUMN_DOT[col.key])} />
                  {col.name}
                  <span className="text-xs text-ink-3">{items.length}</span>
                </div>
                <button className="text-ink-3 hover:text-ink cursor-pointer" onClick={() => setCreating(col.key)} title="Add task">
                  +
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 pb-2">
                {items.map((t) => (
                  <TaskCard key={t.id} task={t} onOpen={() => setOpen(t.id)} onDrag={setDragging} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {creating && <NewTask orgId={orgId} boardId={board.id} status={creating} onClose={() => setCreating(null)} />}
      <TaskDrawer taskId={openTask} onClose={() => setOpen(null)} />
    </div>
  );
}

const COLUMN_DOT: Record<TaskStatus, string> = {
  backlog: "bg-ink-3",
  todo: "bg-ink-2",
  in_progress: "bg-accent",
  blocked: "bg-bad",
  review: "bg-warn",
  done: "bg-ok",
  cancelled: "bg-ink-3",
};

function TaskCard({ task, onOpen, onDrag }: { task: Task; onOpen: () => void; onDrag: (id: string | null) => void }) {
  const assignee = useOrg((s) => (task.assigneeId ? s.agents[task.assigneeId] : undefined));
  const tasks = useOrg((s) => s.tasks);
  const openDeps = task.dependsOn.filter((d) => tasks[d] && !["done", "cancelled"].includes(tasks[d].status));
  const working = useOrg((s) => (task.assigneeId ? s.live[task.assigneeId]?.status === "working" : false));
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/task", task.id);
        onDrag(task.id);
      }}
      onDragEnd={() => onDrag(null)}
      onClick={onOpen}
      className="group cursor-pointer rounded-lg border border-line bg-panel-2 p-3 transition-colors hover:border-line-2"
    >
      <div className="flex items-center gap-2 text-[11px] text-ink-3">
        <span className="font-mono">{task.ref}</span>
        {task.priority !== "normal" && <Badge tone={PRIORITY_TONE[task.priority]}>{task.priority}</Badge>}
        {openDeps.length > 0 && <Badge tone="warn">waits on {openDeps.map((d) => tasks[d].ref).join(", ")}</Badge>}
        <span className="ml-auto">{timeAgo(task.updatedAt)}</span>
      </div>
      <div className="mt-1.5 text-sm leading-snug">{task.title}</div>
      {task.result && task.status !== "todo" && <div className="mt-1.5 line-clamp-2 text-xs text-ink-3">{task.result}</div>}
      <div className="mt-2.5 flex items-center gap-2">
        {task.assigneeId === "user" ? (
          <UserAvatar size={20} />
        ) : assignee ? (
          <AgentAvatar agent={assignee} size={20} status={working ? "working" : undefined} />
        ) : (
          <span className="text-xs text-ink-3">Unassigned</span>
        )}
        {assignee && <span className="text-xs text-ink-2">{assignee.name}</span>}
        <div className="ml-auto flex gap-1">
          {task.labels.slice(0, 2).map((l) => (
            <Badge key={l}>{l}</Badge>
          ))}
        </div>
      </div>
    </div>
  );
}

function NewTask({ orgId, boardId, status, onClose }: { orgId: string; boardId: string; status: TaskStatus; onClose: () => void }) {
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const [form, setForm] = useState({ title: "", description: "", acceptance: "", assigneeId: "", priority: "normal" as Task["priority"] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    try {
      await api.createTask(orgId, {
        ...form,
        assigneeId: form.assigneeId || null,
        boardId,
        status: form.assigneeId && status === "backlog" ? "todo" : status,
      });
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
      title="New task"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={busy} disabled={!form.title.trim()}>
            Create
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Title">
          <Input autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </Field>
        <Field label="Description">
          <Textarea rows={5} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <Field label="Acceptance criteria" hint="How the reviewer knows it's done">
          <Textarea rows={3} value={form.acceptance} onChange={(e) => setForm({ ...form, acceptance: e.target.value })} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Assignee" hint="They'll be notified">
            <Select value={form.assigneeId} onChange={(e) => setForm({ ...form, assigneeId: e.target.value })}>
              <option value="">Unassigned</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} — {a.role}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Priority">
            <Select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value as Task["priority"] })}>
              {["low", "normal", "high", "urgent"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </Select>
          </Field>
        </div>
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}