"use client";

import { useEffect, useState } from "react";
import { AgentAvatar, UserAvatar } from "@/components/agent";
import { Markdown } from "@/components/Markdown";
import { Badge, Button, cx, Modal, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, duration, tokens, usd } from "@/lib/format";
import type { LlmCall, Message, ToolCall, TurnDetail } from "@/lib/types";
import { agentName, useOrg } from "@/store/org";

const TONE = { running: "accent", completed: "ok", failed: "bad", awaiting_approval: "warn", stopped: "neutral" } as const;

export function TurnView({ turnId }: { turnId: string }) {
  const [turn, setTurn] = useState<TurnDetail | null>(null);
  const [raw, setRaw] = useState<string | null>(null);
  const version = useOrg((s) => s.turnsVersion);
  const feedLen = useOrg((s) => s.feed.length);
  const agent = useOrg((s) => (turn ? s.agents[turn.agentId] : undefined));

  useEffect(() => {
    api.turn(turnId).then(setTurn).catch(() => {});
  }, [turnId, version]);
  // While running, refresh as activity arrives.
  useEffect(() => {
    if (turn?.status === "running" || turn?.status === "awaiting_approval") api.turn(turnId).then(setTurn).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedLen]);

  if (!turn) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  const tools = new Map(turn.toolCalls.map((t) => [t.id, t]));
  const turnCalls = turn.llmCalls.filter((c) => c.purpose === "turn");
  const other = turn.llmCalls.filter((c) => c.purpose !== "turn");
  const elapsed = turn.endedAt && turn.startedAt ? Date.parse(turn.endedAt) - Date.parse(turn.startedAt) : null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-5">
      <div className="flex items-start gap-3">
        <AgentAvatar agent={agent} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{agent?.name ?? turn.agentId}</span>
            <Badge tone={TONE[turn.status]}>{turn.status.replace("_", " ")}</Badge>
            <span className="font-mono text-xs text-ink-3">{turn.id}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-3">
            <span>{dateTime(turn.startedAt)}</span>
            {elapsed !== null && <span>{duration(elapsed)}</span>}
            <span>{turn.steps} model steps</span>
            <span>
              {tokens(turn.inputTokens)} in / {tokens(turn.outputTokens)} out
            </span>
            {turn.costUsd > 0 && <span>{usd(turn.costUsd)}</span>}
            {turn.attempts > 1 && <span className="text-warn">{turn.attempts} attempts</span>}
          </div>
        </div>
      </div>
      {turn.error && <div className="mt-4 rounded-lg border border-bad/30 bg-bad/10 p-3 font-mono text-xs text-bad whitespace-pre-wrap">{turn.error}</div>}

      <Timeline>
        <Step icon="📥" title="Inbox" subtitle={`${turn.inbox.length} message${turn.inbox.length === 1 ? "" : "s"}`}>
          <div className="space-y-2">
            {turn.inbox.map((m) => (
              <InboxMessage key={m.id} m={m} />
            ))}
          </div>
        </Step>

        {other
          .filter((c) => c.purpose === "compaction")
          .map((c) => (
            <Step key={c.id} icon="🗜" title="Memory compaction" subtitle={`${c.model} · ${duration(c.latencyMs)}`}>
              <div className="text-sm text-ink-2">{c.response.content}</div>
            </Step>
          ))}

        {turnCalls.map((c, i) => (
          <ModelStep key={c.id} call={c} index={i + 1} tools={tools} onRaw={() => setRaw(c.id)} />
        ))}

        {turn.status === "running" && (
          <Step icon="⏳" title="Working…">
            <LiveStream agentId={turn.agentId} />
          </Step>
        )}

        {turn.sent.length > 0 && (
          <Step icon="📤" title="Messages sent" subtitle={`${turn.sent.length}`}>
            <div className="space-y-2">
              {turn.sent.map((m) => (
                <SentMessage key={m.id} m={m} />
              ))}
            </div>
          </Step>
        )}

        {other.filter((c) => c.purpose === "meeting").length > 0 && (
          <Step icon="◎" title="Meeting model calls" subtitle={`${other.filter((c) => c.purpose === "meeting").length}`}>
            <div className="space-y-1.5">
              {other
                .filter((c) => c.purpose === "meeting")
                .map((c) => (
                  <div key={c.id} className="flex gap-2 text-sm">
                    <span className="shrink-0 font-medium">{agentName(c.agentId)}:</span>
                    <span className="text-ink-2">{c.response.content}</span>
                  </div>
                ))}
            </div>
          </Step>
        )}
      </Timeline>
      {raw && <RawPrompt callId={raw} onClose={() => setRaw(null)} />}
    </div>
  );
}

