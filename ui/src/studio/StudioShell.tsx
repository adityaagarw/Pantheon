/**
 * The Studio's frame: a slim rail (orgs as glyphs with a live activity ring, then the
 * places), the page, and the command bar over everything.
 */
import { motion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { api } from "@/lib/api";
import type { Org } from "@/lib/types";
import { CommandBar } from "./CommandBar";
import { Icon, type IconName } from "./icons";
import { agentHue, hsl, initials } from "./look";
import { useStudio } from "./store";
import "./studio.css";

function RailButton({ to, icon, label, active }: { to: string; icon: IconName; label: string; active: boolean }) {
  return (
    <Link to={to} title={label} className="group relative grid size-11 place-items-center">
      {active && (
        <motion.span
          layoutId="rail-active"
          className="absolute inset-0 rounded-xl"
          style={{ background: "rgba(157,140,255,0.14)", boxShadow: "inset 0 0 0 1px rgba(157,140,255,0.3)" }}
          transition={{ type: "spring", stiffness: 500, damping: 34 }}
        />
      )}
      <span className={`relative transition-colors ${active ? "text-[var(--st-ink)]" : "text-[var(--st-ink-3)] group-hover:text-[var(--st-ink)]"}`}>
        <Icon name={icon} size={19} />
      </span>
    </Link>
  );
}

function OrgGlyph({ org, active }: { org: Org; active: boolean }) {
  const hue = agentHue({ name: org.name, avatar: {} });
  const working = org.workingAgents ?? 0;
  const paused = org.status === "paused";
  return (
    <Link to={`/org/${org.id}`} title={`${org.name}${working ? ` · ${working} working` : ""}${paused ? " · paused" : ""}`}
      className="group relative grid size-11 place-items-center">
      {active && (
        <motion.span layoutId="rail-active" className="absolute inset-0 rounded-xl"
          style={{ background: "rgba(157,140,255,0.14)", boxShadow: "inset 0 0 0 1px rgba(157,140,255,0.3)" }}
          transition={{ type: "spring", stiffness: 500, damping: 34 }} />
      )}
      <span
        className="relative grid size-8 place-items-center rounded-[10px] text-[12px] font-semibold text-white/95 transition-transform group-hover:scale-105"
        style={{
          background: `linear-gradient(140deg, ${hsl(hue, 75, 62)}, ${hsl((hue + 40) % 360, 70, 42)})`,
          opacity: paused ? 0.45 : 1,
          boxShadow: working ? `0 0 0 2px var(--st-void), 0 0 0 3.5px var(--st-violet), 0 0 16px -2px var(--st-violet)` : undefined,
        }}
      >
        {initials(org.name)}
      </span>
      {working > 0 && (
        <span className="st-mono absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-[var(--st-violet-2)] px-1 text-[9.5px] text-white">
          {working}
        </span>
      )}
    </Link>
  );
}

export function StudioShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const setCommand = useStudio((s) => s.setCommand);
  const [orgs, setOrgs] = useState<Org[]>([]);
  // Phones: the rail is a drawer, closed whenever you go somewhere.
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
    const t = setInterval(load, 8000);
    const onChange = () => load();
    window.addEventListener("pantheon:orgs-changed", onChange);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener("pantheon:orgs-changed", onChange);
    };
  }, []);

  const on = (p: string) => (p === "/" ? pathname === "/" : pathname.startsWith(p));
  return (
    <div className="studio st-grain relative flex h-full">
      <button
        onClick={() => setMenu(true)}
        aria-label="Menu"
        className="fixed left-2 top-[max(0.5rem,env(safe-area-inset-top))] z-40 grid size-10 place-items-center rounded-xl border border-[var(--st-edge)] bg-[rgba(14,15,20,0.85)] text-[var(--st-ink-2)] backdrop-blur md:hidden"
      >
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>
      {menu && <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMenu(false)} />}
      <nav
        className={`z-50 flex w-[62px] shrink-0 flex-col items-center gap-1 overflow-y-auto py-3 transition-transform duration-200 max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:bg-[#0c0d12] max-md:pt-[max(0.75rem,env(safe-area-inset-top))] max-md:shadow-2xl md:relative md:z-10 md:translate-x-0 ${menu ? "translate-x-0" : "max-md:-translate-x-full"}`}
      >
        <Link to="/" title="Pantheon Studio" className="mb-2 grid size-11 place-items-center">
          <img src="/logo.svg" alt="" className="size-8" onError={(e) => (e.currentTarget.style.display = "none")} />
        </Link>
        <RailButton to="/" icon="home" label="Home" active={on("/")} />
        <RailButton to="/supervisor" icon="zeus" label="Zeus, the supervisor" active={on("/supervisor")} />
        <RailButton to="/argus" icon="argus" label="Argus, oversight" active={on("/argus")} />
        <RailButton to="/kronos" icon="kronos" label="Kronos" active={on("/kronos")} />
        <div className="my-2 h-px w-7 bg-[var(--st-edge)]" />
        <div className="no-scrollbar flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto">
          {orgs.map((o) => <OrgGlyph key={o.id} org={o} active={pathname.startsWith(`/org/${o.id}`)} />)}
          <Link to="/" title="New organization" className="grid size-11 place-items-center text-[var(--st-ink-3)] hover:text-[var(--st-ink)]">
            <span className="grid size-8 place-items-center rounded-[10px] border border-dashed border-[var(--st-edge-2)]">
              <Icon name="plus" size={15} />
            </span>
          </Link>
        </div>
        <div className="my-2 h-px w-7 bg-[var(--st-edge)]" />
        <button onClick={() => setCommand(true)} title="Command bar (Ctrl K)"
          className="grid size-11 place-items-center text-[var(--st-ink-3)] hover:text-[var(--st-ink)]">
          <Icon name="search" size={19} />
        </button>
        <RailButton to="/packs" icon="packs" label="Packs: libraries, assets, plugins" active={on("/packs")} />
        <RailButton to="/benchmarks" icon="bench" label="Benchmarks" active={on("/benchmarks")} />
        <RailButton to="/settings" icon="settings" label="Settings" active={on("/settings")} />
      </nav>
      <main className="relative z-10 min-h-0 min-w-0 flex-1 overflow-hidden max-md:pt-14">{children}</main>
      <CommandBar />
    </div>
  );
}
