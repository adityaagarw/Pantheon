"use client";

import { useShallow } from "zustand/react/shallow";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { dmKey, useDmMessages } from "@/components/AgentInspector";
import { Composer, MessageList, useAutoSpeak } from "@/components/Chat";
import { Badge, Button, cx, Empty, Toggle } from "@/components/ui";
import { api, type AttentionView } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { WhiteboardEditor } from "@/components/WhiteboardOverlay";
import type { Agent, WhiteboardInfo } from "@/lib/types";
import type { LiveState } from "@/lib/liveTalk";
import { isSpeaking, onSpeakingChange, speakAndWait, stopSpeaking } from "@/lib/voice";
import { backendHttpBase, servedDirect } from "@/lib/ws";
import { useOrg } from "@/store/org";

type View = { kind: "page"; id: string } | { kind: "browser"; agentId: string } | { kind: "computer" } | { kind: "whiteboard"; id: string };

/**
 * Teach-along: tell the agent that owns the current view what the user is
 * looking at (debounced). Clears it when the view goes away, so an agent the
 * user switched away from isn't told they're still with it.
 */
function useAttention(agentId: string | null | undefined, view: AttentionView | null, deps: unknown[], delayMs = 400): void {
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

export default function StagePage() {
  const orgId = useOrg((s) => s.orgId)!;
  const pages = useOrg(useShallow((s) => Object.values(s.stagePages).sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""))));
  const browsers = useOrg((s) => s.browsers);
  const agents = useOrg((s) => s.agents);
  const boards = useOrg(useShallow((s) => Object.values(s.whiteboards)));
  const [view, setView] = useState<View | null>(null);
  const [known, setKnown] = useState<Record<string, { url: string; title: string }>>({});

  // Agents that already had a browser open before this page loaded.
  useEffect(() => {
    let alive = true;
    for (const a of Object.values(agents)) {
      if (a.isSupervisor) continue;
      api
        .agentBrowser(a.id)
        .then((b) => alive && b.open && setKnown((k) => ({ ...k, [a.id]: { url: b.url, title: b.title } })))
        .catch(() => {});
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);
  const openBrowsers = useMemo(() => ({ ...known, ...browsers }), [known, browsers]);

  // Follow the newest page as agents publish, unless the user picked something else.
  const newest = pages[0];
  const followed = useRef<string | null>(null);
  useEffect(() => {
    if (!newest) return;
    if (!view || (view.kind === "page" && view.id === followed.current)) {
      setView({ kind: "page", id: newest.id });
      followed.current = newest.id;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newest?.id, newest?.version]);

  const current = view?.kind === "page" ? pages.find((p) => p.id === view.id) : undefined;
  const [listOpen, setListOpen] = useState(true);
  const [chatOpen, setChatOpen] = useState(false);
  const [liveState, setLiveState] = useState<LiveState | null>(null);
  // The ref mirrors liveState synchronously so the frame's narration handler can
  // tell a barge-in (learner talking) from a TTS failure without waiting for a render.
  const liveStateRef = useRef<LiveState | null>(null);
  const handleLiveState = (s: LiveState | null) => {
    liveStateRef.current = s;
    setLiveState(s);
  };

  // The lesson stays paused while the learner talks AND until the teacher has
  // answered (and finished speaking), not just until the question is sent.
  const [awaiting, setAwaiting] = useState(false);
  const awaitingRef = useRef(false);
  const setAwait = (v: boolean) => {
    awaitingRef.current = v;
    setAwaiting(v);
  };
  const whereRef = useRef("");
  // Pause now: silence the narration, hold the lesson, and tell the teacher — before
  // the question itself is sent, so their turn starts already knowing it's paused.
  const pauseLesson = async () => {
    setAwait(true);
    stopSpeaking();
    if (current?.agentId) {
      await api
        .agentAttention(current.agentId, { kind: "page", pageId: current.id, title: current.title, text: whereRef.current, held: true })
        .catch(() => {});
    }
  };
  // Talking over the lesson only works without echo: on speakers the narration leaks
  // into the mic and the lesson would interrupt itself, so it's opt-in (headphones).
  const [headphones, setHeadphones] = useState(false);
  useEffect(() => {
    try {
      setHeadphones(localStorage.getItem("pantheon.stage.headphones") === "1");
    } catch {
      /* per-device preference only */
    }
  }, []);
  const changeHeadphones = (on: boolean) => {
    setHeadphones(on);
    try {
      localStorage.setItem("pantheon.stage.headphones", on ? "1" : "0");
    } catch {
      /* per-device preference only */
    }
  };

  return (
    <div className="relative flex h-full">
      <aside className={cx("w-full shrink-0 overflow-y-auto border-r border-line bg-panel p-3 md:block md:w-72", !listOpen && view && "hidden")}>
        <Section title="Lessons & scenes">
          {pages.length === 0 && (
            <div className="px-2 text-xs leading-relaxed text-ink-3">
              Nothing on the Stage yet. Ask an agent to teach you something visually, e.g. &ldquo;show me how a Fourier series builds a square wave&rdquo;.
            </div>
          )}
          {pages.map((p) => (
            <Item
              key={p.id}
              active={view?.kind === "page" && view.id === p.id}
              onClick={() => (setView({ kind: "page", id: p.id }), setListOpen(false))}
              icon={<AgentAvatar agent={p.agentId ? agents[p.agentId] : undefined} size={18} />}
              label={p.title}
              meta={`v${p.version} · ${timeAgo(p.updatedAt)}`}
            />
          ))}
        </Section>
        <Section
          title="Whiteboards"
          action={
            <button
              className="text-ink-3 hover:text-ink cursor-pointer"
              title="New whiteboard"
              onClick={() => api.createWhiteboard(orgId, `Board ${boards.length + 1}`).then((b) => (setView({ kind: "whiteboard", id: b.id }), setListOpen(false)))}
            >
              +
            </button>
          }
        >
          {boards.map((b) => (
            <Item
              key={b.id}
              active={view?.kind === "whiteboard" && view.id === b.id}
              onClick={() => (setView({ kind: "whiteboard", id: b.id }), setListOpen(false))}
              icon={<span className="w-[18px] text-center">✏️</span>}
              label={b.title}
              meta={b.updatedBy && b.updatedBy !== "user" ? b.updatedBy : `${b.elementCount}`}
            />
          ))}
        </Section>
        <Section title="Agents' browsers">
          {Object.keys(openBrowsers).length === 0 && <div className="px-2 text-xs text-ink-3">No agent has a browser open.</div>}
          {Object.entries(openBrowsers).map(([aid, b]) => (
            <Item
              key={aid}
              active={view?.kind === "browser" && view.agentId === aid}
              onClick={() => (setView({ kind: "browser", agentId: aid }), setListOpen(false))}
              icon={<AgentAvatar agent={agents[aid]} size={18} />}
              label={b.title || b.url || "Browser"}
              meta={agents[aid]?.name ?? ""}
            />
          ))}
        </Section>
        <Section title="Computer">
          <Item active={view?.kind === "computer"} onClick={() => (setView({ kind: "computer" }), setListOpen(false))} icon={<span className="w-[18px] text-center">🖥</span>} label="Sandbox desktop" meta="live" />
        </Section>
      </aside>
      <section className={cx("min-w-0 flex-1 flex-col bg-[#0f1116] md:flex", listOpen && view ? "hidden" : "flex")}>
        <button onClick={() => setListOpen(true)} className="border-b border-line px-3 py-2 text-left text-sm text-ink-2 md:hidden cursor-pointer">
          ‹ Stage
        </button>
        {!view ? (
          <Empty title="The Stage" icon="🎬">
            Agents put interactive lessons, animations and 3D scenes here — and you can watch their browsers and the computer they use.
          </Empty>
        ) : view.kind === "page" && current ? (
          <StageFrame
            key={current.id}
            orgId={orgId}
            page={current}
            agentName={current.agentId ? agents[current.agentId]?.name : undefined}
            liveState={liveState}
            liveStateRef={liveStateRef}
            awaiting={awaiting}
            awaitingRef={awaitingRef}
            onWhere={(t) => (whereRef.current = t)}
            onPause={() => void pauseLesson()}
            onResume={() => setAwait(false)}
            chatOpen={chatOpen}
            onToggleChat={() => setChatOpen((o) => !o)}
          />
        ) : view.kind === "browser" ? (
          <BrowserView agentId={view.agentId} info={openBrowsers[view.agentId]} name={agents[view.agentId]?.name ?? "Agent"} />
        ) : view.kind === "computer" ? (
          <ComputerView />
        ) : view.kind === "whiteboard" ? (
          <WhiteboardView boardId={view.id} board={boards.find((b) => b.id === view.id)} agents={agents} />
        ) : (
          <Empty title="That page was removed" />
        )}
      </section>
      {view?.kind === "page" && current?.agentId && chatOpen && (
        <aside className="absolute inset-y-0 right-0 z-20 flex w-full flex-col border-l border-line bg-panel md:static md:w-[360px] md:shrink-0">
          <LessonChat
            key={current.agentId}
            orgId={orgId}
            agentId={current.agentId}
            agentName={agents[current.agentId]?.name}
            onLiveState={handleLiveState}
            onAsk={pauseLesson}
            onAnswered={() => setAwait(false)}
            headphones={headphones}
            onHeadphones={changeHeadphones}
            onClose={() => setChatOpen(false)}
          />
        </aside>
      )}
    </div>
  );
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-1 flex items-center justify-between px-2 text-[11px] font-medium uppercase tracking-wider text-ink-3">
        {title}
        {action}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function Item({ active, onClick, icon, label, meta }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string; meta: string }) {
  return (
    <button
      onClick={onClick}
      className={cx("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors cursor-pointer", active ? "bg-panel-2 text-ink" : "text-ink-2 hover:bg-panel-2/60")}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="shrink-0 text-[10px] text-ink-3">{meta}</span>
    </button>
  );
}

function StageFrame({
  orgId,
  page,
  agentName,
  liveState,
  liveStateRef,
  awaiting,
  awaitingRef,
  onWhere,
  onPause,
  onResume,
  chatOpen,
  onToggleChat,
}: {
  orgId: string;
  page: { id: string; title: string; version: number; agentId: string | null };
  agentName?: string;
  liveState: LiveState | null;
  liveStateRef: React.RefObject<LiveState | null>;
  /** The learner asked something and the teacher hasn't finished answering. */
  awaiting: boolean;
  awaitingRef: React.RefObject<boolean>;
  onWhere: (text: string) => void;
  onPause: () => void;
  onResume: () => void;
  chatOpen: boolean;
  onToggleChat: () => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [stepLabel, setStepLabel] = useState("");
  const [captionText, setCaptionText] = useState("");
  const [whereText, setWhereText] = useState("");
  const src = `${backendHttpBase()}/api/v1/stage/${page.id}/view?v=${page.version}&r=${reload}`;

  // Teach-along: the learner's voice pauses the lesson (the agent's own narration
  // does not — that is what we are narrating), and it stays paused until the
  // teacher has answered.
  const isTalking = (s: LiveState | null) => s === "hearing" || s === "transcribing";
  const learnerTalking = isTalking(liveState);
  const held = learnerTalking || awaiting;
  // Read from refs so the narration handler sees the current state without a render.
  const isHeld = () => isTalking(liveStateRef.current) || !!awaitingRef.current;

  // New lesson: forget where the previous one was.
  useEffect(() => {
    onWhere("");
  }, [page.id, onWhere]);
  // Narration must never outlive the frame that asked for it (reload, new version, leaving).
  useEffect(() => () => stopSpeaking(), [src]);

  const postToFrame = (msg: Record<string, unknown>) => {
    frame.current?.contentWindow?.postMessage(msg, "*");
  };

  // The only channel out of a (sandboxed) page: pantheon messages.
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return;
      const d = e.data as { pantheon?: boolean; type?: string; text?: string; step?: boolean; id?: number };
      if (!d?.pantheon) return;
      if (d.type === "error") setError(String(d.text ?? "error"));
      else if (d.type === "send" && d.text && page.agentId) {
        const text = String(d.text).slice(0, 4000);
        void api.send(orgId, { to: page.agentId, content: `[On the Stage · ${page.title}] ${text}` }).then(() => {
          setSent(text);
          setTimeout(() => setSent(null), 4000);
        });
      } else if (d.type === "progress" && d.text) {
        setWhereText(String(d.text));
        onWhere(String(d.text));
        if (d.step) setStepLabel(String(d.text));
        else setCaptionText(String(d.text));
      } else if (d.type === "say" && page.agentId) {
        const id = d.id as number;
        const text = String(d.text ?? "");
        // We will narrate it, so the lesson waits for our "said" instead of
        // falling back to timed captions.
        postToFrame({ pantheon: true, type: "say-ack", id });
        void speakAndWait(text, page.agentId).then((spoken) => {
          postToFrame(
            spoken
              ? { pantheon: true, type: "said", id, spoken: true }
              : { pantheon: true, type: "said", id, interrupted: isHeld(), spoken: false },
          );
        });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, page.agentId, page.title]);

  // Pause at once; resume after a beat, so the moment between "you finished
  // talking" and "your message arrived" doesn't restart the lesson.
  useEffect(() => {
    if (held) {
      postToFrame({ pantheon: true, type: "hold", on: true });
      return;
    }
    const t = setTimeout(() => postToFrame({ pantheon: true, type: "hold", on: false }), 900);
    return () => clearTimeout(t);
  }, [held, reload]);

  // Tell the teacher where the lesson is and whether it is paused (and clear it
  // when the user leaves this page). Pausing is reported immediately.
  useAttention(
    page.agentId,
    { kind: "page", pageId: page.id, title: page.title, text: whereText, held },
    [page.agentId, page.id, page.title, whereText, held],
    held ? 0 : 200,
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2">
        <span className="font-semibold">{page.title}</span>
        <Badge>v{page.version}</Badge>
        {agentName && <span className="text-xs text-ink-3">by {agentName}</span>}
        {stepLabel && <Badge tone="accent">{stepLabel}</Badge>}
        {captionText && <span className="min-w-0 max-w-[38%] truncate text-xs text-ink-3" title={captionText}>{captionText}</span>}
        {held && <Badge tone="warn">{learnerTalking ? "paused · you're talking" : `paused · ${agentName ?? "the teacher"} is answering`}</Badge>}
        {error && <Badge tone="bad">page error: {error.slice(0, 80)}</Badge>}
        {sent && <Badge tone="ok">sent to {agentName ?? "the agent"}</Badge>}
        <span className="ml-auto flex gap-1.5">
          {page.agentId && (
            <Button size="sm" variant={awaiting ? "primary" : "ghost"} onClick={awaiting ? onResume : onPause} title={awaiting ? "Continue the lesson" : "Pause the lesson to ask something"}>
              {awaiting ? "▶ Resume" : "✋ Ask"}
            </Button>
          )}
          {page.agentId && (
            <Button size="sm" variant={chatOpen ? "primary" : "ghost"} onClick={onToggleChat}>
              💬 Talk
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => (setError(null), setReload((r) => r + 1))}>
            ↻ Restart
          </Button>
          <a href={src} target="_blank" rel="noreferrer">
            <Button size="sm" variant="ghost">
              ⤢ Open
            </Button>
          </a>
          {confirmDelete ? (
            <Button size="sm" variant="danger" onClick={() => api.deleteStagePage(page.id)}>
              Delete page
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}>
              ✕
            </Button>
          )}
        </span>
      </div>
      <iframe
        ref={frame}
        key={src}
        src={src}
        title={page.title}
        className="min-h-0 w-full flex-1 border-0 bg-[#0f1116]"
        sandbox="allow-scripts allow-popups allow-forms"
        allow="fullscreen"
        onLoad={() => postToFrame({ pantheon: true, type: "hold", on: isHeld() })}
      />
    </div>
  );
}

/** Chat with the lesson's teacher: type a question, or go hands-free with Live talk. */
function LessonChat({
  orgId,
  agentId,
  agentName,
  onLiveState,
  onAsk,
  onAnswered,
  headphones,
  onHeadphones,
  onClose,
}: {
  orgId: string;
  agentId: string;
  agentName?: string;
  onLiveState: (s: LiveState | null) => void;
  /** The learner is asking something: pause the lesson (and tell the teacher). */
  onAsk: () => Promise<void>;
  /** The teacher has answered (and finished speaking): resume the lesson. */
  onAnswered: () => void;
  headphones: boolean;
  onHeadphones: (on: boolean) => void;
  onClose: () => void;
}) {
  const agents = useOrg((s) => s.agents);
  const messages = useDmMessages(agentId);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const speakOn = speakReplies || liveOn;
  useAutoSpeak(messages, speakOn);
  useEffect(() => {
    api.voiceConfig().then((c) => setSpeakReplies(c.autoSpeak)).catch(() => {});
  }, []);

  // Watch the conversation: your question pauses the lesson (typed or spoken alike);
  // the lesson resumes once the teacher's answer has been read out.
  const speakRef = useRef(speakOn);
  const onAskRef = useRef(onAsk);
  const onAnsweredRef = useRef(onAnswered);
  useEffect(() => {
    speakRef.current = speakOn;
    onAskRef.current = onAsk;
    onAnsweredRef.current = onAnswered;
  });
  const seen = useRef<Set<string> | null>(null);
  const asked = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopWatching = useRef<(() => void) | null>(null);
  // History loads after mount; only messages after that count as new.
  const loaded = useOrg((s) => {
    const ch = Object.values(s.channels).find((c) => c.key === dmKey(agentId, "user"));
    return ch ? !!s.messagesByChannel[ch.id] : true;
  });

  const finish = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    stopWatching.current?.();
    stopWatching.current = null;
    if (asked.current) {
      asked.current = false;
      onAnsweredRef.current();
    }
  }, []);
  const beginAsk = useCallback(async () => {
    if (asked.current) return;
    asked.current = true;
    // Never leave the lesson paused for good if the answer doesn't come.
    timer.current = setTimeout(finish, 90_000);
    await onAskRef.current();
  }, [finish]);
  const answerArrived = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!speakRef.current) return finish();
    // Wait for the answer to start playing (speech synthesis takes a moment), then
    // for it to finish. If it never starts, don't hold the lesson for it.
    let started = isSpeaking();
    const off = onSpeakingChange((s) => {
      if (s) started = true;
      else if (started) finish();
    });
    const t = setTimeout(() => !started && finish(), 6000);
    const guard = setTimeout(finish, 60_000);
    stopWatching.current = () => {
      off();
      clearTimeout(t);
      clearTimeout(guard);
    };
  }, [finish]);
  useEffect(() => {
    if (!loaded) return;
    if (seen.current === null) {
      seen.current = new Set(messages.map((m) => m.id));
      return;
    }
    for (const m of messages) {
      if (seen.current.has(m.id)) continue;
      seen.current.add(m.id);
      if (m.senderType === "user") void beginAsk(); // e.g. spoken through Live talk
      else if (m.senderType === "agent" && m.senderId === agentId && asked.current) answerArrived();
    }
  }, [messages, loaded, agentId, beginAsk, answerArrived]);
  // Closing the chat mid-question must not leave the lesson paused.
  useEffect(() => () => finish(), [finish]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2">
        <AgentAvatar agent={agents[agentId]} size={20} />
        <span className="truncate text-sm font-semibold">{agentName ?? "Teacher"}</span>
        <span className="ml-auto text-[11px] text-ink-3">Your teacher</span>
        <button onClick={onClose} className="text-ink-3 hover:text-ink cursor-pointer" aria-label="Close chat">
          ✕
        </button>
      </div>
      <div className="flex items-center justify-between px-4 pt-3 text-xs font-medium uppercase tracking-wider text-ink-3">
        <span className="normal-case tracking-normal">Conversation with you</span>
        <Toggle checked={speakOn} onChange={setSpeakReplies} label={<span className="normal-case tracking-normal">Speak replies</span>} />
      </div>
      <div className="flex items-center justify-between gap-2 px-4 pt-1.5 text-xs text-ink-3">
        <Toggle checked={headphones} onChange={onHeadphones} label={<span title="Lets you talk over the lesson. On speakers the narration can leak into the microphone.">🎧 Headphones</span>} />
      </div>
      <MessageList
        messages={messages}
        agents={agents}
        compact
        empty={
          <div className="py-8 text-center text-sm text-ink-3">
            Ask your teacher a question — type it, or tap <b>Live</b> and ask out loud. The lesson pauses while you ask and picks up again once they&apos;ve answered.
            {headphones ? " With headphones you can just start talking over the lesson." : " On speakers, tap ✋ Ask first, then speak."}
          </div>
        }
      />
      <Composer
        placeholder={`Ask ${agentName ?? "your teacher"}…`}
        attach={{ orgId, agentId }}
        onSend={async (t, files) => {
          await beginAsk(); // pause and notify the teacher before the question is sent
          try {
            await api.send(orgId, { to: agentId, content: t, attachments: files });
          } catch (e) {
            finish();
            throw e;
          }
        }}
        live={{ orgId, agentId, agentName }}
        onLiveChange={setLiveOn}
        onLiveState={onLiveState}
        preferInterruptible={headphones}
      />
    </div>
  );
}