function Timeline({ children }: { children: React.ReactNode }) {
  return <div className="relative mt-6 space-y-4 before:absolute before:bottom-2 before:left-[15px] before:top-2 before:w-px before:bg-line">{children}</div>;
}

function Step({ icon, title, subtitle, children, tone }: { icon: string; title: React.ReactNode; subtitle?: React.ReactNode; children?: React.ReactNode; tone?: "bad" }) {
  return (
    <div className="relative flex gap-3">
      <div className={cx("z-10 flex size-8 shrink-0 items-center justify-center rounded-full border bg-panel text-sm", tone === "bad" ? "border-bad/50" : "border-line-2")}>{icon}</div>
      <div className="min-w-0 flex-1 pt-1">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold">{title}</span>
          {subtitle && <span className="text-xs text-ink-3">{subtitle}</span>}
        </div>
        {children && <div className="mt-2">{children}</div>}
      </div>
    </div>
  );
}

function ModelStep({ call, index, tools, onRaw }: { call: LlmCall; index: number; tools: Map<string, ToolCall>; onRaw: () => void }) {
  const [showReasoning, setShowReasoning] = useState(false);
  const r = call.response;
  return (
    <Step
      icon={call.error ? "⚠" : "🧠"}
      tone={call.error ? "bad" : undefined}
      title={`Step ${index}`}
      subtitle={
        <span className="flex flex-wrap items-center gap-3">
          <span>{call.model}</span>
          <span>{duration(call.latencyMs)}</span>
          <span>
            {tokens(call.inputTokens)}→{tokens(call.outputTokens)}
          </span>
          {call.costUsd > 0 && <span>{usd(call.costUsd)}</span>}
          <button onClick={onRaw} className="text-info hover:underline cursor-pointer">
            raw prompt
          </button>
        </span>
      }
    >
      {call.error && <div className="mb-2 rounded-md border border-bad/30 bg-bad/10 p-2 font-mono text-xs text-bad">{call.error}</div>}
      {r.reasoning && (
        <div className="mb-2">
          <button onClick={() => setShowReasoning((v) => !v)} className="text-xs text-ink-3 hover:text-ink cursor-pointer">
            {showReasoning ? "▾" : "▸"} Reasoning ({r.reasoning.length} chars)
          </button>
          {showReasoning && <div className="mt-1.5 rounded-md border border-line bg-bg p-3 text-xs italic leading-relaxed text-ink-2 whitespace-pre-wrap">{r.reasoning}</div>}
        </div>
      )}
      {r.content && (
        <div className="mb-2 rounded-md border border-line bg-panel p-3">
          <Markdown>{r.content}</Markdown>
        </div>
      )}
      {(r.tool_calls ?? []).length > 0 && (
        <div className="space-y-2">
          {r.tool_calls!.map((tc) => (
            <ToolBlock key={tc.id} name={tc.name} args={tc.args} result={tools.get(tc.id)} />
          ))}
        </div>
      )}
      {!r.content && !(r.tool_calls ?? []).length && !call.error && <div className="text-xs text-ink-3">(empty response)</div>}
    </Step>
  );
}

