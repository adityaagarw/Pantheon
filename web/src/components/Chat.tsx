"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { clock } from "@/lib/format";
import type { Agent, AttachmentBrief, AttachmentInfo, Message } from "@/lib/types";
import { LiveTalk, type LiveState } from "@/lib/liveTalk";
import { canListen, listen, speak, stopSpeaking, voiceStatus, VoiceSetupError, type Listener, type VoiceStatus } from "@/lib/voice";
import { AgentAvatar, UserAvatar } from "./agent";
import { FilePill, MessageAttachments, uploadFiles } from "./Files";
import { Markdown } from "./Markdown";
import { Button, cx, Spinner, Textarea } from "./ui";

export function MessageList({
  messages,
  agents,
  empty,
  compact,
}: {
  messages: Message[];
  agents: Record<string, Agent>;
  empty?: ReactNode;
  compact?: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useLayoutEffect(() => {
    if (stick.current) end.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);
  return (
    <div
      ref={box}
      className="min-h-0 flex-1 overflow-y-auto px-4 py-3"
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
    >
      {messages.length === 0 && (empty ?? <div className="py-10 text-center text-sm text-ink-3">No messages yet.</div>)}
      <div className="space-y-3">
        {messages.map((m, i) => {
          const prev = messages[i - 1];
          const grouped = prev && prev.senderId === m.senderId && Date.parse(m.createdAt) - Date.parse(prev.createdAt) < 120000;
          return <MessageRow key={m.id} m={m} agents={agents} grouped={!!grouped} compact={compact} />;
        })}
      </div>
      <div ref={end} />
    </div>
  );
}

function MessageRow({ m, agents, grouped, compact }: { m: Message; agents: Record<string, Agent>; grouped: boolean; compact?: boolean }) {
  const agent = m.senderType === "agent" ? agents[m.senderId] : undefined;
  const name =
    m.senderType === "user" ? "You" : m.senderType === "system" ? "System" : (agent?.name ?? String(m.meta?.senderName ?? m.senderId));
  if (m.senderType === "system") {
    return (
      <div className="flex justify-center">
        <div className="max-w-[85%] rounded-md border border-line bg-panel-2/60 px-3 py-1.5 text-xs text-ink-2 whitespace-pre-wrap">{m.content}</div>
      </div>
    );
  }
  return (
    <div className={cx("flex gap-2.5", grouped && "-mt-1.5")}>
      <div className="w-7 shrink-0">
        {!grouped && (m.senderType === "user" ? <UserAvatar size={28} /> : <AgentAvatar agent={agent} size={28} />)}
      </div>
      <div className="min-w-0 flex-1">
        {!grouped && (
          <div className="mb-0.5 flex items-baseline gap-2">
            <span className="text-sm font-semibold">{name}</span>
            {agent && !compact && <span className="text-xs text-ink-3">{agent.role}</span>}
            <span className="text-[11px] text-ink-3">{clock(m.createdAt)}</span>
            {m.kind === "reply" && <span className="text-[10px] uppercase tracking-wide text-ink-3">reply</span>}
          </div>
        )}
        {m.senderType === "agent" && m.meta?.thinking === true && <ThinkingBlock messageId={m.id} />}
        <Markdown className="text-ink">{m.content}</Markdown>
        {Array.isArray(m.meta?.attachments) && m.meta.attachments.length > 0 && <MessageAttachments items={m.meta.attachments as AttachmentBrief[]} />}
      </div>
    </div>
  );
}

type ThinkingStep = { reasoning: string; text: string; tools: string[] };

/** The agent's reasoning behind a message: collapsed by default, loaded on first open. */
function ThinkingBlock({ messageId }: { messageId: string }) {
  const [steps, setSteps] = useState<ThinkingStep[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    if (steps || error) return;
    api
      .messageThinking(messageId)
      .then((r) => setSteps(r.steps.filter((s) => s.reasoning.trim())))
      .catch((e: Error) => setError(e.message));
  };
  return (
    <details className="group mb-1 text-xs" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && load()}>
      <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded px-1 py-0.5 text-ink-3 hover:bg-panel-2 hover:text-ink-2">
        <span className="transition-transform group-open:rotate-90">›</span> Thinking
      </summary>
      <div className="mt-1 space-y-2 border-l-2 border-line pl-3 text-ink-2">
        {error ? (
          <div className="text-bad">{error}</div>
        ) : !steps ? (
          <Spinner className="size-3" />
        ) : steps.length === 0 ? (
          <div className="text-ink-3">No reasoning was recorded for this message.</div>
        ) : (
          steps.map((s, i) => (
            <div key={i}>
              {steps.length > 1 && <div className="mb-0.5 text-[10px] uppercase tracking-wide text-ink-3">Step {i + 1}</div>}
              <div className="whitespace-pre-wrap leading-relaxed">{s.reasoning.trim()}</div>
              {s.tools.length > 0 && <div className="mt-0.5 text-[11px] text-ink-3">→ then used {s.tools.join(", ")}</div>}
            </div>
          ))
        )}
      </div>
    </details>
  );
}

/** What the agent is thinking right now (while a turn runs); collapsed by default. */
export function LiveThinking({ text }: { text: string }) {
  const t = text.trim();
  if (!t) return null;
  return (
    <details className="group text-xs">
      <summary className="inline-flex cursor-pointer select-none items-center gap-1 text-ink-3 hover:text-ink-2">
        <span className="transition-transform group-open:rotate-90">›</span> Thinking…
      </summary>
      <div className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap border-l-2 border-line pl-3 leading-relaxed text-ink-2">{t.slice(-3000)}</div>
    </details>
  );
}

const LIVE_LABEL: Record<LiveState, string> = {
  connecting: "Connecting…",
  listening: "Listening — just talk",
  hearing: "Hearing you…",
  transcribing: "Transcribing…",
  "agent-speaking": "Agent speaking — talk to interrupt",
  paused: "Mic muted",
  error: "Stopped",
  off: "Off",
};

function readHalfDuplex(): boolean {
  try {
    return localStorage.getItem("pantheon.live.interrupt") !== "1";
  } catch {
    return true;
  }
}

/** Hands-free conversation bar: VAD-driven turn taking with an agent. */
function LiveBar({
  orgId,
  agentId,
  agentName,
  onClose,
  onHearing,
  onLiveState,
  preferInterruptible,
}: {
  orgId: string;
  agentId: string;
  agentName?: string;
  onClose: (error?: string) => void;
  onHearing?: () => void;
  /** Every live-talk state change (null when the session ends). */
  onLiveState?: (s: LiveState | null) => void;
  preferInterruptible?: boolean;
}) {
  const [state, setState] = useState<LiveState>("connecting");
  const [level, setLevel] = useState(0);
  const [muted, setMuted] = useState(false);
  const [info, setInfo] = useState<{ vad: string; fallbackReason?: string | null } | null>(null);
  const [heard, setHeard] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // A caller that decides (the Stage's headphones switch) wins over the stored per-device choice.
  const [halfDuplex, setHalfDuplex] = useState(() => (preferInterruptible === undefined ? readHalfDuplex() : !preferInterruptible));
  useEffect(() => {
    if (preferInterruptible !== undefined) setHalfDuplex(!preferInterruptible);
  }, [preferInterruptible]);
  const hearingRef = useRef(onHearing);
  useLayoutEffect(() => {
    hearingRef.current = onHearing;
  });
  const session = useRef<LiveTalk | null>(null);
  const closeRef = useRef(onClose);
  useLayoutEffect(() => {
    closeRef.current = onClose;
  });
  const liveStateRef = useRef(onLiveState);
  useLayoutEffect(() => {
    liveStateRef.current = onLiveState;
  });

  useEffect(() => {
    let lastError: string | undefined;
    const s = new LiveTalk(
      orgId,
      agentId,
      {
        onState: (st) => {
          setState(st);
          liveStateRef.current?.(st);
          if (st === "hearing") hearingRef.current?.();
          if (st === "error") closeRef.current(lastError);
        },
        onTranscript: setHeard,
        onLevel: setLevel,
        onInfo: setInfo,
        onError: (m) => {
          lastError = m;
          setNote(m);
        },
      },
      { halfDuplex },
    );
    session.current = s;
    void s.start();
    return () => {
      s.stop();
      liveStateRef.current?.(null);
    };
    // Restart the session only when the target or duplex mode changes.
  }, [orgId, agentId, halfDuplex]);

  const toggleDuplex = () => {
    const next = !halfDuplex;
    try {
      localStorage.setItem("pantheon.live.interrupt", next ? "0" : "1");
    } catch {
      /* per-device preference only */
    }
    setHalfDuplex(next);
  };

  const active = state === "hearing";
  return (
    <div className="mb-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className={cx("relative flex size-7 shrink-0 items-center justify-center rounded-full", active ? "bg-bad/25 text-bad" : "bg-accent/25 text-accent")}>
          <span
            className="absolute inset-0 rounded-full border-2 border-current opacity-60 transition-transform duration-100"
            style={{ transform: `scale(${1 + (muted ? 0 : level) * 0.6})` }}
          />
          {state === "transcribing" || state === "connecting" ? <Spinner className="size-3.5" /> : <MicIcon />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            {LIVE_LABEL[state]}
            {agentName && state === "agent-speaking" && <span className="text-ink-3"> · {agentName}</span>}
          </div>
          <div className="truncate text-xs text-ink-3" title={info?.fallbackReason ?? undefined}>
            {heard ? `“${heard}”` : info ? `Voice detection: ${info.vad}${info.fallbackReason ? " (fallback)" : ""}` : "Starting microphone…"}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            title={halfDuplex ? "Mic is muted while the agent talks (best with speakers)" : "Talk over the agent to interrupt (use headphones)"}
            onClick={toggleDuplex}
          >
            {halfDuplex ? "Wait for agent" : "Interruptible"}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              session.current?.setMuted(!muted);
              setMuted(!muted);
            }}
          >
            {muted ? "Unmute" : "Mute"}
          </Button>
          <Button size="sm" variant="danger" onClick={() => onClose()}>
            End
          </Button>
        </div>
      </div>
      {note && state !== "error" && <div className="mt-1 text-xs text-warn">{note}</div>}
    </div>
  );
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}

