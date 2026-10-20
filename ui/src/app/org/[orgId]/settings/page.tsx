"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { notifyOrgsChanged } from "@/components/AppShell";
import { ActivitiesPanel } from "@/components/ActivitiesPanel";
import { AgentFiles } from "@/components/Files";
import { DeleteOrgDialog } from "@/components/DeleteOrgDialog";
import { LibraryPicker } from "@/components/LibraryPicker";
import { McpServersPanel } from "@/components/settings/McpServersPanel";
import { SecretsPanel } from "@/components/settings/SecretsPanel";
import { Button, Card, ErrorNote, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { inDesktopApp } from "@/lib/engine";
import { pickFolder } from "@/lib/folders";
import type { Org, Provider } from "@/lib/types";
import { useOrg } from "@/store/org";

export default function OrgSettingsPage() {
  const org = useOrg((s) => s.org);
  const router = useRouter();
  const [draft, setDraft] = useState<Org | null>(org);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => setDraft(org), [org?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api.providers().then(setProviders).catch(() => {});
  }, []);
  if (!org || !draft) return null;

  const save = async () => {
    setError(null);
    try {
      const o = await api.updateOrg(org.id, {
        name: draft.name,
        description: draft.description,
        workspace: draft.workspace ?? "",
        settings: draft.settings,
      });
      useOrg.getState().setOrg(o);
      notifyOrgsChanged();
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const s = draft.settings;
  const setS = (patch: Partial<Org["settings"]>) => setDraft({ ...draft, settings: { ...s, ...patch } });
  const dm = s.default_model ?? {};
  const provider = providers.find((p) => p.id === dm.provider_id);
  const cm = s.checker_model ?? {};
  const checkerProvider = providers.find((p) => p.id === cm.provider_id);
  // An empty binding clears the setting: the check runs on the agent's own model.
  const setCm = (b: typeof cm) => setS({ checker_model: b.provider_id || b.model ? b : {} });

  const exportJson = async () => {
    const def = await api.exportOrg(org.id);
    const blob = new Blob([JSON.stringify(def, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${org.name.replace(/\W+/g, "-").toLowerCase()}.pantheon.json`;
    a.click();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">Organization</div>
          <div className="grid gap-4">
            <Field label="Name">
              <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            <Field label="Description" hint="Given to every agent as shared context">
              <Textarea rows={4} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </Field>
            <Field label="Workspace folder" hint="The folder on this computer where agents read, write and run commands">
              <div className="flex gap-2">
                <Input value={draft.workspace ?? ""} onChange={(e) => setDraft({ ...draft, workspace: e.target.value || null })} placeholder="(a fresh folder under the workspace root)" />
                {inDesktopApp() && (
                  <Button onClick={() => void pickFolder("The organization's workspace").then((p) => p && setDraft({ ...draft, workspace: p }))}>Choose…</Button>
                )}
              </div>
            </Field>
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">How agents work together</div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="How it works" hint="A flow org works only through its flows (the Flows pane)">
              <Select value={s.mode ?? "team"} onChange={(e) => setS({ mode: e.target.value as "team" | "flow" })}>
                <option value="team">A team: agents plan, talk and delegate</option>
                <option value="flow">A flow: work runs as a graph of steps</option>
              </Select>
            </Field>
            <Field label="Communication">
              <Select value={s.comm_policy} onChange={(e) => setS({ comm_policy: e.target.value as Org["settings"]["comm_policy"] })}>
                <option value="open">Open — anyone can message anyone</option>
                <option value="structured">Structured — only related agents / shared channels</option>
              </Select>
            </Field>
            <Field label="Max message chain depth" hint="Stops runaway agent loops">
              <Input type="number" value={s.max_chain_depth} onChange={(e) => setS({ max_chain_depth: Number(e.target.value) })} />
            </Field>
            <Field label="Daily token budget" hint="0 = unlimited; the org pauses when reached">
              <Input type="number" value={s.daily_token_budget} onChange={(e) => setS({ daily_token_budget: Number(e.target.value) })} />
            </Field>
            <Field label="Agents working at once" hint="In this organization; 0 = only the global limit (Settings > Agents)">
              <Input type="number" min={0} max={64} value={s.max_concurrent ?? 0} onChange={(e) => setS({ max_concurrent: Math.max(0, Math.min(64, Math.round(Number(e.target.value) || 0))) })} />
            </Field>
            <Field label="Writing style" hint="For every agent's messages and documents (agents can also turn STE on one by one)">
              <Select value={s.writing ?? "plain"} onChange={(e) => setS({ writing: e.target.value as "plain" | "ste" })}>
                <option value="plain">Plain (each agent&apos;s own)</option>
                <option value="ste">ASD-STE100 Simplified Technical English</option>
              </Select>
            </Field>
            <Field label="Shell commands" hint="run_command and computer_shell, for every agent">
              <Select value={s.shell_approval ?? "ask"} onChange={(e) => setS({ shell_approval: e.target.value as "ask" | "auto" })}>
                <option value="ask">Ask me before each command</option>
                <option value="auto">Run without asking</option>
              </Select>
            </Field>
          </div>
          {s.shell_approval === "auto" && (
            <div className="mt-3 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">
              Agents can run any command in their workspace without your approval. Agents whose shell tools are set to &quot;deny&quot; on the Team tab still
              can&apos;t.
            </div>
          )}
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Task permissions (org default)</div>
          <div className="mb-4 text-xs text-ink-3">Individual agents can override these on their Permissions tab.</div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Create tasks">
              <Select value={String(s.task_policy?.create ?? true)} onChange={(e) => setS({ task_policy: { ...s.task_policy, create: e.target.value === "true" } })}>
                <option value="true">Everyone may create tasks</option>
                <option value="false">Only agents granted it individually</option>
              </Select>
            </Field>
            <Field label="Assign work to">
              <Select value={s.task_policy?.assign ?? "anyone"} onChange={(e) => setS({ task_policy: { ...s.task_policy, assign: e.target.value as "anyone" } })}>
                <option value="anyone">Anyone</option>
                <option value="reports">Themselves and people they manage</option>
                <option value="self">Only themselves</option>
              </Select>
            </Field>
            <Field label="Change tasks">
              <Select value={s.task_policy?.edit ?? "any"} onChange={(e) => setS({ task_policy: { ...s.task_policy, edit: e.target.value as "any" } })}>
                <option value="any">Any task</option>
                <option value="involved">Tasks they&apos;re involved in</option>
                <option value="assigned">Only tasks assigned to them</option>
              </Select>
            </Field>
            <Field label="Review before done">
              <Select value={String(s.task_policy?.require_review ?? true)} onChange={(e) => setS({ task_policy: { ...s.task_policy, require_review: e.target.value === "true" } })}>
                <option value="true">Required when a task has a reviewer</option>
                <option value="false">Assignees may close their own tasks</option>
              </Select>
            </Field>
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">Meetings</div>
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Max participants">
              <Input type="number" min={2} max={20} value={s.meetings?.max_participants ?? 8} onChange={(e) => setS({ meetings: { ...s.meetings, max_participants: Number(e.target.value) } })} />
            </Field>
            <Field label="Max rounds">
              <Input type="number" min={1} max={6} value={s.meetings?.max_rounds ?? 3} onChange={(e) => setS({ meetings: { ...s.meetings, max_rounds: Number(e.target.value) } })} />
            </Field>
            <Field label="Default rounds">
              <Input type="number" min={1} max={6} value={s.meetings?.default_rounds ?? 2} onChange={(e) => setS({ meetings: { ...s.meetings, default_rounds: Number(e.target.value) } })} />
            </Field>
            <Field label="Default style">
              <Select value={s.meetings?.default_style ?? "discussion"} onChange={(e) => setS({ meetings: { ...s.meetings, default_style: e.target.value } })}>
                {["discussion", "decision", "brainstorm", "standup", "review"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </Select>
            </Field>
            <Field label="Action items">
              <Select value={String(s.meetings?.create_tasks ?? false)} onChange={(e) => setS({ meetings: { ...s.meetings, create_tasks: e.target.value === "true" } })}>
                <option value="false">Stay in the minutes</option>
                <option value="true">Become assigned tasks</option>
              </Select>
            </Field>
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Physical space</div>
          <div className="mb-4 text-xs text-ink-3">
            Agents have bodies in the office (or scene): they know where they are, can walk around, talk out loud to whoever is in the room, and pick up, hand over and use
            objects. Ask them &ldquo;come to the lounge&rdquo; or &ldquo;what&apos;s in the kitchen?&rdquo;.
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Bodies & presence tools">
              <Select value={String(s.world?.enabled ?? true)} onChange={(e) => setS({ world: { ...s.world, enabled: e.target.value === "true" } })}>
                <option value="true">On — agents are aware of the space</option>
                <option value="false">Off — agents only work (fewer tokens)</option>
              </Select>
            </Field>
            <Field label="Witnessing" hint="Wakes agents when something happens near them">
              <Select value={String(s.world?.witness ?? false)} onChange={(e) => setS({ world: { ...s.world, witness: e.target.value === "true" } })}>
                <option value="false">Only speech and hand-overs reach others</option>
                <option value="true">People notice movements and actions in their room</option>
              </Select>
            </Field>
          </div>
          <Field label="World rules" hint="The scene's premise and rules, added to every agent's prompt (for simulations)">
            <Textarea
              rows={3}
              value={s.world?.rules ?? ""}
              onChange={(e) => setS({ world: { ...s.world, rules: e.target.value } })}
              placeholder="e.g. It's a Saturday market in a small town. Officers patrol; civilians go about their day."
            />
          </Field>
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Stage libraries</div>
          <div className="mb-4 text-xs text-ink-3">
            What agents&apos; Stage pages may import. Agents read the docs only for these; one agent can be given its own set on the Team tab.
          </div>
          <LibraryPicker value={s.libraries} onChange={(libraries) => setS({ libraries })} />
        </Card>

        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">Default model for this org</div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Provider">
              <Select value={dm.provider_id ?? ""} onChange={(e) => setS({ default_model: { ...dm, provider_id: e.target.value || undefined, model: undefined } })}>
                <option value="">Global default</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Model">
              <Input list="org-models" value={dm.model ?? ""} onChange={(e) => setS({ default_model: { ...dm, model: e.target.value || undefined } })} placeholder={provider?.defaultModel ?? "provider default"} />
              <datalist id="org-models">
                {(provider?.models ?? []).map((m) => (
                  <option key={m.id} value={m.id} />
                ))}
              </datalist>
            </Field>
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Acceptance check model</div>
          <div className="mb-4 text-xs text-ink-3">
            The check of finished work runs on this model. A model unlike the builders&apos; finds what they missed; leave it empty to use the model of whoever hands the work in.
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Provider">
              <Select value={cm.provider_id ?? ""} onChange={(e) => setCm({ ...cm, provider_id: e.target.value || undefined, model: undefined })}>
                <option value="">Same as the builder</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Model">
              <Input list="checker-models" value={cm.model ?? ""} disabled={!cm.provider_id} onChange={(e) => setCm({ ...cm, model: e.target.value || undefined })} placeholder={checkerProvider?.defaultModel ?? "provider default"} />
              <datalist id="checker-models">
                {(checkerProvider?.models ?? []).map((m) => (
                  <option key={m.id} value={m.id} />
                ))}
              </datalist>
            </Field>
          </div>
        </Card>

        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={save}>
            Save settings
          </Button>
          {saved && <span className="text-sm text-ok">Saved</span>}
          <ErrorNote error={error} />
        </div>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Shared files</div>
          <div className="mb-4 text-xs text-ink-3">Documents and images every agent in this organization can read. To give something to just one agent, use its Files tab on Team or drop it in your chat with them.</div>
          <AgentFiles orgId={org.id} agentId={null} />
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Activities</div>
          <div className="mb-4 text-xs text-ink-3">Recurring happenings: participants are gathered in a room (optional) and told what&apos;s going on.</div>
          <ActivitiesPanel orgId={org.id} />
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">Secrets</div>
          <div className="mb-4 text-xs text-ink-3">
            API keys and tokens agents use by name ({"{{secret:NAME}}"}) without ever seeing them. Values are encrypted and masked out of every transcript, record
            and log. Never paste a credential into a chat.
          </div>
          <SecretsPanel orgId={org.id} />
        </Card>

        <Card className="p-5">
          <div className="mb-1 text-sm font-semibold">MCP servers for this organization</div>
          <div className="mb-4 text-xs text-ink-3">Global servers (Settings) are available too. Grant them to agents on the Team tab.</div>
          <McpServersPanel orgId={org.id} />
        </Card>

        <Card className="p-5">
          <div className="mb-3 text-sm font-semibold">Save as a template</div>
          <p className="mb-3 text-sm text-ink-2">
            Keep this organization as one of your templates (its agents, personas, tools, structure, flows and office; not its work, secrets or history), to make
            more like it from New organization. Download it to share it: anyone can import the file there.
          </p>
          <SaveTemplate orgId={org.id} defaultName={org.name} />
          <div className="mt-3">
            <Button variant="ghost" onClick={exportJson}>
              Download as a file
            </Button>
          </div>
        </Card>

        <Card className="border-bad/30 p-5">
          <div className="mb-3 text-sm font-semibold text-bad">Delete organization</div>
          <p className="mb-3 text-sm text-ink-2">Permanently deletes agents, conversations, tasks and history. Workspace files on disk are not touched.</p>
          <Button variant="danger" onClick={() => setDeleting(true)}>
            Delete {org.name}…
          </Button>
        </Card>
        <DeleteOrgDialog org={org} open={deleting} onClose={() => setDeleting(false)} onDeleted={() => router.push("/")} />
      </div>
    </div>
  );
}

function SaveTemplate({ orgId, defaultName }: { orgId: string; defaultName: string }) {
  const [name, setName] = useState(defaultName);
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);
  const save = () =>
    api.saveAsTemplate(orgId, name.trim()).then(
      (t) => setState({ ok: true, text: `Saved as "${t.name}": it's under Your templates when you make a new organization.` }),
      (e: Error) => setState({ ok: false, text: e.message }),
    );
  return (
    <div className="grid gap-2">
      <div className="flex gap-2">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Template name" className="flex-1" />
        <Button variant="primary" onClick={save} disabled={!name.trim()}>
          Save as template
        </Button>
      </div>
      {state && <div className={state.ok ? "text-sm text-ok" : "text-sm text-bad"}>{state.text}</div>}
    </div>
  );
}
