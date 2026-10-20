/**
 * An org's workspace: the command bar on top, the dock of panes, the shelf of panes put
 * away, and the timeline. The arrangement is saved per org (docs/DESIGN.md).
 */
import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type DockviewTheme,
  type IDockviewHeaderActionsProps,
  type IDockviewPanelProps,
} from "dockview-react";
import "dockview-react/dist/styles/dockview.css";
import { motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { InboxButton } from "@/components/InboxDrawer";
import { api } from "@/lib/api";
import { useOrg, useOrgConnection } from "@/store/org";
import { Icon } from "./icons";
import { paneById, PANES } from "./panes";
import { useStudio } from "./store";
import { Timeline } from "./Timeline";

const THEME: DockviewTheme = { name: "studio", className: "dockview-theme-studio", colorScheme: "dark", gap: 8 };
const LAYOUT_VERSION = 3; // 2: the office is the main view; 3: Flows by the Board
const layoutKey = (orgId: string) => `studio.layout.v${LAYOUT_VERSION}.${orgId}`;

interface Saved {
  layout: ReturnType<DockviewApi["toJSON"]>;
  shelf: string[];
}

function PaneHost({ params }: IDockviewPanelProps<{ pane: string }>) {
  const def = paneById(params.pane);
  return <div className="h-full w-full overflow-hidden">{def ? def.render() : null}</div>;
}

type Position = Parameters<DockviewApi["addPanel"]>[0]["position"];

const addPane = (dock: DockviewApi, pane: string, position?: Position) =>
  dock.addPanel({ id: pane, component: "pane", title: paneById(pane)!.title, params: { pane }, position });

/** The default arrangement: the 3D office (with its own activity and inspector panel)
 * filling the view, work surfaces below. Everything else waits on the shelf. */
function defaultLayout(dock: DockviewApi): string[] {
  dock.clear();
  addPane(dock, "office");
  addPane(dock, "board", { referencePanel: "office", direction: "below" });
  addPane(dock, "flows", { referencePanel: "board", direction: "within" });
  addPane(dock, "comms", { referencePanel: "board", direction: "within" });
  addPane(dock, "stage", { referencePanel: "board", direction: "within" });
  dock.getPanel("board")?.api.setActive();
  dock.getPanel("office")?.api.setActive();
  try {
    dock.getPanel("board")?.group.api.setSize({ height: 260 });
  } catch {
    // sizes are a nicety
  }
  return PANES.map((p) => p.id).filter((id) => !dock.getPanel(id));
}

/** True on phone-sized screens (Tailwind's md breakpoint). */
function usePhone(): boolean {
  const query = "(max-width: 767px)";
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setPhone(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return phone;
}

type ViewMode = "windows" | "tabs";
const VIEW_KEY = "studio.view";

/** Windows (panes docked side by side, floatable, resizable) or tabs (one pane at a time,
 * filling the workspace). One choice for every org, remembered on this computer. */
function useViewMode(): [ViewMode, (v: ViewMode) => void] {
  const [mode, setMode] = useState<ViewMode>(() => {
    try {
      return localStorage.getItem(VIEW_KEY) === "tabs" ? "tabs" : "windows";
    } catch {
      return "windows";
    }
  });
  const choose = (v: ViewMode) => {
    setMode(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // a convenience only
    }
  };
  return [mode, choose];
}

/** A link to one of an org's pages (/org/<id>/sessions?agent=…, Pantheon 1's tab paths)
 * opens that pane, and its query stays for the pane to read. */
function useLinkedPane() {
  const params = useParams<{ orgId: string; "*": string }>();
  const { search } = useLocation();
  const navigate = useNavigate();
  const rest = params["*"] ?? "";
  useEffect(() => {
    const pane = rest.split("/")[0];
    if (!pane) return;
    if (paneById(pane)) window.dispatchEvent(new CustomEvent("studio:open-pane", { detail: pane }));
    navigate(`/org/${params.orgId}${search}`, { replace: true });
  }, [rest, search, params.orgId, navigate]);
}

export default function OrgWorkspace() {
  useLinkedPane();
  const phone = usePhone();
  const [mode, setMode] = useViewMode();
  if (phone) return <PhoneWorkspace />;
  return mode === "tabs" ? <TabsWorkspace onView={setMode} /> : <DockWorkspace onView={setMode} />;
}

const activeKey = (orgId: string) => `studio.tab.${orgId}`;

/** Tabs: a tab for every pane, and the chosen one fills the workspace. Panes stay alive
 * once opened, so switching back is instant and keeps their place. */
function TabsWorkspace({ onView }: { onView: (v: ViewMode) => void }) {
  const { orgId } = useParams<{ orgId: string }>();
  useOrgConnection(orgId!);
  const [active, setActive] = useState(() => {
    try {
      const saved = localStorage.getItem(activeKey(orgId!));
      return saved && paneById(saved) ? saved : "office";
    } catch {
      return "office";
    }
  });
  const [mounted, setMounted] = useState<string[]>([active]);

  const show = (id: string) => {
    if (!paneById(id)) return;
    setMounted((m) => (m.includes(id) ? m : [...m, id]));
    setActive(id);
    try {
      localStorage.setItem(activeKey(orgId!), id);
    } catch {
      // a convenience only
    }
  };

  useEffect(() => {
    const onOpen = (e: Event) => show((e as CustomEvent<string>).detail);
    window.addEventListener("studio:open-pane", onOpen);
    return () => window.removeEventListener("studio:open-pane", onOpen);
  });

  return (
    <div className="flex h-full min-w-0 flex-col">
      <TopBar orgId={orgId!} onReset={() => show("office")} view="tabs" onView={onView} />
      <div className="no-scrollbar flex shrink-0 items-center gap-1 overflow-x-auto px-3 pb-2">
        {PANES.map((p) => (
          <button
            key={p.id}
            onClick={() => show(p.id)}
            title={p.hint}
            className={`flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12.5px] transition-colors ${p.id === active ? "border-[var(--st-edge-2)] bg-[rgba(157,140,255,0.12)] text-[var(--st-ink)]" : "border-transparent text-[var(--st-ink-3)] hover:bg-white/[0.04] hover:text-[var(--st-ink-2)]"}`}
          >
            <Icon name={p.icon} size={14} />
            {p.title}
          </button>
        ))}
      </div>
      <div className="relative mx-2 min-h-0 flex-1 overflow-hidden rounded-[var(--st-radius)] border border-[var(--st-edge)] bg-[var(--st-glass)]">
        {mounted.map((id) => {
          const def = paneById(id);
          return def ? (
            <div key={id} className="absolute inset-0" style={{ display: id === active ? "block" : "none" }}>
              {def.render()}
            </div>
          ) : null;
        })}
      </div>
      <Timeline />
    </div>
  );
}

/** Phones: one pane at a time, chosen from a strip of tabs; no docking, shelf or timeline. */
function PhoneWorkspace() {
  const { orgId } = useParams<{ orgId: string }>();
  useOrgConnection(orgId!);
  const [pane, setPane] = useState(() => {
    try {
      return localStorage.getItem("studio.phone.pane") || "office";
    } catch {
      return "office";
    }
  });
  const choose = (id: string) => {
    setPane(id);
    try {
      localStorage.setItem("studio.phone.pane", id);
    } catch {
      // a convenience only
    }
  };
  useEffect(() => {
    const onOpen = (e: Event) => choose((e as CustomEvent<string>).detail);
    window.addEventListener("studio:open-pane", onOpen);
    return () => window.removeEventListener("studio:open-pane", onOpen);
  });
  const def = paneById(pane) ?? paneById("office")!;
  return (
    <div className="flex h-full min-w-0 flex-col">
      <TopBar orgId={orgId!} onReset={() => choose("office")} compact />
      <div className="no-scrollbar flex shrink-0 gap-1 overflow-x-auto border-b border-[var(--st-edge)] px-2 pb-2">
        {PANES.map((p) => (
          <button
            key={p.id}
            onClick={() => choose(p.id)}
            className={`flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] ${p.id === def.id ? "bg-white/[0.08] text-[var(--st-ink)]" : "text-[var(--st-ink-3)]"}`}
          >
            <Icon name={p.icon} size={14} />
            {p.title}
          </button>
        ))}
      </div>
      <div key={def.id} className="min-h-0 flex-1 overflow-hidden">
        {def.render()}
      </div>
    </div>
  );
}

function DockWorkspace({ onView }: { onView: (v: ViewMode) => void }) {
  const { orgId } = useParams<{ orgId: string }>();
  useOrgConnection(orgId!);
  const setDock = useStudio((s) => s.setDock);
  const select = useStudio((s) => s.select);
  const maximized = useStudio((s) => s.maximized !== null);
  const setMaximized = useStudio((s) => s.setMaximized);
  const [dock, setLocalDock] = useState<DockviewApi | null>(null);
  const waiting = useRef<string | null>(null); // a pane asked for before the dock was ready
  const [shelf, setShelf] = useState<string[]>([]);
  const shelfRef = useRef<string[]>([]);
  useEffect(() => {
    shelfRef.current = shelf;
  }, [shelf]);

  useEffect(() => () => {
    setDock(null);
    select(null);
    setMaximized(null);
  }, [orgId, setDock, select, setMaximized]);

  const save = useCallback((d: DockviewApi, nextShelf = shelfRef.current) => {
    try {
      localStorage.setItem(layoutKey(orgId!), JSON.stringify({ layout: d.toJSON(), shelf: nextShelf } satisfies Saved));
    } catch {
      // storage unavailable: the layout lasts for this session
    }
  }, [orgId]);

  const onReady = (e: DockviewReadyEvent) => {
    const d = e.api;
    let restored = false;
    try {
      const raw = localStorage.getItem(layoutKey(orgId!));
      if (raw) {
        const saved = JSON.parse(raw) as Saved;
        d.fromJSON(saved.layout);
        setShelf(saved.shelf.filter((id) => paneById(id) && !d.getPanel(id)));
        restored = d.panels.length > 0;
      }
    } catch {
      restored = false;
    }
    if (!restored) setShelf(defaultLayout(d));
    d.onDidLayoutChange(() => save(d));
    // A pane closed from its tab goes to the shelf, never away.
    d.onDidRemovePanel((p) => {
      const pane = (p.params as { pane?: string } | undefined)?.pane ?? p.id;
      setShelf((s) => (s.includes(pane) ? s : [...s, pane]));
    });
    // Leaving maximized by any route (a pane closed, dragged, or dockview restoring the
    // grid) brings the shelf and timeline back.
    d.onDidRemoveGroup((g) => {
      if (useStudio.getState().maximized === g.id) setMaximized(null);
    });
    d.onDidMaximizedGroupChange(() => {
      const id = useStudio.getState().maximized;
      const g = id ? d.groups.find((x) => x.id === id) : undefined;
      if (g && d.groups.length > 1 && !g.api.isMaximized()) setMaximized(null);
    });
    setLocalDock(d);
    setDock(d);
  };

  useEffect(() => {
    if (dock) save(dock, shelf);
  }, [shelf, dock, save]);

  const open = (pane: string) => {
    if (!dock) {
      waiting.current = pane;
      return;
    }
    const existing = dock.getPanel(pane);
    if (existing) {
      existing.api.setActive();
      return;
    }
    setShelf((s) => s.filter((x) => x !== pane));
    // A pane comes back as its own window beside the others, never as another tab, so
    // panes opened one after another stay separate. It splits off the largest window
    // other than the office, which keeps its room; beside the office it starts narrow.
    const grid = dock.groups.filter((g) => g.api.location.type === "grid");
    const others = grid.filter((g) => !g.panels.some((p) => p.id === "office"));
    const largest = (others.length ? others : grid).reduce<(typeof grid)[number] | undefined>(
      (best, g) => (!best || g.width * g.height > best.width * best.height ? g : best), undefined);
    const ref = largest?.activePanel;
    if (!largest || !ref) {
      addPane(dock, pane);
      return;
    }
    const direction = largest.width >= largest.height ? "right" : "below";
    const added = addPane(dock, pane, { referencePanel: ref.id, direction });
    if (!others.length) {
      try {
        added.group.api.setSize(direction === "right" ? { width: 440 } : { height: 280 });
      } catch {
        // sizes are a nicety
      }
    }
  };

  useEffect(() => {
    const onOpen = (e: Event) => open((e as CustomEvent<string>).detail);
    window.addEventListener("studio:open-pane", onOpen);
    return () => window.removeEventListener("studio:open-pane", onOpen);
  });

  useEffect(() => {
    if (dock && waiting.current) {
      const pane = waiting.current;
      waiting.current = null;
      open(pane);
    }
  });

  const reset = () => {
    if (!dock) return;
    localStorage.removeItem(layoutKey(orgId!));
    setMaximized(null);
    setShelf(defaultLayout(dock));
  };

  return (
    <div className="flex h-full min-w-0 flex-col">
      <TopBar orgId={orgId!} onReset={reset} view="windows" onView={onView} />
      <div className="relative min-h-0 flex-1 px-2">
        <DockviewReact
          className="h-full"
          theme={THEME}
          components={{ pane: PaneHost }}
          rightHeaderActionsComponent={GroupActions}
          onReady={onReady}
        />
      </div>
      {!maximized && (
        <>
          <Shelf shelf={shelf} onOpen={open} />
          <Timeline />
        </>
      )}
    </div>
  );
}

/** A group's controls: put the pane on the shelf, float it, maximize it.
 *
 * Maximized means the group fills the workspace: dockview hides the other groups and the
 * shelf and timeline step aside. Dockview can't maximize a floating group, so a floating
 * one docks back into the grid first. */
function GroupActions(props: IDockviewHeaderActionsProps) {
  const max = useStudio((s) => s.maximized === props.group.id);
  const setMaximized = useStudio((s) => s.setMaximized);
  const maximize = () => {
    const dock = props.containerApi;
    if (props.api.location.type === "floating") {
      const grid = dock.groups.filter((g) => g !== props.group && g.api.location.type === "grid");
      const target = grid.reduce<(typeof grid)[number] | undefined>(
        (best, g) => (!best || g.width * g.height > best.width * best.height ? g : best), undefined);
      if (target) props.group.api.moveTo({ group: target, position: "right" });
    }
    requestAnimationFrame(() => {
      if (props.group.api.location.type === "grid") props.group.api.maximize();
      setMaximized(props.group.id);
    });
  };
  const restore = () => {
    if (props.group.api.isMaximized()) props.group.api.exitMaximized();
    setMaximized(null);
  };
  const btn = "grid size-7 place-items-center rounded-md text-[var(--st-ink-3)] transition-colors hover:bg-white/[0.06] hover:text-[var(--st-ink)]";
  const floating = props.api.location.type === "floating";
  return (
    <div className="flex h-full items-center gap-0.5 pr-1">
      <button className={btn} title="Put away (on the shelf below)" onClick={() => props.activePanel?.api.close()}>
        <Icon name="collapse" size={14} style={{ transform: "rotate(-90deg)" }} />
      </button>
      {!floating && (
        <button className={btn} title="Float" onClick={() => props.containerApi.addFloatingGroup(props.group)}>
          <Icon name="float" size={14} />
        </button>
      )}
      <button
        className={btn}
        title={max ? "Restore" : "Maximize"}
        onClick={max ? restore : maximize}
      >
        <Icon name={max ? "restore" : "maximize"} size={14} />
      </button>
    </div>
  );
}

/** Panes put away, one click from coming back. */
function Shelf({ shelf, onOpen }: { shelf: string[]; onOpen: (pane: string) => void }) {
  if (!shelf.length) return null;
  return (
    <div className="flex shrink-0 items-center gap-1.5 overflow-x-auto px-3 pb-1 pt-2">
      <span className="mr-1 text-[10.5px] uppercase tracking-[0.12em] text-[var(--st-ink-3)]">Shelf</span>
      {shelf.map((id) => {
        const def = paneById(id);
        if (!def) return null;
        return (
          <motion.button
            key={id}
            layout
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 500, damping: 32 }}
            onClick={() => onOpen(id)}
            title={def.hint}
            className="flex shrink-0 items-center gap-1.5 rounded-full border border-[var(--st-edge)] bg-[var(--st-glass)] px-2.5 py-1 text-[12px] text-[var(--st-ink-2)] transition-colors hover:border-[var(--st-edge-2)] hover:text-[var(--st-ink)]"
          >
            <Icon name={def.icon} size={13} /> {def.title}
          </motion.button>
        );
      })}
    </div>
  );
}