export interface LiveTarget {
  orgId: string;
  agentId: string;
  agentName?: string;
}

export function Composer({
  onSend,
  placeholder,
  disabled,
  voice = true,
  live,
  onLiveChange,
  onHearing,
  onLiveState,
  preferInterruptible,
  attach,
}: {
  onSend: (text: string, attachments?: string[]) => Promise<void> | void;
  /** Lets the user attach documents and images (uploaded to this agent's library, or the org's). */
  attach?: { orgId: string; agentId?: string | null };
  placeholder?: string;
  disabled?: boolean;
  voice?: boolean;
  /** Enables hands-free live talk with this agent. */
  live?: LiveTarget;
  onLiveChange?: (on: boolean) => void;
  /** Live talk heard the user start speaking. */
  onHearing?: () => void;
  /** Every live-talk state change (null when the session ends). */
  onLiveState?: (s: LiveState | null) => void;
  /** Live talk lets the user talk over the agent by default (teach-along). */
  preferInterruptible?: boolean;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<"idle" | "recording" | "transcribing">("idle");
  const [err, setErr] = useState<string | null>(null);
  const [liveOn, setLiveOn] = useState(false);
  const recorder = useRef<Listener | null>(null);
  const [voiceNow, setVoiceNow] = useState<VoiceStatus | null>(null);
  useEffect(() => {
    if (voice) void voiceStatus().then(setVoiceNow);
  }, [voice]);
  const micReady = canListen(voiceNow);
  const [files, setFiles] = useState<AttachmentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const addFiles = async (list: FileList | File[]) => {
    if (!attach) return;
    const picked = Array.from(list);
    if (!picked.length) return;
    setUploading(true);
    setErr(null);
    const done = await uploadFiles(attach.orgId, picked, attach.agentId, (m) => setErr(m));
    setFiles((f) => [...f, ...done]);
    setUploading(false);
  };
  // Files still being read finish on their own; keep the pills current.
  useEffect(() => {
    if (!files.some((f) => f.status === "processing")) return;
    const t = setInterval(() => {
      void Promise.all(files.map((f) => (f.status === "processing" ? api.attachmentInfo(f.id).catch(() => f) : f))).then(setFiles);
    }, 1500);
    return () => clearInterval(t);
  }, [files]);
  const removeFile = (f: AttachmentInfo) => {
    setFiles((all) => all.filter((x) => x.id !== f.id));
    void api.deleteAttachment(f.id).catch(() => {});
  };

  const setLive = (on: boolean, error?: string) => {
    setLiveOn(on);
    onLiveChange?.(on);
    if (error) setErr(error);
    else if (on) setErr(null);
  };
  const liveKey = live ? `${live.orgId}/${live.agentId}` : "";
  const onLiveChangeRef = useRef(onLiveChange);
  useLayoutEffect(() => {
    onLiveChangeRef.current = onLiveChange;
  });
  useEffect(() => {
    // Changing target (or unmounting) ends a live conversation.
    setLiveOn(false);
    return () => onLiveChangeRef.current?.(false);
  }, [liveKey]);

  const send = async (value = text) => {
    const v = value.trim();
    if ((!v && !files.length) || busy || uploading) return;
    setBusy(true);
    setErr(null);
    try {
      await onSend(v, files.map((f) => f.id));
      setText("");
      setFiles([]);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const startRec = async () => {
    setErr(null);
    stopSpeaking();
    try {
      recorder.current = await listen();
      setRec("recording");
    } catch (e) {
      setErr(e instanceof VoiceSetupError ? e.message : `Microphone unavailable: ${(e as Error).message}`);
      setRec("idle");
    }
  };
  const stopRec = async () => {
    if (!recorder.current || rec !== "recording") return;
    setRec("transcribing");
    try {
      const said = (await recorder.current.stop()).trim();
      if (said) await send(said);
    } catch (e) {
      setErr(`Transcription failed: ${(e as Error).message}`);
    } finally {
      setRec("idle");
      recorder.current = null;
    }
  };

  return (
    <div
      className={cx("border-t border-line p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]", dragging && "bg-accent/10 outline-dashed outline-2 -outline-offset-4 outline-accent")}
      onDragOver={
        attach
          ? (e) => {
              e.preventDefault();
              setDragging(true);
            }
          : undefined
      }
      onDragLeave={attach ? () => setDragging(false) : undefined}
      onDrop={
        attach
          ? (e) => {
              e.preventDefault();
              setDragging(false);
              void addFiles(e.dataTransfer.files);
            }
          : undefined
      }
    >
      {err && <div className="mb-2 whitespace-pre-wrap text-xs text-bad">{err}</div>}
      {(files.length > 0 || uploading) && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          {files.map((f) => (
            <FilePill key={f.id} file={f} onRemove={() => removeFile(f)} />
          ))}
          {uploading && <Spinner className="size-4" />}
        </div>
      )}
      {live && liveOn && (
        <LiveBar
          key={liveKey}
          orgId={live.orgId}
          agentId={live.agentId}
          agentName={live.agentName}
          onClose={(error) => setLive(false, error)}
          onHearing={onHearing}
          onLiveState={onLiveState}
          preferInterruptible={preferInterruptible}
        />
      )}
      <div className="flex items-end gap-2">
        <Textarea
          rows={1}
          className="max-h-40 min-h-9 resize-none"
          value={text}
          disabled={disabled}
          placeholder={placeholder ?? "Message…"}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            if (!attach) return;
            const pasted = Array.from(e.clipboardData.files);
            if (pasted.length) {
              e.preventDefault();
              void addFiles(pasted);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        {attach && (
          <>
            <button
              title="Attach documents or images (or drop / paste them here)"
              aria-label="Attach files"
              disabled={disabled}
              onClick={() => picker.current?.click()}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line-2 bg-panel-2 text-ink-2 transition-colors hover:text-ink cursor-pointer"
            >
              <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 11.5 12.5 20a5.5 5.5 0 0 1-7.8-7.8l8.5-8.5a3.7 3.7 0 0 1 5.2 5.2l-8.5 8.5a1.8 1.8 0 0 1-2.6-2.6l7.8-7.8" />
              </svg>
            </button>
            <input ref={picker} type="file" multiple className="hidden" onChange={(e) => (void addFiles(e.target.files ?? []), (e.target.value = ""))} />
          </>
        )}
        {voice && (
          <button
            title={micReady ? "Hold to talk" : "Voice input isn't set up yet (Settings → Voice)"}
            disabled={disabled || rec === "transcribing"}
            onMouseDown={startRec}
            onMouseUp={stopRec}
            onMouseLeave={stopRec}
            onTouchStart={startRec}
            onTouchEnd={stopRec}
            className={cx(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border transition-colors cursor-pointer",
              rec === "recording" ? "border-bad bg-bad/20 text-bad pulse-ring" : "border-line-2 bg-panel-2 text-ink-2 hover:text-ink",
              voiceNow && !micReady && "opacity-50",
            )}
          >
            {rec === "transcribing" ? <Spinner className="size-4" /> : <MicIcon />}
          </button>
        )}
        {voice && live && voiceNow?.server && (
          <button
            title={liveOn ? "End live talk" : "Live talk — hands-free conversation"}
            disabled={disabled}
            onClick={() => setLive(!liveOn)}
            className={cx(
              "flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors cursor-pointer",
              liveOn ? "border-accent bg-accent/20 text-accent" : "border-line-2 bg-panel-2 text-ink-2 hover:text-ink",
            )}
          >
            <span className={cx("size-2 rounded-full", liveOn ? "bg-accent animate-pulse" : "bg-ink-3")} />
            Live
          </button>
        )}
        <Button variant="primary" onClick={() => send()} loading={busy} disabled={disabled || uploading || (!text.trim() && !files.length)}>
          Send
        </Button>
      </div>
    </div>
  );
}

/** Speak new agent replies aloud (when enabled), once each. */
export function useAutoSpeak(messages: Message[], enabled: boolean): void {
  const spoken = useRef<Set<string>>(new Set());
  const primed = useRef(false);
  useEffect(() => {
    if (!primed.current) {
      messages.forEach((m) => spoken.current.add(m.id));
      primed.current = true;
      return;
    }
    for (const m of messages) {
      if (spoken.current.has(m.id)) continue;
      spoken.current.add(m.id);
      if (enabled && m.senderType === "agent") speak(m.content, m.senderId);
    }
  }, [messages, enabled]);
}
