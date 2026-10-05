"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { notifyOrgsChanged } from "@/components/AppShell";
import { ActivitiesPanel } from "@/components/ActivitiesPanel";
import { AgentFiles } from "@/components/Files";
import { DeleteOrgDialog } from "@/components/DeleteOrgDialog";
import { McpServersPanel } from "@/components/settings/McpServersPanel";
import { SecretsPanel } from "@/components/settings/SecretsPanel";
import { Button, Card, ErrorNote, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
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
            <Field label="Workspace folder" hint="Host folder agents read/write and run commands in">
              <Input value={draft.workspace ?? ""} onChange={(e) => setDraft({ ...draft, workspace: e.target.value || null })} placeholder="(a fresh folder under the workspace root)" />
            </Field>
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 text-sm font-semibold">How agents work together</div>
          <div className="grid gap-4 md:grid-cols-2">
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
          </div>
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
          <div className="mb-3 text-sm font-semibold">Export</div>
          <p className="mb-3 text-sm text-ink-2">Download this organization&apos;s definition (agents, personas, tools, structure, channels — no secrets or history). Import it later through Zeus or the API.</p>
          <Button onClick={exportJson}>Download JSON</Button>
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
