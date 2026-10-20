"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DeleteOrgDialog } from "@/components/DeleteOrgDialog";
import { OrgWizard } from "@/components/OrgWizard";
import { Badge, Button, Card, Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Org, Provider } from "@/lib/types";

export default function Home() {
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Org | null>(null);

  const load = () => api.orgs().then(setOrgs).catch(() => setOrgs([]));
  useEffect(() => {
    load();
    api.providers().then(setProviders).catch(() => {});
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const onlyMock = providers.length > 0 && providers.every((p) => p.type === "mock");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Organizations</h1>
            <p className="mt-1 text-sm text-ink-2">Teams of AI agents working for you — every message, decision and tool call visible.</p>
          </div>
          <div className="flex gap-2">
            <Link href="/supervisor">
              <Button variant="secondary">✦ Design with Zeus</Button>
            </Link>
            <Button variant="primary" onClick={() => setCreating(true)}>
              New organization
            </Button>
          </div>
        </div>

        {onlyMock && (
          <div className="mt-6 flex items-center justify-between rounded-lg border border-warn/30 bg-warn/10 px-4 py-3 text-sm">
            <span className="text-warn">Only the offline mock model is configured — agents will give canned answers.</span>
            <Link href="/settings" className="font-medium text-warn underline">
              Add an LLM provider
            </Link>
          </div>
        )}

        {orgs === null ? null : orgs.length === 0 ? (
          <Card className="mt-8">
            <Empty title="No organizations yet" icon="🏛">
              Start from a template, build one by hand, or describe what you need to Zeus, the Pantheon supervisor, and it will design it for you.
            </Empty>
          </Card>
        ) : (
          <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {orgs.map((o) => (
              <Link key={o.id} href={`/org/${o.id}`}>
                <Card className="group h-full p-5 transition-colors hover:border-line-2 hover:bg-panel-2">
                  <div className="flex items-center justify-between">
                    <div className="min-w-0 truncate font-semibold">{o.name}</div>
                    <button
                      title={`Delete ${o.name}`}
                      aria-label={`Delete ${o.name}`}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setDeleting(o);
                      }}
                      className="ml-auto mr-2 rounded p-1 text-ink-3 opacity-100 transition-opacity hover:bg-bad/15 hover:text-bad md:opacity-0 md:group-hover:opacity-100 cursor-pointer"
                    >
                      <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
                      </svg>
                    </button>
                    {o.status === "paused" ? (
                      <Badge tone="neutral">Paused</Badge>
                    ) : (o.workingAgents ?? 0) > 0 ? (
                      <Badge tone="accent">{o.workingAgents} working</Badge>
                    ) : (
                      <Badge tone="ok">Idle</Badge>
                    )}
                  </div>
                  <p className="mt-2 line-clamp-2 min-h-10 text-sm text-ink-2">{o.description || "No description"}</p>
                  <div className="mt-4 flex gap-4 text-xs text-ink-3">
                    <span>{o.agentCount} agents</span>
                    <span>{o.openTasks} open tasks</span>
                    <span className="ml-auto">{timeAgo(o.createdAt)}</span>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>
      <OrgWizard open={creating} onClose={() => setCreating(false)} />
      {deleting && <DeleteOrgDialog org={deleting} open onClose={() => setDeleting(null)} onDeleted={load} />}
    </div>
  );
}
