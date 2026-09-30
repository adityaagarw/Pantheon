"use client";


import { useShallow } from "zustand/react/shallow";
import { useEffect, useMemo, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { Composer, MessageList, useAutoSpeak } from "@/components/Chat";
import { Badge, Button, cx, Empty, ErrorNote, Field, Input, Modal, Select, Toggle } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Channel } from "@/lib/types";
import { agentName, useOrg } from "@/store/org";

export default function CommsPage() {
  const orgId = useOrg((s) => s.orgId)!;
  const channelsMap = useOrg((s) => s.channels);
  const agents = useOrg((s) => s.agents);
  const [selected, setSelected] = useState<string | null>(null);
  const [newDm, setNewDm] = useState(false);
  const [creating, setCreating] = useState(false);

  const channels = useMemo(() => Object.values(channelsMap), [channelsMap]);
  const byRecent = (a: Channel, b: Channel) => (b.lastMessageAt ?? "").localeCompare(a.lastMessageAt ?? "");
  const groups = {
    channels: channels.filter((c) => c.kind === "channel").sort((a, b) => a.key.localeCompare(b.key)),
    mine: channels.filter((c) => c.kind === "dm" && c.members.includes("user")).sort(byRecent),
    agents: channels.filter((c) => c.kind === "dm" && !c.members.includes("user")).sort(byRecent),
    meetings: channels.filter((c) => c.kind === "meeting").sort(byRecent),
  };
  const current = selected ? channelsMap[selected] : (groups.channels[0] ?? null);

  const label = (c: Channel) =>
    c.kind === "channel" ? c.key : c.kind === "meeting" ? c.name : c.members.filter((m) => m !== "user").map(agentName).join(" ↔ ");

  const Item = ({ c }: { c: Channel }) => {
    const others = c.members.filter((m) => m !== "user" && agents[m]);
    return (
      <button
        onClick={() => setSelected(c.id)}
        className={cx(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors cursor-pointer",
          current?.id === c.id ? "bg-panel-2 text-ink" : "text-ink-2 hover:bg-panel-2/60",
        )}
      >
        {c.kind === "dm" ? (
          <span className="flex -space-x-1.5">
            {others.slice(0, 2).map((m) => (
              <AgentAvatar key={m} agent={agents[m]} size={18} />
            ))}
          </span>
        ) : (
          <span className="w-4 text-center text-ink-3">{c.kind === "meeting" ? "◎" : "#"}</span>
        )}
        <span className="truncate">{c.kind === "channel" ? c.name : label(c)}</span>
        {c.lastMessageAt && <span className="ml-auto shrink-0 text-[10px] text-ink-3">{timeAgo(c.lastMessageAt)}</span>}
      </button>
    );
  };

  return (
    <div className="flex h-full">
      {/* Phones show the list or the conversation, never both. */}
      <aside className={cx("w-full shrink-0 overflow-y-auto border-r border-line bg-panel p-3 md:block md:w-72", selected && "hidden")}>
        <Group title="Channels" action={<button className="text-ink-3 hover:text-ink cursor-pointer" onClick={() => setCreating(true)}>+</button>}>
          {groups.channels.map((c) => (
            <Item key={c.id} c={c} />
          ))}
        </Group>
        <Group title="Your conversations" action={<button className="text-ink-3 hover:text-ink cursor-pointer" onClick={() => setNewDm(true)}>+</button>}>
          {groups.mine.map((c) => (
            <Item key={c.id} c={c} />
          ))}
          {groups.mine.length === 0 && <div className="px-2 text-xs text-ink-3">No direct messages yet.</div>}
        </Group>
        <Group title="Between agents" hint="read-only">
          {groups.agents.map((c) => (
            <Item key={c.id} c={c} />
          ))}
          {groups.agents.length === 0 && <div className="px-2 text-xs text-ink-3">Agents haven&apos;t messaged each other yet.</div>}
        </Group>
        {groups.meetings.length > 0 && (
          <Group title="Meetings">
            {groups.meetings.map((c) => (
              <Item key={c.id} c={c} />
            ))}
          </Group>
        )}
      </aside>
      <section className={cx("min-w-0 flex-1 flex-col md:flex", selected ? "flex" : "hidden")}>
        {selected?.startsWith("pending:") ? (
          <PendingDm agentId={selected.slice(8)} orgId={orgId} onCreated={(id) => setSelected(id)} onBack={() => setSelected(null)} />
        ) : current ? (
          <ChannelView key={current.id} channel={current} orgId={orgId} title={label(current)} onBack={() => setSelected(null)} />
        ) : (
          <Empty title="Pick a conversation" />
        )}
      </section>
      <NewDmModal open={newDm} onClose={() => setNewDm(false)} onPick={(agentId) => {
        const key = [agentId, "user"].sort().join(":");
        const existing = channels.find((c) => c.key === `dm:${key}`);
        if (existing) setSelected(existing.id);
        else setSelected(`pending:${agentId}`);
        setNewDm(false);
      }} />
      <NewChannelModal open={creating} onClose={() => setCreating(false)} orgId={orgId} onCreated={(id) => setSelected(id)} />
    </div>
  );
}

