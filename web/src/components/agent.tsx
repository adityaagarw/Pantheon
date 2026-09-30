"use client";

import type { Agent, RuntimeStatus } from "@/lib/types";
import { initials } from "@/lib/format";
import { Badge, cx, type Tone } from "./ui";

export const STATUS_META: Record<RuntimeStatus, { label: string; tone: Tone; dot: string }> = {
  idle: { label: "Idle", tone: "neutral", dot: "bg-ink-3" },
  working: { label: "Working", tone: "accent", dot: "bg-accent" },
  awaiting_approval: { label: "Needs approval", tone: "warn", dot: "bg-warn" },
  error: { label: "Error", tone: "bad", dot: "bg-bad" },
  retrying: { label: "Retrying", tone: "warn", dot: "bg-warn" },
  cooling_down: { label: "Cooling down", tone: "info", dot: "bg-info" },
};

export function StatusDot({ status, className }: { status: RuntimeStatus; className?: string }) {
  const m = STATUS_META[status] ?? STATUS_META.idle;
  return (
    <span
      className={cx("inline-block size-2 rounded-full", m.dot, status === "working" && "pulse-ring", className)}
      title={m.label}
    />
  );
}

export function StatusBadge({ status }: { status: RuntimeStatus }) {
  const m = STATUS_META[status] ?? STATUS_META.idle;
  return (
    <Badge tone={m.tone}>
      <StatusDot status={status} className="size-1.5" /> {m.label}
    </Badge>
  );
}

export function AgentAvatar({
  agent,
  size = 28,
  status,
  className,
}: {
  agent: Pick<Agent, "name" | "avatar"> | null | undefined;
  size?: number;
  status?: RuntimeStatus;
  className?: string;
}) {
  const color = agent?.avatar?.outfit ?? "#5b5f6d";
  const accent = agent?.avatar?.accent ?? "#ffffff";
  return (
    <span className={cx("relative inline-flex shrink-0", className)} style={{ width: size, height: size }}>
      <span
        className="flex h-full w-full items-center justify-center rounded-full font-semibold text-white"
        style={{
          background: `radial-gradient(circle at 30% 25%, ${accent}55, transparent 60%), ${color}`,
          fontSize: Math.max(9, size * 0.38),
        }}
      >
        {agent ? initials(agent.name) : "?"}
      </span>
      {status && (
        <span className="absolute -bottom-0.5 -right-0.5 rounded-full border-2 border-panel">
          <StatusDot status={status} className="block" />
        </span>
      )}
    </span>
  );
}

export function UserAvatar({ size = 28 }: { size?: number }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-ink text-bg font-semibold"
      style={{ width: size, height: size, fontSize: Math.max(9, size * 0.38) }}
    >
      You
    </span>
  );
}
