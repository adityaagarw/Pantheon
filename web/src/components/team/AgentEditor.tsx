"use client";

import { useEffect, useMemo, useState } from "react";
import { AgentAvatar, StatusBadge } from "@/components/agent";
import { Markdown } from "@/components/Markdown";
import { Badge, Button, cx, ErrorNote, Field, Input, Select, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { ContextControls } from "@/components/ContextControls";
import { AgentFiles } from "@/components/Files";
import type { Agent, CatalogTool, McpServer, Provider, TaskPolicy, ToolEntry } from "@/lib/types";
import { CHARACTERS, characterFor, characterThumb } from "@/office/characters";
import { useOrg } from "@/store/org";

type Tab = "profile" | "model" | "tools" | "files" | "permissions" | "workspace" | "look";

export function AgentEditor({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const agent = useOrg((s) => s.agents[agentId]);
  const orgId = useOrg((s) => s.orgId)!;
  const [tab, setTab] = useState<Tab>("profile");
  const [draft, setDraft] = useState<Agent>(agent);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setDraft(agent), [agentId]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = useMemo(() => JSON.stringify(pick(draft)) !== JSON.stringify(pick(agent)), [draft, agent]);
  if (!agent) return null;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateAgent(agentId, pick(draft));
      useOrg.setState((s) => ({ agents: { ...s.agents, [agentId]: updated } }));
      setDraft(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const set = <K extends keyof Agent>(k: K, v: Agent[K]) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line px-5 py-3.5">
        <AgentAvatar agent={draft} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 font-semibold">
            {agent.name} <StatusBadge status={agent.runtimeStatus} />
          </div>
          <div className="text-xs text-ink-3">{agent.role}</div>
        </div>
        <button className="text-ink-3 hover:text-ink cursor-pointer" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="px-5 pt-3">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: "profile", label: "Profile" },
            { value: "model", label: "Model" },
            { value: "tools", label: `Tools (${draft.tools.length})` },
            { value: "files", label: "Files" },
            { value: "permissions", label: "Permissions" },
            { value: "workspace", label: "Workspace" },
            { value: "look", label: "Look" },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {tab === "profile" && <ProfileTab draft={draft} set={set} />}
        {tab === "model" && <ModelSettings model={draft.model ?? {}} onChange={(m) => set("model", m)} />}
        {tab === "tools" && <ToolsTab draft={draft} set={set} orgId={orgId} />}
        {tab === "files" && <AgentFiles orgId={orgId} agentId={agentId} agentName={draft.name} />}
        {tab === "permissions" && <PermissionsTab draft={draft} set={set} />}
        {tab === "workspace" && <WorkspaceTab draft={draft} set={set} agentId={agentId} onDeleted={onClose} />}
        {tab === "look" && <LookTab draft={draft} set={set} />}
      </div>
      <div className="flex items-center gap-2 border-t border-line px-5 py-3">
        <ErrorNote error={error} />
        {saved && <span className="text-sm text-ok">Saved</span>}
        <div className="ml-auto flex gap-2">
          <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(agent)}>
            Discard
          </Button>
          <Button variant="primary" disabled={!dirty} loading={saving} onClick={save}>
            Save changes
          </Button>
        </div>
      </div>
    </div>
  );
}

function pick(a: Agent): Partial<Agent> {
  return {
    name: a.name,
    role: a.role,
    team: a.team,
    persona: a.persona,
    model: a.model,
    tools: a.tools,
    worktree: a.worktree,
    limits: a.limits,
    avatar: a.avatar,
    status: a.status,
    permissions: a.permissions,
  };
}

type SetFn = <K extends keyof Agent>(k: K, v: Agent[K]) => void;