function Group({ title, hint, action, children }: { title: string; hint?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="mb-1 flex items-center justify-between px-2 text-[11px] font-medium uppercase tracking-wider text-ink-3">
        <span>
          {title} {hint && <span className="normal-case tracking-normal opacity-70">· {hint}</span>}
        </span>
        {action}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} aria-label="Back" className="-ml-2 flex size-9 shrink-0 items-center justify-center rounded-md text-ink-2 hover:bg-panel-2 md:hidden cursor-pointer">
      ‹
    </button>
  );
}

function ChannelView({ channel, orgId, title, onBack }: { channel: Channel; orgId: string; title: string; onBack: () => void }) {
  const agents = useOrg((s) => s.agents);
  const messages = useOrg((s) => s.messagesByChannel[channel.id]);
  const loadChannel = useOrg((s) => s.loadChannel);
  const [speak, setSpeak] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  useEffect(() => {
    void loadChannel(channel.id);
  }, [channel.id, loadChannel]);
  useAutoSpeak(messages ?? [], (speak || liveOn) && channel.members.includes("user"));

  const canPost = channel.kind === "channel" || (channel.kind === "dm" && channel.members.includes("user"));
  const dmTarget = channel.kind === "dm" ? channel.members.find((m) => m !== "user") : undefined;
  return (
    <>
      <div className="flex items-center gap-3 border-b border-line px-4 py-3 md:px-5">
        <BackButton onClick={onBack} />
        <div className="min-w-0">
          <div className="truncate font-semibold">{title}</div>
          <div className="truncate text-xs text-ink-3">
            {channel.topic || `${channel.members.length} members`} {channel.kind === "channel" && channel.notify === "mentions" && "· notifies on @mention"}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <div className="hidden -space-x-1.5 sm:flex">
            {channel.members
              .filter((m) => agents[m])
              .slice(0, 8)
              .map((m) => (
                <AgentAvatar key={m} agent={agents[m]} size={22} />
              ))}
          </div>
          {channel.members.includes("user") && <Toggle checked={speak || liveOn} onChange={setSpeak} label="Speak" />}
        </div>
      </div>
      <MessageList messages={messages ?? []} agents={agents} />
      {canPost ? (
        <Composer
          placeholder={dmTarget ? `Message ${agentName(dmTarget)}…` : `Post to ${channel.key} (use @Name to mention)…`}
          onSend={(t, files) => api.send(orgId, dmTarget ? { to: dmTarget, content: t, attachments: files } : { channel: channel.id, content: t, attachments: files }).then(() => undefined)}
          attach={{ orgId, agentId: dmTarget }}
          live={dmTarget ? { orgId, agentId: dmTarget, agentName: agentName(dmTarget) } : undefined}
          onLiveChange={setLiveOn}
        />
      ) : (
        <div className="border-t border-line px-5 py-3 text-xs text-ink-3">You&apos;re observing a conversation between agents.</div>
      )}
    </>
  );
}