/** The switch between windows and tabs. */
function ViewSwitch({ view, onView }: { view: ViewMode; onView: (v: ViewMode) => void }) {
  const opts: { v: ViewMode; label: string; hint: string }[] = [
    { v: "windows", label: "Windows", hint: "Panes side by side: drag, resize, float" },
    { v: "tabs", label: "Tabs", hint: "One pane at a time, filling the view" },
  ];
  return (
    <div className="relative flex h-9 shrink-0 items-center rounded-xl border border-[var(--st-edge)] bg-[var(--st-glass)] p-0.5">
      {opts.map((o) => (
        <button
          key={o.v}
          title={o.hint}
          onClick={() => onView(o.v)}
          className={`relative h-full rounded-[9px] px-2.5 text-[12px] transition-colors ${view === o.v ? "text-[var(--st-ink)]" : "text-[var(--st-ink-3)] hover:text-[var(--st-ink-2)]"}`}
        >
          {view === o.v && (
            <motion.span
              layoutId="studio-view-switch"
              className="absolute inset-0 rounded-[9px] bg-[rgba(157,140,255,0.16)]"
              transition={{ type: "spring", stiffness: 500, damping: 36 }}
            />
          )}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

function TopBar({
  orgId,
  onReset,
  compact = false,
  view,
  onView,
}: {
  orgId: string;
  onReset: () => void;
  compact?: boolean;
  view?: ViewMode;
  onView?: (v: ViewMode) => void;
}) {
  const org = useOrg((s) => s.org);
  const stream = useOrg((s) => s.stream);
  const agents = useOrg((s) => s.agents);
  const setCommand = useStudio((s) => s.setCommand);
  const list = Object.values(agents).filter((a) => !a.isSupervisor && !a.metaRole);
  const working = list.filter((a) => a.runtimeStatus === "working").length;
  const needsYou = list.filter((a) => a.runtimeStatus === "awaiting_approval").length;
  const failed = list.filter((a) => a.runtimeStatus === "error").length;
  const paused = org?.status === "paused";

  const chip = (label: string, color: string, n: number) =>
    n > 0 && (
      <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px]" style={{ background: "rgba(255,255,255,0.04)", color }}>
        <span className="size-1.5 rounded-full" style={{ background: color }} />
        {n} {label}
      </span>
    );

  return (
    <div className={`flex shrink-0 items-center gap-3 ${compact ? "h-12 pl-14 pr-2" : "h-14 px-4"}`}>
      <div className="min-w-[120px] shrink-0">
        <div className="st-display truncate text-[19px] leading-tight">{org?.name ?? "…"}</div>
        <div className="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-[var(--st-ink-3)]">
          <span
            className="size-1.5 rounded-full"
            style={{ background: stream === "live" ? "var(--st-green)" : stream === "closed" ? "var(--st-ink-3)" : "var(--st-amber)" }}
          />
          {stream === "live" ? "Live" : stream === "connecting" ? "Connecting…" : stream === "reconnecting" ? "Reconnecting…" : "Offline"}
          {" · "}
          {list.length} agent{list.length === 1 ? "" : "s"}
          {paused && " · paused"}
        </div>
      </div>
      <div className={`flex items-center gap-1.5 ${compact ? "hidden" : ""}`}>
        {chip("working", "var(--st-violet)", working)}
        {chip("need you", "var(--st-amber)", needsYou)}
        {chip("failed", "var(--st-red)", failed)}
      </div>
      <button
        onClick={() => setCommand(true)}
        className={`${compact ? "hidden" : "flex"} mx-auto h-9 w-full min-w-0 max-w-[460px] items-center gap-2.5 overflow-hidden whitespace-nowrap rounded-xl border border-[var(--st-edge)] bg-[var(--st-glass)] px-3 text-[13px] text-[var(--st-ink-3)] transition-colors hover:border-[var(--st-edge-2)] hover:text-[var(--st-ink-2)]`}
      >
        <Icon name="search" size={15} />
        Go anywhere, find anything, tell an agent…
        <kbd className="st-mono ml-auto rounded-md border border-[var(--st-edge)] px-1.5 py-0.5 text-[10.5px]">Ctrl K</kbd>
      </button>
      <div className="flex items-center gap-1.5">
        {view && onView && !compact && <ViewSwitch view={view} onView={onView} />}
        <button
          title="Reset this org's layout"
          onClick={onReset}
          hidden={compact}
          className="h-9 whitespace-nowrap rounded-xl border border-[var(--st-edge)] bg-[var(--st-glass)] px-3 text-[12px] text-[var(--st-ink-2)] hover:text-[var(--st-ink)]"
        >
          Reset layout
        </button>
        {org && (
          <button
            title={paused ? "Resume all agents" : "Pause all agents (steps in flight finish)"}
            onClick={() => api.updateOrg(org.id, { status: paused ? "running" : "paused" }).then((o) => useOrg.getState().setOrg(o))}
            className="flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px]"
            style={{
              borderColor: paused ? "rgba(61,220,151,0.4)" : "var(--st-edge)",
              color: paused ? "var(--st-green)" : "var(--st-ink-2)",
              background: "var(--st-glass)",
            }}
          >
            <Icon name={paused ? "play" : "pause"} size={13} /> {paused ? "Resume" : "Pause"}
          </button>
        )}
        <div className="studio-page">
          <InboxButton orgId={orgId} />
        </div>
      </div>
    </div>
  );
}
