"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, ErrorNote, Field, Input, Modal, Select, Textarea, Toggle } from "@/components/ui";
import { api } from "@/lib/api";
import type { McpServer } from "@/lib/types";

const EXAMPLES = [
  { label: "Filesystem (npx)", name: "fs", transport: "stdio", command: "npx", args: "-y @modelcontextprotocol/server-filesystem /workspaces" },
  { label: "GitHub (npx)", name: "github", transport: "stdio", command: "npx", args: "-y @modelcontextprotocol/server-github", env: "GITHUB_PERSONAL_ACCESS_TOKEN=" },
  { label: "Fetch (uvx)", name: "fetch", transport: "stdio", command: "uvx", args: "mcp-server-fetch" },
  { label: "Remote HTTP server", name: "remote", transport: "http", url: "https://example.com/mcp" },
];

export function McpServersPanel({ orgId }: { orgId?: string }) {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [editing, setEditing] = useState<McpServer | "new" | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, string>>({});
  const load = useCallback(() => api.mcpServers(orgId).then((s) => setServers(orgId ? s.filter((x) => x.orgId === orgId) : s.filter((x) => !x.orgId))).catch(() => {}), [orgId]);
  useEffect(() => {
    void load();
  }, [load]);

  const test = async (s: McpServer) => {
    setTesting(s.id);
    try {
      const r = await api.testMcpServer(s.id);
      setResults((m) => ({ ...m, [s.id]: r.ok ? `✓ ${r.tools?.length} tools: ${r.tools?.map((t) => t.name).join(", ")}` : `✗ ${r.error}` }));
    } finally {
      setTesting(null);
      void load();
    }
  };

  return (
    <div className="space-y-3">
      {servers.length === 0 && <div className="text-sm text-ink-3">No MCP servers yet.</div>}
      {servers.map((s) => (
        <div key={s.id} className="rounded-lg border border-line p-3">
          <div className="flex items-center gap-2">
            <code className="font-mono text-sm font-semibold">{s.name}</code>
            <Badge>{s.transport}</Badge>
            <Badge tone={s.status === "ready" ? "ok" : s.status === "error" ? "bad" : "neutral"}>{s.status}</Badge>
            {!s.enabled && <Badge tone="warn">disabled</Badge>}
            <div className="ml-auto flex gap-1.5">
              <Button size="sm" onClick={() => test(s)} loading={testing === s.id}>
                Test
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(s)}>
                Edit
              </Button>
            </div>
          </div>
          <div className="mt-1.5 truncate font-mono text-xs text-ink-3">{s.transport === "stdio" ? `${s.command} ${s.args.join(" ")}` : s.url}</div>
          {(results[s.id] || s.error) && <div className={`mt-1.5 text-xs ${results[s.id]?.startsWith("✓") ? "text-ok" : "text-bad"}`}>{results[s.id] ?? s.error}</div>}
        </div>
      ))}
      <Button onClick={() => setEditing("new")}>+ Add MCP server</Button>
      {editing && (
        <McpEditor
          orgId={orgId}
          server={editing === "new" ? null : editing}
          onClose={() => {
            setEditing(null);
            void load();
          }}
        />
      )}
    </div>
  );
}

function McpEditor({ orgId, server, onClose }: { orgId?: string; server: McpServer | null; onClose: () => void }) {
  const [form, setForm] = useState({
    name: server?.name ?? "",
    description: server?.description ?? "",
    transport: server?.transport ?? "stdio",
    command: server?.command ?? "",
    args: (server?.args ?? []).join(" "),
    cwd: server?.cwd ?? "",
    url: server?.url ?? "",
    env: "",
    headers: "",
    enabled: server?.enabled ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const parseKv = (s: string) =>
    Object.fromEntries(
      s
        .split("\n")
        .map((l) => l.trim())
        .filter((l) => l.includes("="))
        .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]),
    );
  const splitArgs = (s: string) => (s.match(/"[^"]*"|\S+/g) ?? []).map((a) => a.replace(/^"|"$/g, ""));

  const save = async () => {
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {
      description: form.description,
      command: form.command || null,
      args: splitArgs(form.args),
      cwd: form.cwd || null,
      url: form.url || null,
      enabled: form.enabled,
    };
    if (form.env.trim()) body.env = parseKv(form.env);
    if (form.headers.trim()) body.headers = parseKv(form.headers);
    try {
      if (server) await api.updateMcpServer(server.id, body);
      else await api.createMcpServer({ ...body, name: form.name, transport: form.transport, orgId: orgId ?? null });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={server ? `Edit ${server.name}` : "Add MCP server"}
      footer={
        <>
          {server && (
            <Button variant="danger" onClick={() => api.deleteMcpServer(server.id).then(onClose)}>
              Delete
            </Button>
          )}
          <Button variant="primary" onClick={save} loading={busy}>
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {!server && (
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((ex) => (
              <button
                key={ex.label}
                className="rounded-full border border-line-2 px-2.5 py-1 text-xs text-ink-2 hover:text-ink cursor-pointer"
                onClick={() =>
                  setForm({ ...form, name: ex.name, transport: ex.transport as "stdio" | "http", command: ex.command ?? "", args: ex.args ?? "", url: ex.url ?? "", env: ex.env ?? "" })
                }
              >
                {ex.label}
              </button>
            ))}
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name" hint="lowercase slug">
            <Input disabled={!!server} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Transport">
            <Select disabled={!!server} value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value as "stdio" | "http" })}>
              <option value="stdio">stdio (local process)</option>
              <option value="http">Streamable HTTP</option>
            </Select>
          </Field>
        </div>
        <Field label="Description">
          <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        {form.transport === "stdio" ? (
          <>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Command">
                <Input value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} placeholder="npx" />
              </Field>
              <div className="col-span-2">
                <Field label="Arguments">
                  <Input value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })} />
                </Field>
              </div>
            </div>
            <Field label="Working directory">
              <Input value={form.cwd} onChange={(e) => setForm({ ...form, cwd: e.target.value })} />
            </Field>
            <Field label="Environment" hint={server?.envKeys.length ? `set: ${server.envKeys.join(", ")} (values hidden; re-enter to change)` : "KEY=value per line, stored encrypted"}>
              <Textarea rows={3} className="font-mono text-xs" value={form.env} onChange={(e) => setForm({ ...form, env: e.target.value })} />
            </Field>
          </>
        ) : (
          <>
            <Field label="URL">
              <Input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
            </Field>
            <Field label="Headers" hint={server?.headerKeys.length ? `set: ${server.headerKeys.join(", ")}` : "Header=value per line, stored encrypted"}>
              <Textarea rows={3} className="font-mono text-xs" value={form.headers} onChange={(e) => setForm({ ...form, headers: e.target.value })} placeholder="Authorization=Bearer …" />
            </Field>
          </>
        )}
        <Toggle checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} label="Enabled" />
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}
