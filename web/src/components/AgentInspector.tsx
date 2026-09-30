"use client";


import { useShallow } from "zustand/react/shallow";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { AgentSchedule, Message } from "@/lib/types";
import { useOrg } from "@/store/org";
import { liveFor } from "@/store/reduce";
import { AgentAvatar, StatusBadge } from "./agent";
import { Composer, LiveThinking, MessageList, useAutoSpeak } from "./Chat";
import { ContextControls } from "./ContextControls";
import { AgentFiles } from "./Files";
import { ApprovalCard } from "./InboxDrawer";
import { Badge, Button, Toggle } from "./ui";

export function dmKey(a: string, b: string): string {
  const [x, y] = [a, b].sort();
  return `dm:${x}:${y}`;
}

export function useDmMessages(agentId: string): Message[] {
  const channel = useOrg((s) => Object.values(s.channels).find((c) => c.key === dmKey(agentId, "user")));
  const messages = useOrg((s) => (channel ? s.messagesByChannel[channel.id] : undefined));
  const loadChannel = useOrg((s) => s.loadChannel);
  useEffect(() => {
    if (channel && !messages) void loadChannel(channel.id);
  }, [channel, messages, loadChannel]);
  return messages ?? [];
}

export function AgentInspector({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const agent = useOrg((s) => s.agents[agentId]);
  const orgId = useOrg((s) => s.orgId);
  const live = useOrg(useShallow((s) => liveFor(s, agentId)));
  const agents = useOrg((s) => s.agents);
  const tasks = useOrg(useShallow((s) => Object.values(s.tasks).filter((t) => t.assigneeId === agentId && !["done", "cancelled"].includes(t.status))));
  const approvals = useOrg(useShallow((s) => Object.values(s.approvals).filter((a) => a.agentId === agentId)));
  const messages = useDmMessages(agentId);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  useAutoSpeak(messages, speakReplies || liveOn);

  useEffect(() => {
    api
      .voiceConfig()
      .then((c) => setSpeakReplies(c.autoSpeak))
      .catch(() => {});
  }, []);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  };

  const stream = useMemo(() => live.stream.trim(), [live.stream]);
  if (!agent || !orgId) return null;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line p-4">
        <div className="flex items-start gap-3">
          <AgentAvatar agent={agent} size={40} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate font-semibold">{agent.name}</span>
              <StatusBadge status={live.status} />
              {agent.status !== "active" && <Badge tone="neutral">{agent.status}</Badge>}
            </div>
            <div className="truncate text-sm text-ink-2">
              {agent.role}
              {agent.team && ` · ${agent.team}`}
            </div>
          </div>
          <button onClick={onClose} className="text-ink-3 hover:text-ink cursor-pointer" aria-label="Close">
            ✕
          </button>
        </div>
        {live.detail && live.status !== "working" && (
          <div className="mt-3 rounded-md border border-line bg-bg px-3 py-2 text-xs text-ink-2 whitespace-pre-wrap">{live.detail}</div>
        )}
        <div className="mt-3 flex flex-wrap gap-1.5">
          {live.status === "working" || live.status === "awaiting_approval" ? (
            <Button size="sm" variant="danger" loading={busy === "stop"} onClick={() => act("stop", () => api.stopAgent(agentId))}>
              ■ Stop turn
            </Button>
          ) : null}
          {(live.status === "error" || live.status === "retrying" || live.status === "cooling_down") && (
            <Button size="sm" variant="ok" loading={busy === "retry"} onClick={() => act("retry", () => api.retryAgent(agentId))}>
              ↻ Retry now
            </Button>
          )}
          <Button
            size="sm"
            loading={busy === "pause"}
            onClick={() => act("pause", () => api.updateAgent(agentId, { status: agent.status === "active" ? "paused" : "active" }))}
          >
            {agent.status === "active" ? "Pause" : "Resume"}
          </Button>
          <Link href={`/org/${orgId}/sessions?agent=${agentId}`}>
            <Button size="sm" variant="ghost">
              Sessions →
            </Button>
          </Link>
          <Link href={`/org/${orgId}/team?agent=${agentId}`}>
            <Button size="sm" variant="ghost">
              Configure →
            </Button>
          </Link>
        </div>
      </div>

      {approvals.length > 0 && (
        <div className="space-y-2 border-b border-line p-4">
          {approvals.map((a) => (
            <ApprovalCard key={a.id} approval={a} />
          ))}
        </div>
      )}

      {(live.status === "working" || stream) && (
        <div className="border-b border-line p-4">
          <div className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-ink-3">
            Live
            {live.tool && <Badge tone="accent">🛠 {live.tool}</Badge>}
          </div>
          <LiveThinking text={live.thinking} />
          <div className="mt-1.5 max-h-40 overflow-y-auto rounded-md bg-bg p-2.5 font-mono text-xs leading-relaxed text-ink-2 whitespace-pre-wrap">
            {stream ? stream.slice(-1500) : live.tool ? `running ${live.tool}…` : "thinking…"}
          </div>
        </div>
      )}

      <Schedules agentId={agentId} />

      <details className="border-b border-line">
        <summary className="cursor-pointer select-none px-4 py-3 text-xs font-medium uppercase tracking-wider text-ink-3 hover:text-ink-2">Files &amp; documents</summary>
        <div className="px-4 pb-4">
          <AgentFiles orgId={orgId} agentId={agentId} agentName={agent.name} compact />
        </div>
      </details>

      {tasks.length > 0 && (
        <div className="border-b border-line p-4">
          <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-ink-3">Open tasks</div>
          <div className="space-y-1">
            {tasks.map((t) => (
              <Link key={t.id} href={`/org/${orgId}/board?task=${t.id}`} className="flex items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-panel-2">
                <span className="font-mono text-xs text-ink-3">{t.ref}</span>
                <span className="truncate">{t.title}</span>
                <Badge className="ml-auto" tone={t.status === "blocked" ? "bad" : t.status === "review" ? "warn" : "neutral"}>
                  {t.status.replace("_", " ")}
                </Badge>
              </Link>
            ))}
          </div>
        </div>
      )}

      <ContextControls agentId={agentId} name={agent.name} className="border-b border-line px-4 py-2" />
      <div className="flex items-center justify-between px-4 pt-3 text-xs font-medium uppercase tracking-wider text-ink-3">
        Conversation with you
        <Toggle checked={speakReplies || liveOn} onChange={setSpeakReplies} label={<span className="normal-case tracking-normal">Speak replies</span>} />
      </div>
      <MessageList
        messages={messages}
        agents={agents}
        compact
        empty={<div className="py-8 text-center text-sm text-ink-3">Say hello, give {agent.name} a goal, or ask what they&apos;re working on.</div>}
      />
      <Composer
        placeholder={`Message ${agent.name}…`}
        onSend={(t, files) => api.send(orgId, { to: agentId, content: t, attachments: files }).then(() => undefined)}
        attach={{ orgId, agentId }}
        live={{ orgId, agentId, agentName: agent.name }}
        onLiveChange={setLiveOn}
      />
    </div>
  );
}
function Schedules({ agentId }: { agentId: string }) {
  const [items, setItems] = useState<AgentSchedule[]>([]);
  const turns = useOrg((s) => s.turnsVersion);
  useEffect(() => {
    api.agentSchedules(agentId).then(setItems).catch(() => {});
  }, [agentId, turns]);
  if (!items.length) return null;
  return (
    <div className="border-b border-line p-4">
      <div className="mb-1.5 text-xs font-medium uppercase tracking-wider text-ink-3">Schedules</div>
      <div className="space-y-1">
        {items.map((s) => (
          <div key={s.id} className="flex items-center gap-2 text-sm">
            <span className="shrink-0 rounded bg-panel-2 px-1.5 font-mono text-[11px] text-ink-2">{s.cron ?? "once"}</span>
            <span className="min-w-0 flex-1 truncate" title={s.message}>
              {s.message}
            </span>
            <span className="shrink-0 text-[11px] text-ink-3">{s.nextRunAt ? new Date(s.nextRunAt).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : ""}</span>
            <button className="text-ink-3 hover:text-bad cursor-pointer" title="Cancel" onClick={() => api.cancelSchedule(s.id).then(() => setItems((x) => x.filter((y) => y.id !== s.id)))}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
