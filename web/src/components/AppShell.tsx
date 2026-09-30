"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { Org } from "@/lib/types";
import { cx } from "./ui";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [orgs, setOrgs] = useState<Org[]>([]);
  // Phones: the sidebar is a drawer, closed whenever you navigate.
  const [menu, setMenu] = useState(false);
  const [menuPath, setMenuPath] = useState(pathname);
  if (menuPath !== pathname) {
    setMenuPath(pathname);
    setMenu(false);
  }

  useEffect(() => {
    let alive = true;
    const load = () => api.orgs().then((o) => alive && setOrgs(o)).catch(() => {});
    load();
    const t = setInterval(load, 10000);
    const onChange = () => load();
    window.addEventListener("pantheon:orgs-changed", onChange);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener("pantheon:orgs-changed", onChange);
    };
  }, []);

  const item = (href: string, label: ReactNode, active: boolean) => (
    <Link
      key={href}
      href={href}
      className={cx(
        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
        active ? "bg-panel-2 text-ink" : "text-ink-2 hover:bg-panel-2/60 hover:text-ink",
      )}
    >
      {label}
    </Link>
  );

  const busy = orgs.reduce((n, o) => n + (o.workingAgents ?? 0), 0);
  return (
    <div className="flex h-full flex-col md:flex-row">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-panel px-2 pt-[env(safe-area-inset-top)] md:hidden">
        <button
          onClick={() => setMenu(true)}
          aria-label="Open menu"
          className="flex size-10 items-center justify-center rounded-md text-ink-2 hover:bg-panel-2 hover:text-ink cursor-pointer"
        >
          <Icon d="M4 7h16M4 12h16M4 17h16" />
        </button>
        <Link href="/" className="flex items-center gap-2">
          <Logo size={20} />
          <span className="font-semibold tracking-tight">Pantheon</span>
        </Link>
        {busy > 0 && <span className="ml-auto mr-2 text-xs text-accent">{busy} working</span>}
      </div>
      {menu && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMenu(false)} />}
      <aside
        className={cx(
          "flex w-64 shrink-0 flex-col border-r border-line bg-panel md:static md:z-auto md:w-56 md:translate-x-0",
          "fixed inset-y-0 left-0 z-50 pt-[env(safe-area-inset-top)] transition-transform duration-200",
          menu ? "translate-x-0 shadow-2xl" : "-translate-x-full",
        )}
      >
        <Link href="/" className="flex items-center gap-2 px-4 py-4">
          <Logo />
          <span className="text-[15px] font-semibold tracking-tight">Pantheon</span>
        </Link>
        <nav className="flex flex-col gap-0.5 px-2">
          {item("/", <><Icon d="M3 10.5 12 3l9 7.5V21H3z" /> Home</>, pathname === "/")}
          {item("/supervisor", <><Icon d="M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z" /> Zeus</>, pathname.startsWith("/supervisor"))}
          {item("/argus", <><Icon d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" /> Argus</>, pathname.startsWith("/argus"))}
          {item("/settings", <><Icon d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm0-5v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1" /> Settings</>, pathname.startsWith("/settings"))}
        </nav>
        <div className="mt-5 px-4 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-ink-3">Organizations</div>
        <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-3">
          {orgs.map((o) =>
            item(
              `/org/${o.id}`,
              <>
                <span
                  className={cx(
                    "size-2 rounded-full",
                    o.status === "paused" ? "bg-ink-3" : (o.workingAgents ?? 0) > 0 ? "bg-accent pulse-ring" : "bg-ok",
                  )}
                />
                <span className="truncate">{o.name}</span>
                {(o.workingAgents ?? 0) > 0 && <span className="ml-auto text-[11px] text-accent">{o.workingAgents}</span>}
              </>,
              pathname.startsWith(`/org/${o.id}`),
            ),
          )}
          {orgs.length === 0 && <div className="px-2.5 py-1 text-xs text-ink-3">None yet</div>}
        </nav>
      </aside>
      <main className="min-h-0 min-w-0 flex-1 overflow-hidden">{children}</main>
    </div>
  );
}

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round">
      <path d={d} />
    </svg>
  );
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <rect x="2" y="2" width="28" height="28" rx="8" fill="url(#pg)" />
      <path d="M9 23V11m5 12V9m5 14V11m5 12V13" stroke="white" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M7 9.5 16 6l9 3.5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <defs>
        <linearGradient id="pg" x1="2" y1="2" x2="30" y2="30">
          <stop stopColor="#8b7cff" />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function notifyOrgsChanged(): void {
  window.dispatchEvent(new Event("pantheon:orgs-changed"));
}
