"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Message, Task, TaskEvent, TaskStatus } from "@/lib/types";
import { agentName, useOrg } from "@/store/org";
import { AgentAvatar, UserAvatar } from "./agent";
import { Markdown } from "./Markdown";
import { Badge, Button, Drawer, ErrorNote, Field, Select, Spinner, Textarea } from "./ui";

const STATUSES: TaskStatus[] = ["backlog", "todo", "in_progress", "blocked", "review", "done", "cancelled"];

export function TaskDrawer({ taskId, onClose }: { taskId: string | null; onClose: () => void }) {
  const live = useOrg((s) => (taskId ? s.tasks[taskId] : undefined));
  const agents = useOrg((s) => s.agents);
  const tasks = useOrg((s) => s.tasks);
  const [detail, setDetail] = useState<(Task & { timeline: TaskEvent[]; messages: Message[] }) | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (taskId) api.task(taskId).then(setDetail).catch((e) => setError(e.message));
  }, [taskId]);
  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);
  useEffect(() => {
    if (live && detail && live.updatedAt !== detail.updatedAt) load();
  }, [live, detail, load]);

  const t = live ?? detail;
  const update = async (patch: Partial<Task> & { comment?: string }) => {
    if (!t) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateTask(t.id, patch);
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer open={!!taskId} onClose={onClose} width="w-[560px]">
      {!t ? (
        <div className="flex h-40 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <div className="flex flex-col">
          <div className="border-b border-line p-5">
            <div className="flex items-center gap-2 text-xs text-ink-3">
              <span className="font-mono">{t.ref}</span>
              <span>created by {agentName(t.reporterId)}</span>
              <button className="ml-auto text-ink-3 hover:text-ink cursor-pointer" onClick={onClose}>
                ✕
              </button>
            </div>
            <h2 className="mt-1.5 text-lg font-semibold leading-snug">{t.title}</h2>
            <div className="mt-4 grid grid-cols-3 gap-3">
              <Field label="Status">
                <Select value={t.status} onChange={(e) => update({ status: e.target.value as TaskStatus })} disabled={busy}>
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s.replace("_", " ")}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Assignee">
                <Select value={t.assigneeId ?? ""} onChange={(e) => update({ assigneeId: e.target.value || null })} disabled={busy}>
                  <option value="">Unassigned</option>
                  <option value="user">You</option>
                  {Object.values(agents)
                    .filter((a) => !a.isSupervisor)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </Select>
              </Field>
              <Field label="Priority">
                <Select value={t.priority} onChange={(e) => update({ priority: e.target.value as Task["priority"] })} disabled={busy}>
                  {["low", "normal", "high", "urgent"].map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </Select>
              </Field>
            </div>
            <ErrorNote error={error} />
          </div>
          <div className="space-y-5 p-5">
            <Section title="Description">{t.description ? <Markdown>{t.description}</Markdown> : <Muted>None</Muted>}</Section>
            {t.acceptance && (
              <Section title="Acceptance criteria">
                <Markdown>{t.acceptance}</Markdown>
              </Section>
            )}
            {t.result && (
              <Section title="Result">
                <div className="rounded-md border border-ok/25 bg-ok/5 p-3">
                  <Markdown>{t.result}</Markdown>
                </div>
              </Section>
            )}
            {(t.dependsOn.length > 0 || t.parentId) && (
              <Section title="Links">
                <div className="flex flex-wrap gap-2 text-sm">
                  {t.parentId && tasks[t.parentId] && <Badge tone="info">parent {tasks[t.parentId].ref}</Badge>}
                  {t.dependsOn.map((d) =>
                    tasks[d] ? (
                      <Badge key={d} tone={tasks[d].status === "done" ? "ok" : "warn"}>
                        depends on {tasks[d].ref} ({tasks[d].status})
                      </Badge>
                    ) : null,
                  )}
                </div>
              </Section>
            )}
            <Section title="History">
              {!detail ? (
                <Spinner />
              ) : (
                <div className="space-y-3">
                  {detail.timeline.map((ev) => (
                    <TimelineRow key={ev.id} ev={ev} />
                  ))}
                </div>
              )}
            </Section>
            <div>
              <Textarea rows={3} placeholder="Add a comment (the assignee and reporter are notified)…" value={comment} onChange={(e) => setComment(e.target.value)} />
              <div className="mt-2 flex justify-end">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={!comment.trim()}
                  loading={busy}
                  onClick={() => update({ comment }).then(() => setComment(""))}
                >
                  Comment
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </Drawer>
  );
}

function TimelineRow({ ev }: { ev: TaskEvent }) {
  const agent = useOrg((s) => s.agents[ev.actorId]);
  const who = ev.actorId === "user" ? "You" : (agent?.name ?? ev.actorId);
  const d = ev.data as Record<string, string>;
  const text =
    ev.kind === "created"
      ? "created the task"
      : ev.kind === "status"
        ? `moved it ${d.from?.replace("_", " ")} → ${d.to?.replace("_", " ")}`
        : ev.kind === "assignee"
          ? `assigned it to ${agentName(d.to) || "nobody"}`
          : ev.kind === "edit"
            ? `edited the ${d.field}`
            : "commented";
  return (
    <div className="flex gap-2.5">
      {ev.actorId === "user" ? <UserAvatar size={22} /> : <AgentAvatar agent={agent} size={22} />}
      <div className="min-w-0 flex-1">
        <div className="text-sm">
          <span className="font-medium">{who}</span> <span className="text-ink-2">{text}</span>
          <span className="ml-2 text-[11px] text-ink-3">{dateTime(ev.createdAt)}</span>
        </div>
        {ev.kind === "comment" && (
          <div className="mt-1 rounded-md bg-panel-2 px-3 py-2">
            <Markdown>{d.text ?? ""}</Markdown>
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-ink-3">{title}</div>
      {children}
    </section>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <div className="text-sm text-ink-3">{children}</div>;
}
