"use client";

import { useEffect, useState } from "react";
import { ActivityFeed } from "@/components/ActivityFeed";
import { MetaChat, MetaLayout, SYSTEM_ORG } from "@/components/MetaChat";
import { HostFolders, useHostStatus } from "@/components/HostStatus";
import { Badge, Spinner, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { useOrgConnection } from "@/store/org";

const PROMPTS = [
  "Add a built-in tool that lets agents read RSS feeds.",
  "Zeus says agents need a CSV export of the task board. Can you build it?",
  "What can you reach on my computer right now?",
  "Summarize the reports in my allowed folder into one document.",
];

function KronosIcon({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <rect x="2" y="2" width="28" height="28" rx="8" fill="url(#kr)" />
      <circle cx="16" cy="16" r="8.5" stroke="white" strokeWidth="2" />
      <path d="M16 11v5.2l3.4 2.2" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <defs>
        <linearGradient id="kr" x1="2" y1="2" x2="30" y2="30">
          <stop stopColor="#d9a35b" />
          <stop offset="1" stopColor="#6b4a2f" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export default function KronosPage() {
  useOrgConnection(SYSTEM_ORG);
  const [kronosId, setKronosId] = useState<string | null>(null);
  const [tab, setTab] = useState<"computer" | "activity">("computer");
  const [side, setSide] = useState(false);
  useEffect(() => {
    api.metaAgent("kronos").then((s) => setKronosId(s.agent.id)).catch(() => {});
  }, []);
  return (
    <MetaLayout
      sideOpen={side}
      onCloseSide={() => setSide(false)}
      chat={
        kronosId ? (
          <MetaChat
            agentId={kronosId}
            fallbackName="Kronos"
            icon={<KronosIcon />}
            subtitle="The engineer. Works on your computer: changes Pantheon's own code when Zeus or you need it, and handles files in folders you allow."
            intro="Ask Kronos for a new capability or a fix to Pantheon, or for work with files on your computer. Code changes are tested before they're applied."
            prompts={PROMPTS}
            placeholder="Ask Kronos to build or fix something…"
            sideLabel="Computer"
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
                { value: "computer", label: "Computer" },
                { value: "activity", label: "What Kronos is doing" },
              ]}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{tab === "computer" ? <Computer /> : <ActivityFeed />}</div>
        </>
      }
    />
  );
}

function Computer() {
  const status = useHostStatus();
  if (!status) return <Spinner />;
  const info = status.info;
  return (
    <div className="space-y-5 p-4 text-sm">
      {info?.host && (
        <div className="flex items-center gap-2">
          <Badge tone="ok">this computer</Badge>
          <span className="text-xs text-ink-3">{info.host}</span>
        </div>
      )}

      {info && (
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">Pantheon code</div>
            <div className="break-all text-xs text-ink-2">{info.repo ?? "Pantheon is installed, not running from its source, so Kronos can't change its code here."}</div>
            {info.branch && <div className="mt-0.5 text-xs text-ink-3">changes merge into branch {info.branch}, after the checks pass</div>}
          </div>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">Folders you allowed</div>
            <HostFolders allowed={info.allowed ?? []} compact />
          </div>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">Changes in progress</div>
            {info.openChanges?.length ? (
              <ul className="space-y-1.5">
                {info.openChanges.map((c) => (
                  <li key={c.id} className="rounded-md border border-line px-2.5 py-1.5">
                    <div className="text-xs font-medium">{c.title}</div>
                    <div className="font-mono text-[11px] text-ink-3">kronos/{c.id}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-xs text-ink-3">None.</div>
            )}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-line p-3 text-xs leading-relaxed text-ink-2">
        <div className="mb-1 font-medium text-ink">How Kronos changes Pantheon</div>
        Every change is made on its own git branch in a separate worktree, never in your working copy. Applying it runs lint, type checks and the test suites; only a
        change that passes is merged into your current branch, and then Pantheon rebuilds itself (a minute or two). If your uncommitted work touches the same files,
        Kronos stops and asks you first. Shell commands outside these checks need your approval.
      </div>
    </div>
  );
}
