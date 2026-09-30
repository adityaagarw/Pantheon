"use client";

/** Chat with one of Pantheon's built-in meta agents (Zeus, Argus). */

import { useShallow } from "zustand/react/shallow";
import { useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useOrg } from "@/store/org";
import { liveFor } from "@/store/reduce";
import { StatusBadge } from "./agent";
import { useDmMessages } from "./AgentInspector";
import { Composer, LiveThinking, MessageList, useAutoSpeak } from "./Chat";
import { ContextControls } from "./ContextControls";
import { ApprovalCard } from "./InboxDrawer";
import { Button, Toggle } from "./ui";

export const SYSTEM_ORG = "org_pantheon";

export function MetaChat({
  agentId,
  fallbackName,
  icon,
  subtitle,
  prompts,
  intro,
  placeholder,
  sideLabel,
  onShowSide,
}: {
  agentId: string;
  fallbackName: string;
  icon: ReactNode;
  subtitle: string;
  prompts: string[];
  intro: string;
  placeholder: string;
  sideLabel: string;
  onShowSide: () => void;
}) {
  const agent = useOrg((s) => s.agents[agentId]);
  const agents = useOrg((s) => s.agents);
  const live = useOrg(useShallow((s) => liveFor(s, agentId)));
  const approvals = useOrg(useShallow((s) => Object.values(s.approvals).filter((a) => a.agentId === agentId)));
  const messages = useDmMessages(agentId);
  const [speakOn, setSpeakOn] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  useAutoSpeak(messages, speakOn || liveOn);
  const name = agent?.name ?? fallbackName;
  const send = (t: string, files?: string[]) => api.send(SYSTEM_ORG, { to: agentId, content: t, attachments: files }).then(() => undefined);

  return (
    <>
      <div className="flex items-center gap-3 border-b border-line px-4 py-3 md:px-5">
        {icon}
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-semibold">
            {name} {agent && <StatusBadge status={live.status} />}
          </div>
          <div className="hidden text-xs text-ink-3 sm:block">{subtitle}</div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {(live.status === "working" || live.status === "awaiting_approval") && (
            <Button size="sm" variant="danger" onClick={() => api.stopAgent(agentId)}>
              ■ Stop
            </Button>
          )}
          <Toggle checked={speakOn || liveOn} onChange={setSpeakOn} label="Speak" />
          <Button size="sm" variant="ghost" className="lg:hidden" onClick={onShowSide}>
            {sideLabel}
          </Button>
        </div>
      </div>
      <ContextControls agentId={agentId} name={name} className="border-b border-line px-4 py-2 md:px-5" />
      {approvals.length > 0 && (
        <div className="space-y-2 border-b border-line p-4">
          {approvals.map((a) => (
            <ApprovalCard key={a.id} approval={a} />
          ))}
        </div>
      )}
      <MessageList
        messages={messages}
        agents={agents}
        empty={
          <div className="mx-auto max-w-xl py-10">
            <div className="text-center text-sm text-ink-2">{intro}</div>
            <div className="mt-5 grid gap-2">
              {prompts.map((p) => (
                <button key={p} onClick={() => send(p)} className="rounded-lg border border-line bg-panel px-4 py-3 text-left text-sm text-ink-2 hover:border-line-2 hover:text-ink cursor-pointer">
                  {p}
                </button>
              ))}
            </div>
          </div>
        }
      />
      {live.status === "working" && (
        <div className="border-t border-line bg-panel/50 px-5 py-2 font-mono text-xs text-ink-3">
          <LiveThinking text={live.thinking} />
          {live.tool ? `🛠 ${live.tool}…` : live.stream ? `✎ …${live.stream.slice(-160)}` : "thinking…"}
        </div>
      )}
      <Composer placeholder={placeholder} onSend={send} attach={{ orgId: SYSTEM_ORG, agentId }} live={{ orgId: SYSTEM_ORG, agentId, agentName: name }} onLiveChange={setLiveOn} />
    </>
  );
}

/** Page shell: chat on the left, a side panel (drawer on phones) on the right. */
export function MetaLayout({ chat, side, sideOpen, onCloseSide }: { chat: ReactNode; side: ReactNode; sideOpen: boolean; onCloseSide: () => void }) {
  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col">{chat}</section>
      <aside className={`flex-col border-l border-line bg-panel lg:static lg:flex lg:w-[440px] lg:shrink-0 ${sideOpen ? "fixed inset-0 z-30 flex" : "hidden"}`}>
        <button onClick={onCloseSide} className="border-b border-line px-3 py-2 text-left text-sm text-ink-2 hover:text-ink lg:hidden cursor-pointer">
          ‹ Back
        </button>
        {side}
      </aside>
    </div>
  );
}