function ToolBlock({ name, args, result }: { name: string; args: Record<string, unknown>; result?: ToolCall }) {
  const [open, setOpen] = useState(false);
  const tone = !result ? "neutral" : result.status === "ok" ? "ok" : result.status === "denied" ? "warn" : result.status === "running" ? "accent" : "bad";
  const argSummary = Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v.length > 50 ? `${v.slice(0, 50)}…` : v) : JSON.stringify(v)}`)
    .join(" ");
  return (
    <div className="overflow-hidden rounded-md border border-line">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 bg-panel-2 px-3 py-2 text-left cursor-pointer">
        <span className="text-ink-3">{open ? "▾" : "▸"}</span>
        <code className="font-mono text-xs font-semibold text-info">{name}</code>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-3">{argSummary}</span>
        <Badge tone={tone}>{result ? result.status : "no result"}</Badge>
        {result && <span className="text-[11px] text-ink-3">{duration(result.durationMs)}</span>}
      </button>
      {open && (
        <div className="grid gap-2 bg-bg p-3">
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-3">Arguments</div>
            <pre className="max-h-72 overflow-auto rounded border border-line bg-panel p-2 font-mono text-xs text-ink-2">{JSON.stringify(args, null, 2)}</pre>
          </div>
          {result && (
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-3">Result</div>
              <pre className="max-h-96 overflow-auto rounded border border-line bg-panel p-2 font-mono text-xs text-ink-2 whitespace-pre-wrap">{result.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InboxMessage({ m }: { m: Message }) {
  const agent = useOrg((s) => s.agents[m.senderId]);
  return (
    <div className="flex gap-2.5 rounded-md border border-line bg-panel p-2.5">
      {m.senderType === "user" ? <UserAvatar size={22} /> : m.senderType === "system" ? <span className="w-[22px] text-center">⚙</span> : <AgentAvatar agent={agent} size={22} />}
      <div className="min-w-0 flex-1">
        <div className="text-xs text-ink-3">
          <span className="font-medium text-ink-2">{m.senderType === "user" ? "You" : m.senderType === "system" ? "System" : (agent?.name ?? String(m.meta?.senderName ?? m.senderId))}</span> · {m.kind.replace("_", " ")}
        </div>
        <Markdown className="mt-0.5">{m.content}</Markdown>
      </div>
    </div>
  );
}

function SentMessage({ m }: { m: Message }) {
  const channels = useOrg((s) => s.channels);
  const ch = m.channelId ? channels[m.channelId] : undefined;
  const to = ch ? (ch.kind === "dm" ? agentName(ch.members.find((x) => x !== m.senderId)) : ch.key) : "inbox";
  return (
    <div className="rounded-md border border-line bg-panel p-2.5">
      <div className="text-xs text-ink-3">
        → <span className="font-medium text-ink-2">{to}</span> · {m.kind}
      </div>
      <Markdown className="mt-0.5">{m.content}</Markdown>
    </div>
  );
}

function LiveStream({ agentId }: { agentId: string }) {
  const stream = useOrg((s) => s.live[agentId]?.stream ?? "");
  const tool = useOrg((s) => s.live[agentId]?.tool);
  return (
    <div className="rounded-md bg-bg p-2.5 font-mono text-xs text-ink-2 whitespace-pre-wrap">
      {tool ? `running ${tool}…` : stream ? stream.slice(-1200) : "thinking…"}
    </div>
  );
}

function RawPrompt({ callId, onClose }: { callId: string; onClose: () => void }) {
  const [call, setCall] = useState<LlmCall | null>(null);
  useEffect(() => {
    api.llmCall(callId).then(setCall).catch(() => {});
  }, [callId]);
  return (
    <Modal open onClose={onClose} title="Exact prompt sent to the model" wide footer={<Button onClick={onClose}>Close</Button>}>
      {!call?.request ? (
        <Spinner />
      ) : (
        <div className="space-y-3">
          <div className="text-xs text-ink-3">
            {call.request.provider} · {call.request.model} · tools offered: {call.request.tools.join(", ") || "none"}
          </div>
          {call.request.messages.map((m, i) => (
            <div key={i} className="rounded-md border border-line">
              <div className="border-b border-line bg-panel-2 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-2">
                {m.role}
                {m.tool_call_id && <span className="ml-2 font-mono normal-case text-ink-3">{m.tool_call_id}</span>}
              </div>
              <pre className="max-h-96 overflow-auto p-3 font-mono text-xs leading-relaxed text-ink-2 whitespace-pre-wrap">
                {m.content}
                {m.tool_calls && `\n\n→ tool calls: ${JSON.stringify(m.tool_calls, null, 2)}`}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