function ProfileTab({ draft, set }: { draft: Agent; set: SetFn }) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name">
          <Input value={draft.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Role">
          <Input value={draft.role} onChange={(e) => set("role", e.target.value)} />
        </Field>
        <Field label="Team" hint="Teams sit together in the office">
          <Input value={draft.team} onChange={(e) => set("team", e.target.value)} />
        </Field>
        <Field label="Status">
          <Select value={draft.status} onChange={(e) => set("status", e.target.value as Agent["status"])}>
            <option value="active">Active</option>
            <option value="paused">Paused (keeps inbox, doesn&apos;t work)</option>
            <option value="disabled">Disabled (receives nothing)</option>
          </Select>
        </Field>
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-medium text-ink-2">Persona — the agent&apos;s system prompt</span>
          <button className="text-xs text-info hover:underline cursor-pointer" onClick={() => setPreview((v) => !v)}>
            {preview ? "Edit" : "Preview"}
          </button>
        </div>
        {preview ? (
          <div className="min-h-64 rounded-md border border-line bg-bg p-3">
            <Markdown>{draft.persona || "_Empty_"}</Markdown>
          </div>
        ) : (
          <Textarea className="min-h-80 font-mono text-[13px]" value={draft.persona} onChange={(e) => set("persona", e.target.value)} />
        )}
        <div className="mt-1.5 text-xs text-ink-3">
          Pantheon adds the org context, colleagues, open tasks, operating rules and tool list automatically — focus on who this agent is and how it works.
        </div>
      </div>
    </div>
  );
}

/** Provider, model, thinking and limits for one agent (the editor's Model tab; also Zeus and Argus). */
export function ModelSettings({ model, onChange }: { model: Agent["model"]; onChange: (m: Agent["model"]) => void }) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const org = useOrg((s) => s.org);
  useEffect(() => {
    api.providers().then(setProviders).catch(() => {});
  }, []);
  const m = model ?? {};
  const upd = (patch: Partial<Agent["model"]>) => onChange({ ...m, ...patch });
  const provider = providers.find((p) => p.id === m.provider_id);
  const orgDefault = org?.settings.default_model;
  return (
    <div className="grid gap-4">
      <Field label="Provider" hint={!m.provider_id ? `Inherits ${orgDefault?.provider_id ? "the org default" : "the global default"}` : undefined}>
        <Select value={m.provider_id ?? ""} onChange={(e) => upd({ provider_id: e.target.value || undefined, model: undefined })}>
          <option value="">Default</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.type})
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Model">
        <Input list="models" value={m.model ?? ""} onChange={(e) => upd({ model: e.target.value || undefined })} placeholder={provider?.defaultModel ?? "provider default"} />
        <datalist id="models">
          {(provider?.models ?? []).map((x) => (
            <option key={x.id} value={x.id} />
          ))}
        </datalist>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label={`Temperature ${m.temperature ?? "default"}`}>
          <input type="range" min={0} max={1.5} step={0.05} value={m.temperature ?? 0.7} onChange={(e) => upd({ temperature: Number(e.target.value) })} className="w-full accent-[#8b7cff]" />
        </Field>
        <Field label="Thinking" hint="Off skips thinking (faster, cheaper). A server that doesn't offer a level gets the closest one it does.">
          <Select value={m.reasoning_effort ?? ""} onChange={(e) => upd({ reasoning_effort: (e.target.value || undefined) as Agent["model"]["reasoning_effort"] })}>
            <option value="">Provider default</option>
            <option value="off">Off — no thinking</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="xhigh">Extra high</option>
          </Select>
        </Field>
        <Field label="Max output tokens">
          <Input type="number" value={m.max_tokens ?? ""} onChange={(e) => upd({ max_tokens: e.target.value ? Number(e.target.value) : undefined })} placeholder="default" />
        </Field>
        <Field label="Context window" hint="Compaction starts at 60%">
          <Input type="number" value={m.context_window ?? ""} onChange={(e) => upd({ context_window: e.target.value ? Number(e.target.value) : undefined })} placeholder="from provider (32k)" />
        </Field>
        <Field label="Sees images" hint="For the browser, computer use and Stage checks">
          <Select value={m.vision === undefined ? "" : String(m.vision)} onChange={(e) => upd({ vision: e.target.value === "" ? undefined : e.target.value === "true" })}>
            <option value="">From the provider&apos;s model settings</option>
            <option value="true">Yes — send screenshots</option>
            <option value="false">No — text only</option>
          </Select>
        </Field>
      </div>
    </div>
  );
}

