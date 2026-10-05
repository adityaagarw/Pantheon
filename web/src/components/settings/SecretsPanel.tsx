"use client";

/** An organization's secrets: credentials agents use by name, never seeing the value. */

import { useShallow } from "zustand/react/shallow";
import { useCallback, useEffect, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { Badge, Button, cx, ErrorNote, Field, Input, Modal, Toggle } from "@/components/ui";
import { api, type SecretInfo, type SecretUse } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useOrg } from "@/store/org";

export function SecretsPanel({ orgId }: { orgId: string }) {
  const [rows, setRows] = useState<SecretInfo[] | null>(null);
  const [usage, setUsage] = useState<SecretUse[]>([]);
  const [editing, setEditing] = useState<SecretInfo | "new" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const load = useCallback(() => {
    api.secrets(orgId).then(setRows).catch((e: Error) => setError(e.message));
    api.secretUsage(orgId).then(setUsage).catch(() => {});
  }, [orgId]);
  useEffect(load, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="grid gap-3">
      {rows === null ? null : rows.length === 0 ? (
        <div className="text-sm text-ink-3">No secrets yet. Add an API key or token, then choose which agents may use it and where it may be sent.</div>
      ) : (
        <div className="divide-y divide-line rounded-md border border-line">
          {rows.map((s) => (
            <div key={s.id} className={cx("flex flex-wrap items-start gap-3 p-3", !s.enabled && "opacity-60")}>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-sm font-semibold">{s.name}</code>
                  {!s.enabled && <Badge>disabled</Badge>}
                  {s.allowShell && <Badge tone="warn">shell: ${s.name}</Badge>}
                </div>
                {s.description && <div className="mt-0.5 text-xs text-ink-2">{s.description}</div>}
                <div className="mt-1 text-xs text-ink-3">
                  Agents: {s.agents.join(", ") || "none yet"} · Hosts: {s.domains.join(", ") || "none yet"}
                  {s.lastUsedAt && ` · last used ${timeAgo(s.lastUsedAt)}`}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Toggle checked={s.enabled} onChange={(v) => act(() => api.updateSecret(s.id, { enabled: v }))} />
                <Button size="sm" variant="ghost" onClick={() => setEditing(s)}>
                  Edit
                </Button>
                {confirmDelete === s.id ? (
                  <>
                    <Button size="sm" variant="danger" onClick={() => act(() => api.deleteSecret(s.id)).then(() => setConfirmDelete(null))}>
                      Delete {s.name}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(null)}>
                      Cancel
                    </Button>
                  </>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(s.id)}>
                    Delete…
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      <div>
        <Button size="sm" variant="primary" onClick={() => setEditing("new")}>
          + Add secret
        </Button>
      </div>
      <ErrorNote error={error} />
      {usage.length > 0 && (
        <details className="text-xs text-ink-3">
          <summary className="cursor-pointer select-none text-ink-2">Recent use ({usage.length})</summary>
          <div className="mt-2 grid gap-1">
            {usage.map((u, i) => (
              <div key={i}>
                {u.at ? timeAgo(u.at) : ""} · <b className="text-ink-2">{u.agent}</b> used <code>{u.secret}</code> in {u.tool}
                {u.target && ` → ${u.target}`}
              </div>
            ))}
          </div>
        </details>
      )}
      <SecretDialog orgId={orgId} secret={editing} onClose={() => setEditing(null)} onSaved={load} />
    </div>
  );
}

function SecretDialog({ orgId, secret, onClose, onSaved }: { orgId: string; secret: SecretInfo | "new" | null; onClose: () => void; onSaved: () => void }) {
  const agents = useOrg(useShallow((s) => Object.values(s.agents).filter((a) => !a.isSupervisor)));
  const isNew = secret === "new";
  const existing = secret && secret !== "new" ? secret : null;
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [description, setDescription] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [hosts, setHosts] = useState("");
  const [shell, setShell] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<typeof secret>(null);
  if (secret !== shown) {
    setShown(secret);
    setName(existing?.name ?? "");
    setValue("");
    setDescription(existing?.description ?? "");
    setMembers(existing?.agentIds ?? []);
    setHosts(existing?.domains.join(", ") ?? "");
    setShell(existing?.allowShell ?? false);
    setError(null);
  }
  const domains = hosts
    .split(/[\s,]+/)
    .map((h) => h.trim())
    .filter(Boolean);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = { description, agents: members, domains, allowShell: shell };
      if (isNew) await api.createSecret(orgId, { name, value, ...body });
      else if (existing) await api.updateSecret(existing.id, { ...body, ...(value ? { value } : {}) });
      onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={secret !== null}
      onClose={onClose}
      title={isNew ? "Add a secret" : `Edit ${existing?.name ?? ""}`}
      footer={
        <Button variant="primary" loading={busy} disabled={isNew && (!name.trim() || !value)} onClick={save}>
          {isNew ? "Add secret" : "Save"}
        </Button>
      }
    >
      <div className="grid gap-4">
        <Field label="Name" hint="UPPER_SNAKE_CASE; agents use it as {{secret:NAME}}">
          <Input value={name} disabled={!isNew} placeholder="GITHUB_TOKEN" onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
        </Field>
        <Field label={isNew ? "Value" : "Replace value"} hint={isNew ? "Encrypted; never shown again" : "Leave empty to keep the current value"}>
          <Input type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} placeholder={isNew ? "" : "••••••••"} />
        </Field>
        <Field label="Description" hint="What it's for; agents see this">
          <Input value={description} placeholder="Read-only GitHub token for the habit repo" onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div>
          <div className="mb-1.5 text-xs font-medium text-ink-2">Agents that may use it</div>
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
        <Field label="Hosts it may be sent to" hint="Comma separated, e.g. api.github.com, *.example.com">
          <Input value={hosts} placeholder="api.github.com" onChange={(e) => setHosts(e.target.value)} />
        </Field>
        <div>
          <Toggle checked={shell} onChange={setShell} label={`Also available in run_command as $${name || "NAME"}`} />
          <div className="mt-1 text-xs text-ink-3">
            Needed for git, CLIs and scripts. Code the agent runs can read its environment, so only enable this for agents you trust, and keep run_command on
            &quot;ask&quot; to approve each command. Output is still masked.
          </div>
        </div>
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}
