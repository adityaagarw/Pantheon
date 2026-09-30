"use client";


import { useShallow } from "zustand/react/shallow";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Approval, InboxItem } from "@/lib/types";
import { useOrg } from "@/store/org";
import { AgentAvatar } from "./agent";
import { Badge, Button, Drawer, Empty, Input, cx } from "./ui";

export function InboxButton({ orgId }: { orgId: string }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<InboxItem[]>([]);
  const approvals = useOrg(useShallow((s) => Object.values(s.approvals)));
  const version = useOrg((s) => s.inboxVersion);
  const feedLen = useOrg((s) => s.feed.length);

  useEffect(() => {
    api.inbox(orgId).then(setItems).catch(() => {});
  }, [orgId, version, feedLen, open]);

  const unread = items.filter((i) => !i.read && i.kind !== "approval").length;
  const count = approvals.length + unread;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={cx(
          "relative flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-sm transition-colors cursor-pointer",
          approvals.length ? "border-warn/40 bg-warn/10 text-warn" : "border-line-2 text-ink-2 hover:text-ink",
        )}
      >
        <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M4 13h4l2 3h4l2-3h4M4 13l2.5-8h11L20 13v6H4z" strokeLinejoin="round" />
        </svg>
        Inbox
        {count > 0 && <span className="rounded-full bg-accent-2 px-1.5 text-[11px] font-semibold text-white">{count}</span>}
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} width="w-[520px]">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="font-semibold">Inbox</div>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => api.markRead(orgId).then(() => api.inbox(orgId).then(setItems))}>
              Mark all read
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              ✕
            </Button>
          </div>
        </div>
        <div className="space-y-5 p-5">
          <section>
            <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">Waiting for your approval</div>
            {approvals.length === 0 ? (
              <div className="text-sm text-ink-3">Nothing to approve.</div>
            ) : (
              <div className="space-y-3">
                {approvals.map((a) => (
                  <ApprovalCard key={a.id} approval={a} />
                ))}
              </div>
            )}
          </section>
          <section>
            <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">Recent</div>
            {items.filter((i) => i.kind !== "approval").length === 0 ? (
              <Empty title="All caught up" />
            ) : (
              <div className="divide-y divide-line rounded-lg border border-line">
                {items
                  .filter((i) => i.kind !== "approval")
                  .slice(0, 80)
                  .map((i) => (
                    <InboxRow key={i.id} item={i} orgId={orgId} onNavigate={() => setOpen(false)} />
                  ))}
              </div>
            )}
          </section>
        </div>
      </Drawer>
    </>
  );
}

function InboxRow({ item, orgId, onNavigate }: { item: InboxItem; orgId: string; onNavigate: () => void }) {
  const href =
    item.kind === "message"
      ? `/org/${orgId}/comms`
      : item.kind === "feature_request"
        ? "/supervisor"
        : item.kind === "error"
          ? `/org/${orgId}/sessions`
          : item.kind === "alert"
            ? "/argus"
            : `/org/${orgId}`;
  const critical = item.kind === "alert" && item.title.startsWith("[critical]");
  const tone = item.kind === "error" || critical ? "bad" : item.kind === "alert" ? "warn" : item.kind === "feature_request" ? "info" : "neutral";
  return (
    <Link href={href} onClick={onNavigate} className="flex items-start gap-3 px-3 py-2.5 hover:bg-panel-2">
      {!item.read && <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-accent" />}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{item.kind.replace("_", " ")}</Badge>
          <span className="text-[11px] text-ink-3">{timeAgo(item.createdAt)}</span>
        </div>
        <div className={cx("mt-1 text-sm text-ink-2", item.kind === "alert" ? "line-clamp-4 whitespace-pre-wrap" : "line-clamp-2")}>{item.title}</div>
      </div>
    </Link>
  );
}

export function ApprovalCard({ approval }: { approval: Approval }) {
  const agent = useOrg((s) => s.agents[approval.agentId]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "deny" | "always" | null>(null);
  const decide = async (ok: boolean, always = false) => {
    setBusy(always ? "always" : ok ? "approve" : "deny");
    try {
      await api.decide(approval.id, ok, note, always);
    } finally {
      setBusy(null);
    }
  };
  const command = typeof approval.args.command === "string" ? approval.args.command : null;
  return (
    <div className="rounded-lg border border-warn/30 bg-warn/5 p-3.5">
      <div className="flex items-center gap-2">
        <AgentAvatar agent={agent} size={22} />
        <span className="text-sm font-medium">{agent?.name ?? approval.agentId}</span>
        <span className="text-sm text-ink-3">wants to run</span>
        <code className="rounded bg-panel-2 px-1.5 py-0.5 font-mono text-xs text-warn">{approval.tool}</code>
        <span className="ml-auto text-[11px] text-ink-3">{timeAgo(approval.createdAt)}</span>
      </div>
      {command ? (
        <pre className="mt-2.5 overflow-x-auto rounded-md border border-line bg-bg px-3 py-2 font-mono text-xs text-ink">$ {command}</pre>
      ) : (
        <pre className="mt-2.5 max-h-56 overflow-auto rounded-md border border-line bg-bg px-3 py-2 font-mono text-xs text-ink-2">
          {JSON.stringify(approval.args, null, 2)}
        </pre>
      )}
      <div className="mt-2.5 flex gap-2">
        <Input className="h-8 text-xs" placeholder="Optional note to the agent" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" variant="danger" onClick={() => decide(false)} loading={busy === "deny"}>
          Deny
        </Button>
        <Button size="sm" variant="ok" onClick={() => decide(true)} loading={busy === "approve"}>
          Approve
        </Button>
      </div>
      <div className="mt-1.5 text-right">
        <button
          className="text-[11px] text-ink-3 hover:text-ok cursor-pointer disabled:opacity-50"
          disabled={!!busy}
          onClick={() => decide(true, true)}
          title={`Approve and stop asking about ${approval.tool} for ${agent?.name ?? "this agent"}`}
        >
          {busy === "always" ? "…" : `Approve & always allow ${approval.tool} for ${agent?.name ?? "this agent"}`}
        </button>
      </div>
    </div>
  );
}