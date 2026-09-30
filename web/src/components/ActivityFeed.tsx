"use client";

import { memo } from "react";
import { timeAgo, truncate } from "@/lib/format";
import { useOrg } from "@/store/org";
import type { FeedItem } from "@/store/reduce";
import { AgentAvatar } from "./agent";
import { cx } from "./ui";

const KIND_STYLE: Record<string, { icon: string; color: string }> = {
  message: { icon: "💬", color: "text-ink" },
  thought: { icon: "💭", color: "text-ink-2 italic" },
  tool: { icon: "🛠", color: "text-ink-2" },
  tool_error: { icon: "⚠", color: "text-bad" },
  task: { icon: "▣", color: "text-info" },
  meeting: { icon: "◎", color: "text-accent" },
  approval: { icon: "✔", color: "text-ok" },
  awaiting_approval: { icon: "⏳", color: "text-warn" },
  error: { icon: "⛔", color: "text-bad" },
  retrying: { icon: "↻", color: "text-warn" },
  cooling_down: { icon: "❄", color: "text-info" },
  warning: { icon: "⚠", color: "text-warn" },
  feature: { icon: "✦", color: "text-accent" },
  notice: { icon: "•", color: "text-ink-3" },
};

export function ActivityFeed({ onSelectAgent, limit = 120, filterAgent }: { onSelectAgent?: (id: string) => void; limit?: number; filterAgent?: string | null }) {
  const feed = useOrg((s) => s.feed);
  const items = (filterAgent ? feed.filter((f) => f.agentId === filterAgent) : feed).slice(0, limit);
  if (!items.length) return <div className="px-4 py-6 text-center text-sm text-ink-3">Waiting for activity…</div>;
  return (
    <div className="divide-y divide-line/60">
      {items.map((f) => (
        <FeedRow key={f.id} item={f} onSelectAgent={onSelectAgent} />
      ))}
    </div>
  );
}

const FeedRow = memo(function FeedRow({ item, onSelectAgent }: { item: FeedItem; onSelectAgent?: (id: string) => void }) {
  const agent = useOrg((s) => (item.agentId ? s.agents[item.agentId] : undefined));
  const style = KIND_STYLE[item.kind] ?? KIND_STYLE.notice;
  return (
    <div
      className={cx("fade-in flex gap-2.5 px-3 py-2 text-[13px]", agent && onSelectAgent && "cursor-pointer hover:bg-panel-2")}
      onClick={() => agent && onSelectAgent?.(agent.id)}
    >
      <div className="w-6 shrink-0 pt-0.5">{agent ? <AgentAvatar agent={agent} size={22} /> : <span className="pl-1">{style.icon}</span>}</div>
      <div className="min-w-0 flex-1">
        <div className={cx("leading-snug break-words", style.color)}>
          {agent && <span className="mr-1 not-italic">{style.icon}</span>}
          {truncate(item.text, 280)}
        </div>
        <div className="mt-0.5 text-[11px] text-ink-3">{timeAgo(item.ts)}</div>
      </div>
    </div>
  );
});