function BrowserView({ agentId, info, name }: { agentId: string; info?: { url: string; title: string; at?: number }; name: string }) {
  const [t, setT] = useState(() => Date.now());
  useEffect(() => setT(Date.now()), [info?.at]);
  // Also refresh every few seconds while watching.
  useEffect(() => {
    const id = setInterval(() => setT(Date.now()), 4000);
    return () => clearInterval(id);
  }, []);
  // Teach-along: the agent whose browser this is knows the user is watching it.
  useAttention(agentId, { kind: "browser", title: info?.title ?? "", url: info?.url ?? "" }, [agentId, info?.title, info?.url]);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line bg-panel px-4 py-2 text-sm">
        <span className="font-semibold">{name}&apos;s browser</span>
        <span className="min-w-0 truncate rounded bg-bg px-2 py-0.5 font-mono text-xs text-ink-2">{info?.url || "…"}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`${backendHttpBase()}/api/v1/agents/${agentId}/browser.jpg?t=${t}`} alt={info?.title ?? "browser"} className="mx-auto max-w-full rounded-lg border border-line shadow-lg" />
      </div>
    </div>
  );
}

/** A shared board on the Stage: the agent that last drew on it knows you're looking. */
function WhiteboardView({ boardId, board, agents }: { boardId: string; board: WhiteboardInfo | undefined; agents: Record<string, Agent> }) {
  const teacherId = useMemo(() => {
    if (!board || board.updatedBy === "user") return null;
    return Object.values(agents).find((a) => a.name === board.updatedBy)?.id ?? null;
  }, [board, agents]);
  useAttention(teacherId, teacherId && board ? { kind: "whiteboard", title: board.title } : null, [teacherId, board?.title]);
  return (
    <div className="min-h-0 flex-1 bg-white">
      <WhiteboardEditor key={boardId} boardId={boardId} />
    </div>
  );
}

