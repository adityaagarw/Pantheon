"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ActivityFeed } from "@/components/ActivityFeed";
import { Logo } from "@/components/AppShell";
import { AssetsPanel } from "@/components/AssetsPanel";
import { CustomToolsPanel } from "@/components/CustomToolsPanel";
import { MetaChat, MetaLayout, SYSTEM_ORG } from "@/components/MetaChat";
import { Badge, Button, cx, Select, Spinner, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { FeatureRequest, Org } from "@/lib/types";
import { useOrg, useOrgConnection } from "@/store/org";

const PROMPTS = [
  "Design an organization for me: a small team that researches competitors and writes a weekly brief.",
  "Build a small town square scene for a social simulation, with a café, a police station and a few residents.",
  "Triage the open feature requests.",
  "Find a free 3D model of a vending machine online and put it in the kitchen of my first organization.",
  "Hold a standup in the boardroom every morning at 9, and give my agents a way to hand each other coffee.",
];

export default function SupervisorPage() {
  useOrgConnection(SYSTEM_ORG);
  const [supId, setSupId] = useState<string | null>(null);
  const [tab, setTab] = useState<"requests" | "tools" | "assets" | "activity">("requests");
  const [side, setSide] = useState(false);
  useEffect(() => {
    api.metaAgent("zeus").then((s) => setSupId(s.agent.id)).catch(() => {});
  }, []);
  return (
    <MetaLayout
      sideOpen={side}
      onCloseSide={() => setSide(false)}
      chat={
        supId ? (
          <MetaChat
            agentId={supId}
            fallbackName="Zeus"
            icon={<Logo size={30} />}
            subtitle="The Pantheon supervisor. Designs your organizations and spaces, brings in 3D assets, tunes agents, and handles their requests."
            intro="Tell Zeus what you want your agents to do. It can build whole organizations, and the spaces they live in, for you."
            prompts={PROMPTS}
            placeholder="Describe the organization you need, or ask for changes…"
            sideLabel="Requests"
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
                { value: "requests", label: "Requests" },
                { value: "tools", label: "Tools" },
                { value: "assets", label: "Assets & plugins" },
                { value: "activity", label: "Activity" },
              ]}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{tab === "requests" ? <FeatureRequests /> : tab === "tools" ? <CustomToolsPanel /> : tab === "assets" ? <AssetsPanel /> : <ActivityFeed />}</div>
        </>
      }
    />
  );
}

const FR_TONE = { open: "info", triaged: "neutral", accepted: "accent", in_progress: "accent", done: "ok", rejected: "bad" } as const;

function FeatureRequests() {
  const [items, setItems] = useState<FeatureRequest[] | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const feedLen = useOrg((s) => s.feed.length);
  useEffect(() => {
    api.featureRequests().then(setItems).catch(() => setItems([]));
    api.orgs().then(setOrgs).catch(() => {});
  }, [feedLen]);
  if (!items) return <Spinner />;
  if (!items.length)
    return <div className="p-6 text-center text-sm text-ink-3">No requests yet. When an agent needs a tool, access or colleague it doesn&apos;t have, it files a request here.</div>;
  const orgName = (id: string | null) => orgs.find((o) => o.id === id)?.name ?? "—";
  return (
    <div className="divide-y divide-line">
      {items.map((fr) => (
        <div key={fr.id} className="p-4">
          <button className="w-full text-left cursor-pointer" onClick={() => setOpen(open === fr.id ? null : fr.id)}>
            <div className="flex items-center gap-2">
              <Badge tone={FR_TONE[fr.status]}>{fr.status.replace("_", " ")}</Badge>
              <span className="text-[11px] text-ink-3">
                {orgName(fr.orgId)} · {timeAgo(fr.createdAt)}
              </span>
            </div>
            <div className="mt-1.5 text-sm font-medium">{fr.title}</div>
          </button>
          {open === fr.id && <FrDetail fr={fr} onChange={(n) => setItems((all) => all!.map((x) => (x.id === n.id ? n : x)))} />}
        </div>
      ))}
    </div>
  );
}

function FrDetail({ fr, onChange }: { fr: FeatureRequest; onChange: (fr: FeatureRequest) => void }) {
  const [resolution, setResolution] = useState(fr.resolution);
  const [status, setStatus] = useState(fr.status);
  return (
    <div className="mt-3 space-y-3 text-sm">
      <div className="whitespace-pre-wrap text-ink-2">{fr.description}</div>
      {fr.rationale && <div className="text-ink-3">Why: {fr.rationale}</div>}
      <div className="grid gap-2 sm:grid-cols-[140px_1fr]">
        <Select value={status} onChange={(e) => setStatus(e.target.value as FeatureRequest["status"])}>
          {Object.keys(FR_TONE).map((s) => (
            <option key={s} value={s}>
              {s.replace("_", " ")}
            </option>
          ))}
        </Select>
        <Textarea rows={2} placeholder="Resolution / notes" value={resolution} onChange={(e) => setResolution(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="primary" onClick={() => api.updateFeatureRequest(fr.id, { status, resolution }).then(onChange)}>
          Update
        </Button>
        {fr.orgId && (
          <Link href={`/org/${fr.orgId}/sessions?agent=${fr.requesterId}`} className={cx("text-xs text-info hover:underline self-center")}>
            See the requester&apos;s sessions →
          </Link>
        )}
      </div>
    </div>
  );
}