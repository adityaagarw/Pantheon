"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ActivityFeed } from "@/components/ActivityFeed";
import { MetaChat, MetaLayout, SYSTEM_ORG } from "@/components/MetaChat";
import { Button, ErrorNote, Field, Input, Select, Spinner, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { ArgusWatch, Org } from "@/lib/types";
import { useOrg, useOrgConnection } from "@/store/org";

const PROMPTS = [
  "Check on all my organizations: is everything proceeding as planned?",
  "Is anyone stuck, looping or failing right now?",
  "Keep an eye on my first organization every 30 minutes and alert me if work drifts from its goals.",
  "Compare what each team was asked to do with what they've actually delivered.",
];

function ArgusIcon({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <rect x="2" y="2" width="28" height="28" rx="8" fill="url(#ag)" />
      <path d="M6 16s3.8-6 10-6 10 6 10 6-3.8 6-10 6-10-6-10-6z" stroke="white" strokeWidth="2" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="3" fill="white" />
      <defs>
        <linearGradient id="ag" x1="2" y1="2" x2="30" y2="30">
          <stop stopColor="#4fb3e8" />
          <stop offset="1" stopColor="#2f5f9f" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export default function ArgusPage() {
  useOrgConnection(SYSTEM_ORG);
  const [argusId, setArgusId] = useState<string | null>(null);
  const [tab, setTab] = useState<"watches" | "activity">("watches");
  const [side, setSide] = useState(false);
  useEffect(() => {
    api.metaAgent("argus").then((s) => setArgusId(s.agent.id)).catch(() => {});
  }, []);
  return (
    <MetaLayout
      sideOpen={side}
      onCloseSide={() => setSide(false)}
      chat={
        argusId ? (
          <MetaChat
            agentId={argusId}
            fallbackName="Argus"
            icon={<ArgusIcon />}
            subtitle="The overseer. Checks on your organizations on demand, or on a schedule, and tells you what needs attention."
            intro="Ask Argus to check whether your organizations are working as planned, or to keep watch over one."
            prompts={PROMPTS}
            placeholder="Ask Argus to check on an organization…"
            sideLabel="Watches"
            onShowSide={() => setSide(true)}
          />
        ) : (
          <Spinner />
        )
      }
      side={
        <>
          <div className="border-b border-line p-3">
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                { value: "watches", label: "Watches" },
                { value: "activity", label: "What Argus is doing" },
              ]}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{tab === "watches" ? <Watches /> : <ActivityFeed />}</div>
        </>
      }
    />
  );
}

function Watches() {
  const [watches, setWatches] = useState<ArgusWatch[] | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [orgId, setOrgId] = useState("");
  const [every, setEvery] = useState(30);
  const [focus, setFocus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const feedLen = useOrg((s) => s.feed.length);
  const load = () => api.argusWatches().then(setWatches).catch(() => setWatches([]));
  useEffect(() => {
    void load();
    api.orgs().then(setOrgs).catch(() => {});
  }, [feedLen]);
  if (!watches) return <Spinner />;
  const add = async () => {
    setError(null);
    try {
      await api.setArgusWatch(orgId, { everyMinutes: every, focus });
      setFocus("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="space-y-5 p-4 text-sm">
      {watches.length === 0 ? (
        <div className="text-ink-3">Argus isn&apos;t watching anything. Ask it in the chat, or set a watch below — it will check on schedule and raise alerts in the org&apos;s inbox.</div>
      ) : (
        <div className="space-y-2">
          {watches.map((w) => (
            <div key={w.orgId} className="rounded-lg border border-line p-3">
              <div className="flex items-center gap-2">
                <Link href={`/org/${w.orgId}`} className="font-medium hover:underline">
                  {w.orgName}
                </Link>
                <span className="text-xs text-ink-3">every {w.everyMinutes} min</span>
                <Button size="sm" variant="ghost" className="ml-auto" onClick={() => api.removeArgusWatch(w.orgId).then(load)}>
                  Stop
                </Button>
              </div>
              {w.focus && <div className="mt-1 text-xs text-ink-2">{w.focus}</div>}
              <div className="mt-1 text-[11px] text-ink-3">last check {timeAgo(w.lastCheck)}</div>
            </div>
          ))}
        </div>
      )}
      <div className="rounded-lg border border-line p-3">
        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-ink-3">New watch</div>
        <div className="grid gap-3">
          <Field label="Organization">
            <Select value={orgId} onChange={(e) => setOrgId(e.target.value)}>
              <option value="">Choose…</option>
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Check every (minutes)">
            <Input type="number" min={5} value={every} onChange={(e) => setEvery(Number(e.target.value))} />
          </Field>
          <Field label="What to watch for" hint="Goals, deadlines, risks — in your words">
            <Textarea rows={2} value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="e.g. the launch checklist is done by Friday; nobody stays blocked for long" />
          </Field>
          <ErrorNote error={error} />
          <Button variant="primary" disabled={!orgId} onClick={add}>
            Watch
          </Button>
        </div>
      </div>
    </div>
  );
}