function ComputerView() {
  const [status, setStatus] = useState<{ ok: boolean; error?: string; inUseBy: string | null } | null>(null);
  const [control, setControl] = useState(false);
  const at = useOrg((s) => s.computerAt);
  useEffect(() => {
    api.computer().then(setStatus).catch(() => setStatus({ ok: false, error: "unreachable", inUseBy: null }));
  }, [at]);
  const { protocol, hostname } = window.location;
  const direct = servedDirect();
  const base = direct ? `${protocol}//${hostname}:${process.env.NEXT_PUBLIC_PANTHEON_DESKTOP_PORT ?? "6901"}` : "/desktop";
  const url = `${base}/vnc.html?autoconnect=1&resize=scale&reconnect=true&view_only=${control ? 0 : 1}${direct ? "" : "&path=desktop/websockify"}`;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2 text-sm">
        <span className="font-semibold">Sandbox desktop</span>
        {status ? status.ok ? <Badge tone="ok">running</Badge> : <Badge tone="bad">not running</Badge> : <Badge>checking…</Badge>}
        {status?.inUseBy && <Badge tone="accent">in use by {status.inUseBy}</Badge>}
        <Button size="sm" className="ml-auto" variant={control ? "danger" : "secondary"} onClick={() => setControl(!control)}>
          {control ? "Stop controlling" : "Take control"}
        </Button>
      </div>
      {status && !status.ok ? (
        <Empty title="The computer isn't running" icon="🖥">
          Start it with <code className="rounded bg-panel-2 px-1">docker compose --profile computer up -d</code>, then grant agents the computer_* tools on the Team tab.
        </Empty>
      ) : (
        <iframe key={url} src={url} title="desktop" className="min-h-0 w-full flex-1 border-0 bg-black" />
      )}
    </div>
  );
}
