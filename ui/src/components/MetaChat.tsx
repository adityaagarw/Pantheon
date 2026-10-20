"use client";

/** Chat with one of Pantheon's built-in meta agents (Zeus, Argus, Kronos). */

import { useShallow } from "zustand/react/shallow";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useOrg } from "@/store/org";
import { liveFor } from "@/store/reduce";
import { StatusBadge } from "./agent";
import { useDmMessages } from "./AgentInspector";
import { Composer, LiveThinking, MessageList, useAutoSpeak } from "./Chat";
import { ContextControls } from "./ContextControls";
import { ApprovalCard, ApproveAllButton } from "./InboxDrawer";
import { ModelSettings } from "./team/AgentEditor";
import { KindTab } from "./team/KindTab";
import { Button, ErrorNote, Modal, Tabs, Toggle } from "./ui";
import type { Agent, AgentKind } from "@/lib/types";

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
  const [modelOpen, setModelOpen] = useState(false);
  const [kinds, setKinds] = useState<AgentKind[]>([]);
  useEffect(() => {
    api.agentKinds().then(setKinds).catch(() => {});
  }, []);
  useAutoSpeak(messages, speakOn || liveOn);
  const name = agent?.name ?? fallbackName;
  const send = (t: string, files?: string[], o?: { steer: boolean }) =>
    api.send(SYSTEM_ORG, { to: agentId, content: t, attachments: files, steer: o?.steer }).then(() => undefined);
  const [resetOpen, setResetOpen] = useState(false);

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
          {agent && (
            <Button size="sm" variant="ghost" title={`Choose what runs ${name}: Pantheon with a model, or an agent such as Claude Code, Codex or Hermes`} onClick={() => setModelOpen(true)}>
              <span className="hidden sm:inline">Runs on: </span>
              <span className="max-w-32 truncate">{runsOn(agent, kinds)}</span>
            </Button>
          )}
          <Toggle checked={speakOn || liveOn} onChange={setSpeakOn} label="Speak" />
          {agent?.metaRole && (
            <Button size="sm" variant="ghost" title={`Start ${name} over with its original settings`} onClick={() => setResetOpen(true)}>
              Reset
            </Button>
          )}
          <Button size="sm" variant="ghost" className="lg:hidden" onClick={onShowSide}>
            {sideLabel}
          </Button>
        </div>
      </div>
      <ContextControls agentId={agentId} name={name} className="border-b border-line px-4 py-2 md:px-5" />
      {approvals.length > 0 && (
        <div className="space-y-2 border-b border-line p-4">
          {approvals.length > 1 && (
            <div className="flex justify-end">
              <ApproveAllButton orgId={SYSTEM_ORG} agentId={agentId} count={approvals.length} />
            </div>
          )}
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
      <Composer
        placeholder={placeholder}
        onSend={send}
        working={live.status === "working" ? { name } : null}
        attach={{ orgId: SYSTEM_ORG, agentId }}
        live={{ orgId: SYSTEM_ORG, agentId, agentName: name }}
        onLiveChange={setLiveOn}
      />
      {agent && <ModelDialog open={modelOpen} agent={agent} onClose={() => setModelOpen(false)} />}
      {agent?.metaRole && <ResetDialog open={resetOpen} role={agent.metaRole} name={name} onClose={() => setResetOpen(false)} />}
    </>
  );
}

/** What the header says a meta agent runs on: its model, or the agent that runs it. */
function runsOn(agent: Agent, kinds: AgentKind[]): string {
  const kindId = agent.kind ?? "llm";
  if (kindId === "llm") return agent.model?.model || "default";
  const kind = kinds.find((k) => k.id === kindId);
  const preset = kind?.presets.find((p) => p.id === agent.runner?.preset);
  return preset?.label ?? kind?.label ?? kindId;
}

/** Choose what runs a meta agent: Pantheon's own loop with a model (the global default
 *  unless one is picked), or any agent kind, such as Claude Code, Codex, Hermes or an ACP agent. */
function ModelDialog({ open, agent, onClose }: { open: boolean; agent: Agent; onClose: () => void }) {
  const [draft, setDraft] = useState<Agent>(agent);
  const [tab, setTab] = useState<"kind" | "model">("kind");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setDraft(agent);
      setTab("kind");
      setError(null);
    }
  }
  const llm = (draft.kind ?? "llm") === "llm";
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateAgent(agent.id, { kind: draft.kind ?? "llm", runner: draft.runner ?? {}, model: draft.model ?? {} });
      useOrg.setState((s) => ({ agents: { ...s.agents, [agent.id]: updated } }));
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`What runs ${agent.name}`}
      footer={
        <Button variant="primary" loading={busy} onClick={save}>
          Save
        </Button>
      }
    >
      <div className="grid gap-4">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: "kind", label: "Kind" },
            { value: "model", label: "Model" },
          ]}
        />
        {tab === "kind" && (
          <>
            <p className="text-sm text-ink-2">
              {agent.name} keeps its instructions, its tools and its chat with you whatever runs it. Another agent, such as Claude Code, Codex or Hermes, gets {agent.name}&apos;s tools through
              Pantheon&apos;s MCP bridge.
            </p>
            <KindTab draft={draft} exclude={["human"]} onChange={(kind, runner) => setDraft((d) => ({ ...d, kind, runner }))} />
          </>
        )}
        {tab === "model" && (
          <>
            <p className="text-sm text-ink-2">
              {llm
                ? `${agent.name} uses the default provider unless you pick one here. A strong model with reliable tool calling works best: ${agent.name} builds and changes organizations for you.`
                : `${agent.name} runs on another agent, which picks its own model on the Kind tab. Pantheon still uses this model for ${agent.name}'s quick jobs, such as drafting a new organization.`}
            </p>
            <ModelSettings model={draft.model ?? {}} onChange={(model) => setDraft((d) => ({ ...d, model }))} />
          </>
        )}
        <ErrorNote error={error} />
      </div>
    </Modal>
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

/** Start Zeus, Argus or Kronos over: chat cleared, original settings back. */
function ResetDialog({ open, role, name, onClose }: { open: boolean; role: "zeus" | "argus" | "kronos"; name: string; onClose: () => void }) {
  const [forget, setForget] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reset = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.resetMetaAgent(role, forget);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} title={`Reset ${name}?`}>
      <div className="space-y-3 text-sm">
        <p className="text-ink-2">
          This stops what {name} is doing, deletes your chat with {name}, clears its context, and puts its name, instructions, tools, kind, model and
          limits back to the originals.
        </p>
        <Toggle checked={forget} onChange={setForget} label={`Also forget ${name}'s long-term memories`} />
        <ErrorNote error={error} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" loading={busy} onClick={() => void reset()}>
            Reset {name}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