function ToolsTab({ draft, set, orgId }: { draft: Agent; set: SetFn; orgId: string }) {
  const [catalog, setCatalog] = useState<CatalogTool[]>([]);
  const [servers, setServers] = useState<McpServer[]>([]);
  useEffect(() => {
    api.toolCatalog().then(setCatalog).catch(() => {});
    api.mcpServers(orgId).then(setServers).catch(() => {});
  }, [orgId]);
  const entries = new Map(draft.tools.map((t) => [t.name, t]));
  const setEntry = (name: string, e: ToolEntry | null) => {
    const next = draft.tools.filter((t) => t.name !== name);
    if (e) next.push(e);
    set("tools", next);
  };
  const cats = [...new Set(catalog.map((c) => c.category))];
  const catLabel: Record<string, string> = {
    communication: "Communication",
    tasks: "Tasks",
    memory: "Memory",
    files: "Files",
    shell: "Shell",
    web: "Web",
    meta: "Self-improvement",
  };
  return (
    <div className="space-y-5">
      {cats.map((cat) => (
        <section key={cat}>
          <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">{catLabel[cat] ?? cat}</div>
          <div className="divide-y divide-line rounded-lg border border-line">
            {catalog
              .filter((c) => c.category === cat)
              .map((c) => {
                const e = entries.get(c.name);
                return (
                  <ToolRow
                    key={c.name}
                    name={c.name}
                    description={c.description.split("\n")[0]}
                    enabled={!!e}
                    approval={e?.approval ?? c.defaultApproval}
                    risky={c.sideEffects}
                    onToggle={(on) => setEntry(c.name, on ? { name: c.name, approval: c.defaultApproval } : null)}
                    onApproval={(a) => setEntry(c.name, { name: c.name, approval: a })}
                  />
                );
              })}
          </div>
        </section>
      ))}
      <section>
        <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">MCP servers</div>
        {servers.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line-2 p-4 text-sm text-ink-3">No MCP servers configured. Add them in Settings (global) or the org&apos;s Settings tab.</div>
        ) : (
          <div className="divide-y divide-line rounded-lg border border-line">
            {servers.map((s) => {
              const key = `mcp:${s.name}:*`;
              const e = entries.get(key);
              return (
                <ToolRow
                  key={s.id}
                  name={`${s.name} (all tools)`}
                  description={`${s.transport} · ${s.description || s.command || s.url || ""}`}
                  enabled={!!e}
                  approval={e?.approval ?? "auto"}
                  onToggle={(on) => setEntry(key, on ? { name: key, approval: "auto" } : null)}
                  onApproval={(a) => setEntry(key, { name: key, approval: a })}
                  badge={<Badge tone={s.status === "ready" ? "ok" : s.status === "error" ? "bad" : "neutral"}>{s.status}</Badge>}
                />
              );
            })}
          </div>
        )}
      </section>
      <div className="text-xs text-ink-3">
        <b>Ask</b> pauses the agent until you approve each call in the Inbox. Permissions are enforced by the runtime on every call, not just in the prompt.
      </div>
    </div>
  );
}

function ToolRow({
  name,
  description,
  enabled,
  approval,
  risky,
  onToggle,
  onApproval,
  badge,
}: {
  name: string;
  description: string;
  enabled: boolean;
  approval: "auto" | "ask" | "deny";
  risky?: boolean;
  onToggle: (on: boolean) => void;
  onApproval: (a: "auto" | "ask") => void;
  badge?: React.ReactNode;
}) {
  return (
    <div className={cx("flex items-center gap-3 px-3 py-2.5", !enabled && "opacity-60")}>
      <input type="checkbox" checked={enabled} onChange={(e) => onToggle(e.target.checked)} className="size-4 accent-[#8b7cff]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <code className="font-mono text-xs font-semibold">{name}</code>
          {risky && <Badge tone="warn">side effects</Badge>}
          {badge}
        </div>
        <div className="truncate text-xs text-ink-3">{description}</div>
      </div>
      {enabled && (
        <Select className="h-7 w-28 text-xs" value={approval === "deny" ? "auto" : approval} onChange={(e) => onApproval(e.target.value as "auto" | "ask")}>
          <option value="auto">Auto</option>
          <option value="ask">Ask me</option>
        </Select>
      )}
    </div>
  );
}

