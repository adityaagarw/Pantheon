"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { InboxButton } from "@/components/InboxDrawer";
import { Badge, Button, cx, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useOrg, useOrgConnection } from "@/store/org";

const TABS = [
  { href: "", label: "Office" },
  { href: "/stage", label: "Stage" },
  { href: "/board", label: "Board" },
  { href: "/comms", label: "Comms" },
  { href: "/sessions", label: "Sessions" },
  { href: "/memory", label: "Memory" },
  { href: "/team", label: "Team" },
  { href: "/files", label: "Files" },
  { href: "/settings", label: "Settings" },
];

export default function OrgLayout({ children }: { children: ReactNode }) {
  const { orgId } = useParams<{ orgId: string }>();
  const pathname = usePathname();
  useOrgConnection(orgId);
  const org = useOrg((s) => s.org);
  const stream = useOrg((s) => s.stream);
  const loading = useOrg((s) => s.loading);
  const error = useOrg((s) => s.error);
  const working = useOrg((s) => Object.values(s.agents).filter((a) => a.runtimeStatus === "working").length);
  const base = `/org/${orgId}`;

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 border-b border-line bg-panel px-3 md:h-12 md:flex-nowrap md:px-4">
        <div className="flex h-11 min-w-0 flex-1 items-center gap-2 md:h-auto md:flex-none">
          <span className="truncate font-semibold">{org?.name ?? "…"}</span>
          {org?.status === "paused" && <Badge tone="warn">Paused</Badge>}
          {working > 0 && <Badge tone="accent">{working} working</Badge>}
        </div>
        <nav className="no-scrollbar order-last -mx-3 flex h-10 w-[calc(100%+1.5rem)] items-stretch gap-1 overflow-x-auto border-t border-line px-2 md:order-none md:mx-0 md:h-full md:w-auto md:border-t-0 md:px-0">
          {TABS.map((t) => {
            const href = base + t.href;
            const active = t.href === "" ? pathname === base : pathname.startsWith(href);
            return (
              <Link
                key={t.href}
                href={href}
                className={cx(
                  "flex shrink-0 items-center border-b-2 px-2.5 text-sm transition-colors",
                  active ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink",
                )}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex h-11 items-center gap-2 md:h-auto">
          <span
            className={cx("size-2 rounded-full", stream === "live" ? "bg-ok" : stream === "closed" ? "bg-ink-3" : "bg-warn")}
            title={`Event stream: ${stream}`}
          />
          {org && (
            <Button
              size="sm"
              variant={org.status === "paused" ? "ok" : "secondary"}
              onClick={() => api.updateOrg(org.id, { status: org.status === "paused" ? "running" : "paused" }).then((o) => useOrg.getState().setOrg(o))}
              title={org.status === "paused" ? "Resume all agents" : "Pause all agents (in-flight steps finish)"}
            >
              {org.status === "paused" ? "▶" : "❚❚"}
              <span className="hidden sm:inline">{org.status === "paused" ? " Resume" : " Pause"}</span>
            </Button>
          )}
          <InboxButton orgId={orgId} />
        </div>
      </header>
      <div className="min-h-0 flex-1">
        {loading && !org ? (
          <div className="flex h-full items-center justify-center text-ink-3">
            <Spinner />
          </div>
        ) : error && !org ? (
          <div className="p-8 text-bad">{error}</div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
