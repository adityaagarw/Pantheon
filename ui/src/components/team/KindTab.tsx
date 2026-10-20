"use client";

/**
 * What runs an agent (engine app/agents/kinds.py): Pantheon's own loop, a command-line
 * agent (Claude Code, Codex, Hermes…), an agent behind an HTTP API, or a person.
 */

import { useEffect, useState } from "react";
import { Button, cx, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Account, Agent, AgentKind, KindCheck } from "@/lib/types";

type Runner = Record<string, unknown>;

export function KindTab({ draft, onChange, exclude = [] }: { draft: Agent; onChange: (kind: string, runner: Runner) => void; exclude?: string[] }) {
  const [kinds, setKinds] = useState<AgentKind[] | null>(null);
  const [check, setCheck] = useState<KindCheck | "running" | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  useEffect(() => {
    api
      .agentKinds()
      .then((ks) => setKinds(ks.filter((k) => !exclude.includes(k.id))))
      .catch(() => setKinds([]));
    api
      .accounts()
      .then((r) => setAccounts(r.accounts))
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const kindId = draft.kind ?? "llm";
  const runner = (draft.runner ?? {}) as Runner;
  const preset = String(runner.preset ?? "");
  // A check is about the settings it ran with: a different program, preset or account needs a new one.
  useEffect(() => setCheck(null), [kindId, preset, runner.command, runner.account]);
  if (!kinds) return <div className="text-sm text-ink-3">Loading…</div>;

  const kind = kinds.find((k) => k.id === kindId);
  const set = (patch: Runner) => onChange(kindId, { ...runner, ...patch });
  const models = check && check !== "running" ? (check.models ?? []) : [];
  const runCheck = () => {
    setCheck("running");
    api
      .checkKind(kindId, runner)
      .then(setCheck)
      .catch((e) => setCheck({ ok: false, checks: [{ label: "Check", ok: false, detail: String(e) }] }));
  };

  return (
    <div className="grid gap-5">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {kinds.map((k) => (
          <button
            key={k.id}
            onClick={() => onChange(k.id, k.presets[0]?.runner ?? {})}
            className={cx("rounded-lg border p-3 text-left transition-colors cursor-pointer", kindId === k.id ? "border-accent bg-accent/10" : "border-line hover:border-line-2")}
          >
            <div className="text-sm font-medium">{k.label}</div>
            <div className="mt-1 text-xs text-ink-3">{k.description}</div>
          </button>
        ))}
        {!kind && <div className="text-sm text-bad">This agent&apos;s kind ({kindId}) isn&apos;t available: is its pack switched off?</div>}
      </div>

      {kind && kind.presets.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">{kind.label}</div>
          <div className="flex flex-wrap gap-2">
            {kind.presets.map((p) => (
              <button
                key={p.id}
                title={p.description}
                onClick={() => onChange(kind.id, { ...p.runner })}
                className={cx("rounded-full border px-3 py-1 text-xs", preset === p.id ? "border-accent bg-accent/15 text-ink" : "border-line text-ink-2 hover:text-ink")}
              >
                {p.label}
              </button>
            ))}
          </div>
          {kind.presets.find((p) => p.id === preset) && <div className="mt-2 text-xs text-ink-3">{kind.presets.find((p) => p.id === preset)!.description}</div>}
        </div>
      )}

      {kind &&
        kind.fields
          .filter((f) => !f.presets || f.presets.includes(preset))
          .map((f) => (
            <Field key={f.key} label={f.label} hint={f.hint}>
              {f.type === "select" ? (
                <Select value={String(runner[f.key] ?? f.options?.[0]?.[0] ?? "")} onChange={(e) => set({ [f.key]: e.target.value })}>
                  {f.options?.map(([v, label]) => (
                    <option key={v} value={v}>
                      {label}
                    </option>
                  ))}
                </Select>
              ) : f.type === "account" ? (
                <div className="flex items-center gap-2">
                  <Select value={String(runner[f.key] ?? "")} onChange={(e) => set({ [f.key]: e.target.value })}>
                    <option value="">{f.key === "account" ? "This computer's sign-in" : "Nothing: wait for the reset"}</option>
                    {accounts
                      .filter((a) => a.tool === preset && (f.key === "account" || a.id !== runner.account))
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name} ({a.methodLabel}
                          {a.status.signedIn === false ? ", not signed in" : ""})
                        </option>
                      ))}
                  </Select>
                  {f.key === "account" && (
                    <a href="/settings?tab=accounts" className="shrink-0 text-xs text-accent hover:underline">
                      Manage
                    </a>
                  )}
                </div>
              ) : f.type === "model" ? (
                <>
                  <Input
                    value={String(runner[f.key] ?? "")}
                    onChange={(e) => set({ [f.key]: e.target.value })}
                    list={`models-${draft.id}`}
                    placeholder={models.length ? "Pick one, or leave empty for its default" : "Its default"}
                  />
                  <datalist id={`models-${draft.id}`}>
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </datalist>
                </>
              ) : f.type === "list" ? (
                <Input
                  value={((runner[f.key] as string[] | undefined) ?? []).join(" ")}
                  onChange={(e) => set({ [f.key]: e.target.value.split(" ").filter(Boolean) })}
                  placeholder="--flag {prompt}"
                />
              ) : (
                <Input
                  value={String(runner[f.key] ?? "")}
                  onChange={(e) => set({ [f.key]: f.type === "secret" ? e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") : e.target.value })}
                  placeholder={f.type === "secret" ? "XAI_API_KEY" : ""}
                />
              )}
            </Field>
          ))}

      {kind?.checkable && (
        <div className="grid gap-2">
          <div className="flex items-center gap-3">
            <Button variant="secondary" onClick={runCheck} disabled={check === "running"}>
              {check === "running" ? "Checking…" : "Check"}
            </Button>
            <span className="text-xs text-ink-3">Is it installed, signed in and answering? Which models can it use?</span>
          </div>
          {check && check !== "running" && (
            <div className="grid gap-1.5 rounded-lg border border-line p-3">
              {check.checks.map((c, i) => (
                <div key={i} className="flex gap-2 text-xs">
                  <span className={c.ok ? "text-ok" : "text-bad"}>{c.ok ? "✓" : "✗"}</span>
                  <span className="w-40 shrink-0 font-medium text-ink-2">{c.label}</span>
                  <span className="min-w-0 break-words text-ink-3">{c.detail}</span>
                </div>
              ))}
              {models.length > 0 && <div className="text-xs text-ink-3">{models.length} models offered: pick one in the Model box.</div>}
              {check.note && <div className="text-xs text-ink-3">{check.note}</div>}
            </div>
          )}
        </div>
      )}

      <div className="rounded-lg border border-line bg-panel-2/40 p-3 text-xs leading-relaxed text-ink-3">
        {kindId === "llm" && "Pantheon runs this agent itself, with the model on the Model tab and the tools on the Tools tab."}
        {kindId === "human" &&
          "A person: what's sent to them waits in your Inbox, and you answer as them from their Console. They can lead, review, be asked anything, and do flow steps: human in the loop."}
        {kindId === "cli" &&
          "Runs in this agent's own folder (its git worktree when the organization uses them), as a full member: Pantheon's tools (messages, tasks, flow steps, git) reach it through MCP; its tool calls, thinking and cost show live in the office and Sessions; what it asks permission for waits in your Inbox; you can steer it while it works; it remembers the conversation from turn to turn (Clear context starts it afresh); and when its plan's usage limit is reached, its work waits for the reset instead of failing. The program must be installed and signed in on this computer (or, for Claude Code and Codex, given an account of its own): Check tells you."}
        {kindId === "remote" &&
          "Pantheon sends it its messages over HTTP and delivers its replies. Its API key is an organization secret (Settings → Secrets) granted to this agent; it's only ever sent to the endpoint's host."}
      </div>
    </div>
  );
}
