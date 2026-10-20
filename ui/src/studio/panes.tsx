/**
 * Every view in an org is a pane the dock can hold (docs/DESIGN.md). The Console and
 * Activity panes are the Studio's own; the rest are Pantheon 1's pages,
 * which read the org from the route as before.
 */
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { ActivityFeed } from "@/components/ActivityFeed";
import { AgentInspector } from "@/components/AgentInspector";
import { useOrg } from "@/store/org";
import { Icon, type IconName } from "./icons";
import { agentHue, hsl } from "./look";
import { HumanSeat } from "./HumanSeat";
import { useStudio } from "./store";
import { FlowPane } from "./FlowPane";
import { WorktreesPane } from "./WorktreesPane";

export interface PaneDef {
  id: string;
  title: string;
  icon: IconName;
  hint: string;
  render: () => ReactNode;
}

const page = (load: () => Promise<{ default: ComponentType }>) => {
  const Page = lazy(load);
  return function Wrapped() {
    return (
      <Suspense fallback={<div className="p-6 text-sm text-[var(--st-ink-3)]">Loading…</div>}>
        <div className="studio-page h-full overflow-auto">
          <Page />
        </div>
      </Suspense>
    );
  };
};

const Office = page(() => import("../app/org/[orgId]/page"));
const Stage = page(() => import("../app/org/[orgId]/stage/page"));
const Board = page(() => import("../app/org/[orgId]/board/page"));
const Comms = page(() => import("../app/org/[orgId]/comms/page"));
const Sessions = page(() => import("../app/org/[orgId]/sessions/page"));
const Memory = page(() => import("../app/org/[orgId]/memory/page"));
const Shadow = page(() => import("../app/org/[orgId]/shadow/page"));
const Team = page(() => import("../app/org/[orgId]/team/page"));
const Files = page(() => import("../app/org/[orgId]/files/page"));
const Settings = page(() => import("../app/org/[orgId]/settings/page"));

/** The selected agent's console: its chat, live thinking, tasks and approvals. */
function ConsolePane() {
  const agentId = useStudio((s) => s.agentId);
  const select = useStudio((s) => s.select);
  const agent = useOrg((s) => (agentId ? s.agents[agentId] : undefined));
  const agents = useOrg((s) => s.agents);
  if (!agentId || !agent) {
    const list = Object.values(agents).filter((a) => !a.isSupervisor && !a.metaRole);
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="text-sm text-[var(--st-ink-3)]">Pick someone in the Office, or here.</div>
        <div className="flex max-w-md flex-wrap justify-center gap-2">
          {list.map((a) => (
            <button
              key={a.id}
              onClick={() => select(a.id)}
              className="rounded-full border px-3 py-1 text-[13px] transition-colors hover:bg-white/5"
              style={{ borderColor: hsl(agentHue(a), 70, 60, 0.4), color: hsl(agentHue(a), 70, 82) }}
            >
              {a.name}
            </button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="studio-page flex h-full flex-col overflow-hidden">
      {agent.kind === "human" && <HumanSeat agent={agent} />}
      <div className="min-h-0 flex-1">
        <AgentInspector key={agentId} agentId={agentId} onClose={() => select(null)} />
      </div>
    </div>
  );
}

function ActivityPane() {
  const select = useStudio((s) => s.select);
  return (
    <div className="studio-page h-full overflow-auto">
      <ActivityFeed onSelectAgent={select} limit={300} />
    </div>
  );
}

export const PANES: PaneDef[] = [
  { id: "office", title: "Office", icon: "office", hint: "The 3D office", render: () => <Office /> },
  { id: "console", title: "Console", icon: "chat", hint: "Talk to the selected agent", render: () => <ConsolePane /> },
  { id: "activity", title: "Activity", icon: "live", hint: "Everything happening, as it happens", render: () => <ActivityPane /> },
  { id: "stage", title: "Stage", icon: "stage", hint: "Lessons, documents and live views", render: () => <Stage /> },
  { id: "board", title: "Board", icon: "board", hint: "Tasks", render: () => <Board /> },
  { id: "comms", title: "Comms", icon: "comms", hint: "Channels and messages", render: () => <Comms /> },
  { id: "sessions", title: "Sessions", icon: "sessions", hint: "Turns, model and tool calls", render: () => <Sessions /> },
  { id: "memory", title: "Memory", icon: "memory", hint: "What the org remembers", render: () => <Memory /> },
  { id: "files", title: "Files", icon: "files", hint: "The workspace", render: () => <Files /> },
  { id: "flows", title: "Flows", icon: "canvas", hint: "Work as a graph of steps: design, run and watch", render: () => <FlowPane /> },
  { id: "worktrees", title: "Worktrees", icon: "branch", hint: "Each agent's branch, its files and changes; merge", render: () => <WorktreesPane /> },
  { id: "team", title: "Team", icon: "team", hint: "Agents and the org chart", render: () => <Team /> },
  { id: "shadow", title: "Shadow", icon: "shadow", hint: "Learn from what you do", render: () => <Shadow /> },
  { id: "settings", title: "Settings", icon: "settings", hint: "This org's settings", render: () => <Settings /> },
];

export const paneById = (id: string) => PANES.find((p) => p.id === id);

export function PaneIcon({ id, size = 14 }: { id: string; size?: number }) {
  const def = paneById(id);
  return def ? <Icon name={def.icon} size={size} /> : null;
}
