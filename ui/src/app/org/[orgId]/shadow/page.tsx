"use client";

import { useShallow } from "zustand/react/shallow";
import { useCallback, useEffect, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { Badge, Button, cx, Empty, ErrorNote, Field, Input, Select, Spinner, Textarea, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Playbook, ShadowEvent, ShadowSession } from "@/lib/types";
import { useOrg } from "@/store/org";

// Where a recording was made: this computer (older recordings may say otherwise).
const WHERE: Record<string, string> = { computer: "this computer", desktop: "my computer", sandbox: "sandbox desktop", browser: "my browser" };
const STATUS: Record<ShadowSession["status"], [string, Tone]> = {
  recording: ["recording", "bad"],
  stopped: ["recorded", "neutral"],
  learning: ["learning", "accent"],
  learned: ["learned", "ok"],
};

export default function ShadowPage() {
  const orgId = useOrg((s) => s.orgId)!;
  const feedLen = useOrg((s) => s.feed.length);
  const [sessions, setSessions] = useState<ShadowSession[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"recordings" | "playbooks">("recordings");
  const load = useCallback(() => api.shadowSessions(orgId).then(setSessions).catch(() => setSessions([])), [orgId]);
  useEffect(() => {
    void load();
  }, [load, feedLen]);
  const active = sessions?.some((s) => s.status === "recording" || s.status === "learning");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [active, load]);

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex w-full shrink-0 flex-col border-b border-line bg-panel md:w-80 md:border-b-0 md:border-r">
        <div className="flex gap-1 border-b border-line p-2">
          {(["recordings", "playbooks"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cx("flex-1 rounded-md px-2 py-1.5 text-sm capitalize cursor-pointer", view === v ? "bg-panel-2 font-medium text-ink" : "text-ink-2 hover:text-ink")}
            >
              {v}
            </button>
          ))}
        </div>
        {view === "recordings" ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <NewRecording
              orgId={orgId}
              onStarted={(s) => {
                setSelected(s.id);
                void load();
              }}
            />
            <div className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-ink-3">Recordings</div>
            {!sessions ? (
              <Spinner />
            ) : sessions.length === 0 ? (
              <div className="px-3 pb-3 text-xs text-ink-3">None yet.</div>
            ) : (
              <ul className="space-y-0.5 px-2 pb-3">
                {sessions.map((s) => (
                  <li key={s.id}>
                    <button
                      onClick={() => setSelected(s.id)}
                      className={cx("w-full rounded-md px-2 py-2 text-left cursor-pointer", selected === s.id ? "bg-panel-2" : "hover:bg-panel-2/60")}
                    >
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">{s.title}</span>
                        <Badge tone={STATUS[s.status][1]}>{STATUS[s.status][0]}</Badge>
                      </div>
                      <div className="mt-0.5 text-[11px] text-ink-3">
                        for {s.agentName} · {WHERE[s.source] ?? s.source} · {s.eventCount} events · {timeAgo(s.startedAt)}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="p-3 text-xs leading-relaxed text-ink-3">
            Playbooks are how agents remember work they learned: from a recording, or because you asked them to write one. Agents read them with read_playbook and can follow
            them whenever you ask, or on a schedule.
          </div>
        )}
      </aside>
      <section className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {view === "playbooks" ? (
          <Playbooks orgId={orgId} />
        ) : selected ? (
          <Recording key={selected} id={selected} onChanged={load} onDeleted={() => (setSelected(null), void load())} />
        ) : (
          <Empty title="Teach an agent by showing it" icon="👁">
            Record yourself doing a piece of work on this computer, in any app. Then the agent reviews the recording, writes a playbook and can do the work for you.
          </Empty>
        )}
      </section>
    </div>
  );
}

function NewRecording({ orgId, onStarted }: { orgId: string; onStarted: (s: ShadowSession) => void }) {
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const [agentId, setAgentId] = useState("");
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await api.startShadow(orgId, { agentId: agentId || agents[0]?.id || "", title, note });
      setTitle("");
      setNote("");
      onStarted(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-3 border-b border-line p-3">
      <div className="text-sm font-semibold">Teach an agent</div>
      <Field label="Who learns">
        <Select value={agentId || agents[0]?.id || ""} onChange={(e) => setAgentId(e.target.value)}>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} ({a.role})
            </option>
          ))}
        </Select>
      </Field>
      <Field label="What you'll do">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Weekly invoice run" />
      </Field>
      <Field label="Anything they should know" hint="Optional: the goal, when it happens, what good looks like">
        <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Every Monday; skip customers marked on hold" />
      </Field>
      <div className="text-[11px] leading-relaxed text-ink-3">
        Records this computer: the windows you use, where you click and screenshots. What you type is counted, never recorded, and password managers, sign-in and private
        windows aren&apos;t captured.
      </div>
      <ErrorNote error={error} />
      <Button variant="primary" disabled={busy || !title.trim() || agents.length === 0} onClick={start}>
        ● Start recording
      </Button>
    </div>
  );
}