function WorkspaceTab({ draft, set, agentId, onDeleted }: { draft: Agent; set: SetFn; agentId: string; onDeleted: () => void }) {
  const org = useOrg((s) => s.org);
  const [confirm, setConfirm] = useState<string | null>(null);
  return (
    <div className="grid gap-5">
      <Field label="Worktree" hint={`Relative to the org workspace${org?.workspace ? ` (${org.workspace})` : ""}`}>
        <Input value={draft.worktree ?? ""} onChange={(e) => set("worktree", e.target.value || null)} placeholder="(the org workspace root)" />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Max steps per turn" hint="Model calls before wrapping up">
          <Input type="number" value={draft.limits.max_steps_per_turn ?? ""} placeholder="30" onChange={(e) => set("limits", { ...draft.limits, max_steps_per_turn: e.target.value ? Number(e.target.value) : undefined })} />
        </Field>
        <Field label="Max turns per hour" hint="Circuit breaker">
          <Input type="number" value={draft.limits.max_turns_per_hour ?? ""} placeholder="120" onChange={(e) => set("limits", { ...draft.limits, max_turns_per_hour: e.target.value ? Number(e.target.value) : undefined })} />
        </Field>
      </div>
      <section className="rounded-lg border border-line p-4">
        <div className="mb-2 text-sm font-semibold">Working context</div>
        <ContextControls agentId={agentId} name={draft.name} />
      </section>
      <section className="rounded-lg border border-bad/30 p-4">
        <div className="text-sm font-semibold text-bad">Danger zone</div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="danger" size="sm" onClick={() => setConfirm("delete")}>
            Remove agent
          </Button>
        </div>
        {confirm && (
          <div className="mt-3 flex items-center gap-2 text-sm">
            <span className="text-ink-2">Remove this agent? Open tasks return to the backlog.</span>
            <Button
              size="sm"
              variant="danger"
              onClick={async () => {
                await api.deleteAgent(agentId);
                onDeleted();
                setConfirm(null);
              }}
            >
              Confirm
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}

function LookTab({ draft, set }: { draft: Agent; set: SetFn }) {
  const av = draft.avatar;
  const current = characterFor(draft);
  return (
    <div className="grid gap-4">
      <div>
        <div className="mb-2 text-xs font-medium text-ink-2">Character in the office</div>
        <div className="grid grid-cols-5 gap-2">
          {CHARACTERS.map((k) => (
            <button
              key={k}
              onClick={() => set("avatar", { ...av, character: k })}
              className={cx(
                "flex aspect-square items-center justify-center rounded-lg border bg-white/90 transition-colors cursor-pointer",
                current === k ? "border-accent ring-2 ring-accent/50" : "border-line-2 hover:border-ink-3",
              )}
              title={k}
            >
              {k === "robot" ? (
                <span className="text-3xl">🤖</span>
              ) : (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={characterThumb(k)} alt={k} className="h-full w-full object-contain p-1" />
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-4">
        <AgentAvatar agent={draft} size={48} />
        <div className="grid flex-1 grid-cols-2 gap-3">
          <Field label="Color" hint="Name tag, selection ring, avatar">
            <input type="color" className="h-9 w-full cursor-pointer rounded-md border border-line-2 bg-bg" value={av.outfit ?? "#6d5dfc"} onChange={(e) => set("avatar", { ...av, outfit: e.target.value })} />
          </Field>
          <Field label="Accent">
            <input type="color" className="h-9 w-full cursor-pointer rounded-md border border-line-2 bg-bg" value={av.accent ?? "#ffffff"} onChange={(e) => set("avatar", { ...av, accent: e.target.value })} />
          </Field>
        </div>
      </div>
      <VoicePicker value={av.voice ?? ""} onChange={(v) => set("avatar", { ...av, voice: v || undefined })} />
      <Button
        size="sm"
        className="w-fit"
        onClick={() => {
          // Plays the voice as chosen here, not the one last saved.
          void import("@/lib/voice").then((v) => v.speak(`Hi, I'm ${draft.name}, ${draft.role}.`, draft.id, av.voice));
        }}
      >
        ▶ Preview voice
      </Button>
    </div>
  );
}

function PermissionsTab({ draft, set }: { draft: Agent; set: SetFn }) {
  const org = useOrg((s) => s.org);
  const orgPolicy = { create: true, assign: "anyone", edit: "any", require_review: true, ...(org?.settings.task_policy ?? {}) };
  const own = draft.permissions?.tasks ?? {};
  const upd = (patch: Partial<TaskPolicy> | null, key?: keyof TaskPolicy) => {
    const next: TaskPolicy = { ...own };
    if (patch === null && key) delete next[key];
    else Object.assign(next, patch);
    set("permissions", { ...(draft.permissions ?? {}), tasks: next });
  };
  const row = (key: keyof TaskPolicy, label: string, hint: string, options: { value: string; label: string }[]) => {
    const value = own[key];
    const inherited = orgPolicy[key];
    return (
      <Field label={label} hint={hint}>
        <Select
          value={value === undefined ? "" : String(value)}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") upd(null, key);
            else upd({ [key]: v === "true" ? true : v === "false" ? false : v } as Partial<TaskPolicy>);
          }}
        >
          <option value="">Org default ({options.find((o) => o.value === String(inherited))?.label ?? String(inherited)})</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </Field>
    );
  };
  return (
    <div className="grid gap-4">
      <p className="text-xs text-ink-3">
        Enforced by the runtime on every task action. When a rule blocks an action, the agent is told why and what to do instead.
      </p>
      {row("create", "Create tasks", "Can this agent put new work on the board?", [
        { value: "true", label: "Allowed" },
        { value: "false", label: "Not allowed" },
      ])}
      {row("assign", "Assign work to", "Whom this agent may delegate tasks to", [
        { value: "anyone", label: "Anyone" },
        { value: "reports", label: "Itself and people it manages" },
        { value: "self", label: "Only itself" },
      ])}
      {row("edit", "Change tasks", "Which tasks this agent may update", [
        { value: "any", label: "Any task" },
        { value: "involved", label: "Tasks it's involved in" },
        { value: "assigned", label: "Only tasks assigned to it" },
      ])}
      {row("require_review", "Review before done", "Assignees must send reviewed work to review instead of closing it", [
        { value: "true", label: "Required" },
        { value: "false", label: "Not required" },
      ])}
    </div>
  );
}
/** The agent's voice: one of the speech server's voices, or one of this browser's. */
function VoicePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [info, setInfo] = useState<{ voices: string[]; default: string; error?: string } | null>(null);
  const [local, setLocal] = useState<{ name: string; lang: string }[]>([]);
  useEffect(() => {
    api.voiceVoices().then(setInfo).catch(() => setInfo({ voices: [], default: "" }));
    void import("@/lib/voice").then((v) => v.browserVoices()).then((all) => setLocal(all.map((x) => ({ name: x.name, lang: x.lang }))));
  }, []);
  const listed = info?.voices ?? [];
  const known = [...listed, ...local.map((v) => `browser:${v.name}`)];
  if (!listed.length && !local.length) {
    return (
      <Field label="Voice" hint="Voice id passed to the speech model (leave empty for the default)">
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder="e.g. alba" />
      </Field>
    );
  }
  return (
    <Field
      label="Voice"
      hint={
        listed.length
          ? "Used whenever this agent speaks: chat replies, live talk and lessons"
          : "No speech server, so agents speak with this browser's voices (they differ between devices)"
      }
    >
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Default voice{info?.default ? ` (${info.default})` : ""}</option>
        {value && !known.includes(value) && <option value={value}>{value.replace(/^browser:/, "")} (not available here)</option>}
        {listed.length > 0 && (
          <optgroup label="Speech server">
            {listed.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </optgroup>
        )}
        {local.length > 0 && (
          <optgroup label="This browser">
            {local.map((v) => (
              <option key={v.name} value={`browser:${v.name}`}>
                {v.name} ({v.lang})
              </option>
            ))}
          </optgroup>
        )}
      </Select>
    </Field>
  );
}