function PendingDm({ agentId, orgId, onCreated, onBack }: { agentId: string; orgId: string; onCreated: (id: string) => void; onBack: () => void }) {
  const agent = useOrg((s) => s.agents[agentId]);
  return (
    <>
      <div className="flex items-center gap-3 border-b border-line px-4 py-3 font-semibold md:px-5">
        <BackButton onClick={onBack} />
        {agent?.name}
      </div>
      <div className="flex flex-1 items-center justify-center text-sm text-ink-3">Start a conversation with {agent?.name}.</div>
      <Composer
        placeholder={`Message ${agent?.name}…`}
        attach={{ orgId, agentId }}
        onSend={async (t, files) => {
          const m = await api.send(orgId, { to: agentId, content: t, attachments: files });
          const chs = await api.channels(orgId);
          useOrg.setState({ channels: Object.fromEntries(chs.map((c) => [c.id, c])) });
          if (m.channelId) onCreated(m.channelId);
        }}
      />
    </>
  );
}

function NewDmModal({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (agentId: string) => void }) {
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  return (
    <Modal open={open} onClose={onClose} title="Message an agent">
      <div className="space-y-1">
        {agents.map((a) => (
          <button key={a.id} onClick={() => onPick(a.id)} className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-panel-2 cursor-pointer">
            <AgentAvatar agent={a} size={28} />
            <div>
              <div className="text-sm font-medium">{a.name}</div>
              <div className="text-xs text-ink-3">{a.role}</div>
            </div>
          </button>
        ))}
      </div>
    </Modal>
  );
}

function NewChannelModal({ open, onClose, orgId, onCreated }: { open: boolean; onClose: () => void; orgId: string; onCreated: (id: string) => void }) {
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [notify, setNotify] = useState<"all" | "mentions">("all");
  const [members, setMembers] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const ch = await api.createChannel(orgId, { name, topic, notify, members: [...members, "user"] });
      useOrg.setState((s) => ({ channels: { ...s.channels, [ch.id]: ch } }));
      onCreated(ch.id);
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
      title="New channel"
      footer={
        <Button variant="primary" onClick={submit} loading={busy} disabled={!name.trim()}>
          Create
        </Button>
      }
    >
      <div className="grid gap-4">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="launch" />
        </Field>
        <Field label="Topic">
          <Input value={topic} onChange={(e) => setTopic(e.target.value)} />
        </Field>
        <Field label="Notifications" hint="Every post wakes every member when set to all">
          <Select value={notify} onChange={(e) => setNotify(e.target.value as "all" | "mentions")}>
            <option value="all">All posts reach every member</option>
            <option value="mentions">Only @mentions reach members</option>
          </Select>
        </Field>
        <div>
          <div className="mb-1.5 text-xs font-medium text-ink-2">Members</div>
          <div className="flex flex-wrap gap-2">
            {agents.map((a) => (
              <button
                key={a.id}
                onClick={() => setMembers((m) => (m.includes(a.id) ? m.filter((x) => x !== a.id) : [...m, a.id]))}
                className={cx(
                  "flex items-center gap-1.5 rounded-full border py-0.5 pl-0.5 pr-2.5 text-xs cursor-pointer",
                  members.includes(a.id) ? "border-accent bg-accent/15" : "border-line-2",
                )}
              >
                <AgentAvatar agent={a} size={18} /> {a.name}
              </button>
            ))}
          </div>
        </div>
        <ErrorNote error={error} />
        <Badge>You are always added</Badge>
      </div>
    </Modal>
  );
}