function Recording({ id, onChanged, onDeleted }: { id: string; onChanged: () => void; onDeleted: () => void }) {
  const [rec, setRec] = useState<(ShadowSession & { events: ShadowEvent[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const load = useCallback(() => api.shadowSession(id).then(setRec).catch((e) => setError((e as Error).message)), [id]);
  useEffect(() => {
    void load();
  }, [load]);
  const live = rec?.status === "recording" || rec?.status === "learning";
  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [live, load]);
  if (!rec) return error ? <ErrorNote error={error} /> : <Spinner />;
  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{rec.title}</h2>
        <Badge tone={STATUS[rec.status][1]}>{STATUS[rec.status][0]}</Badge>
        <span className="text-xs text-ink-3">
          for {rec.agentName} · {WHERE[rec.source] ?? rec.source} · {rec.eventCount} events
        </span>
        <div className="ml-auto flex gap-2">
          {rec.status === "recording" && (
            <Button size="sm" variant="danger" onClick={() => act(() => api.stopShadow(rec.id))}>
              ■ Stop
            </Button>
          )}
          {(rec.status === "recording" || rec.status === "stopped") && rec.eventCount > 0 && (
            <Button size="sm" variant="primary" onClick={() => act(() => api.learnShadow(rec.id))}>
              Teach {rec.agentName}
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => act(() => api.deleteShadow(rec.id)).then(onDeleted)}>
            Delete
          </Button>
        </div>
      </div>
      {rec.note && <div className="text-sm text-ink-2">{rec.note}</div>}
      <ErrorNote error={error} />
      {rec.status === "recording" && <HowToRecord />}
      {rec.status === "learning" && (
        <div className="rounded-lg border border-accent/40 bg-accent/10 p-3 text-sm">
          {rec.agentName} is going through the recording and writing a playbook. It may ask you questions in its chat.
        </div>
      )}
      {(rec.status === "learned" || rec.status === "learning") && <SessionPlaybook orgId={rec.orgId} agentId={rec.agentId} sessionId={rec.id} />}
      <div>
        <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">Timeline</div>
        {rec.events.length === 0 ? (
          <div className="text-sm text-ink-3">Nothing recorded yet.</div>
        ) : (
          <ol className="space-y-1">
            {rec.events.map((e) => (
              <li key={e.id} className="flex items-start gap-3 rounded-md px-2 py-1 hover:bg-panel-2/50">
                <span className="w-12 shrink-0 font-mono text-[11px] leading-5 text-ink-3">{clock(e.t)}</span>
                <span className="min-w-0 flex-1 text-sm leading-5">{describe(e)}</span>
                {e.image && (
                  <button onClick={() => setZoom(e.image)} className="shrink-0 cursor-zoom-in">
                    <img src={e.image} alt="" className="h-14 w-24 rounded border border-line object-cover object-top" loading="lazy" />
                  </button>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
      {zoom && (
        <button className="fixed inset-0 z-50 flex cursor-zoom-out items-center justify-center bg-black/80 p-6" onClick={() => setZoom(null)}>
          <img src={zoom} alt="" className="max-h-full max-w-full rounded-lg shadow-2xl" />
        </button>
      )}
    </div>
  );
}

function HowToRecord() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-bad/40 bg-bad/5 p-4 text-sm">
      <span className="mt-1.5 size-2.5 shrink-0 animate-pulse rounded-full bg-bad" />
      <div className="space-y-1 text-ink-2">
        <div className="font-medium text-ink">Recording this computer</div>
        <div>Do the work as you normally would, in any app, then come back and press Stop (or Teach). Pantheon can stay in the background meanwhile.</div>
      </div>
    </div>
  );
}

function SessionPlaybook({ orgId, agentId, sessionId }: { orgId: string; agentId: string; sessionId: string }) {
  const [books, setBooks] = useState<Playbook[] | null>(null);
  const feedLen = useOrg((s) => s.feed.length);
  useEffect(() => {
    api.playbooks(orgId, agentId).then(setBooks).catch(() => setBooks([]));
  }, [orgId, agentId, feedLen]);
  const mine = books?.filter((b) => b.sessionId === sessionId) ?? [];
  if (!mine.length) return null;
  return (
    <div className="space-y-2">
      {mine.map((b) => (
        <PlaybookCard key={b.id} book={b} onChanged={() => api.playbooks(orgId, agentId).then(setBooks)} />
      ))}
    </div>
  );
}

function Playbooks({ orgId }: { orgId: string }) {
  const byId = useOrg((s) => s.agents);
  const feedLen = useOrg((s) => s.feed.length);
  const [books, setBooks] = useState<Playbook[] | null>(null);
  const load = useCallback(() => api.playbooks(orgId).then(setBooks).catch(() => setBooks([])), [orgId]);
  useEffect(() => {
    void load();
  }, [load, feedLen]);
  if (!books) return <Spinner />;
  if (!books.length)
    return (
      <Empty title="No playbooks yet" icon="📘">
        Record yourself doing some work and teach an agent with it, or ask an agent to write down how it does something with save_playbook.
      </Empty>
    );
  return (
    <div className="space-y-3 p-4">
      {books.map((b) => (
        <div key={b.id}>
          <div className="mb-1 flex items-center gap-2 text-xs text-ink-3">
            {byId[b.agentId] && <AgentAvatar agent={byId[b.agentId]} size={16} />}
            {byId[b.agentId]?.name ?? "?"}
          </div>
          <PlaybookCard book={b} onChanged={load} />
        </div>
      ))}
    </div>
  );
}

function PlaybookCard({ book, onChanged }: { book: Playbook; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(book.content);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      await api.updatePlaybook(book.id, { content: text });
      setEditing(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="rounded-lg border border-line bg-panel p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-semibold">📘 {book.title}</span>
        <span className="text-[11px] text-ink-3">{timeAgo(book.updatedAt ?? book.createdAt)}</span>
        <div className="ml-auto flex gap-1">
          {editing ? (
            <>
              <Button size="sm" variant="primary" onClick={save}>
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setText(book.content))}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
                Edit
              </Button>
              <Button size="sm" variant="ghost" onClick={() => api.deletePlaybook(book.id).then(onChanged)}>
                Delete
              </Button>
            </>
          )}
        </div>
      </div>
      {editing ? <Textarea rows={12} value={text} onChange={(e) => setText(e.target.value)} /> : <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-2">{book.content}</div>}
      <ErrorNote error={error} />
    </div>
  );
}

const clock = (t: number) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function describe(e: ShadowEvent): React.ReactNode {
  const d = e.data as Record<string, string | number | string[] | undefined>;
  const where = d.window ? <span className="text-ink-3"> in {String(d.window)}</span> : null;
  switch (e.kind) {
    case "click":
      return (
        <>
          🖱 Clicked {d.label ? <b>{String(d.label)}</b> : d.x !== undefined ? `at (${d.x}, ${d.y})` : "something"}
          {where}
        </>
      );
    case "input":
      return (
        <>
          ⌨ Filled <b>{String(d.label || d.name || "a field")}</b> with <span className="font-mono text-[12px]">{String(d.value ?? "")}</span>
        </>
      );
    case "submit":
      return <>↵ Submitted {d.label ? <b>{String(d.label)}</b> : "a form"}</>;
    case "key":
      return (
        <>
          ↵ Pressed {String(d.key)}
          {d.label ? <span className="text-ink-3"> in {String(d.label)}</span> : null}
        </>
      );
    case "navigate":
      return (
        <>
          🌐 {d.switched ? "Switched to" : "Opened"} <b>{String(d.title || d.url)}</b> <span className="break-all text-[11px] text-ink-3">{String(d.url ?? "")}</span>
        </>
      );
    case "window":
      return (
        <>
          🗔 Switched to <b>{String(d.title || "(no title)")}</b>
        </>
      );
    case "typing":
      return (
        <>
          ⌨ Typed {String(d.keys)} characters{Array.isArray(d.special) && d.special.length ? ` (then ${d.special.join(", ")})` : ""}
          {where}
        </>
      );
    case "note":
      return <span className="text-ink-2">ℹ {String(d.text)}</span>;
    default:
      return <span className="text-ink-3">📷 Screen{where}</span>;
  }
}
