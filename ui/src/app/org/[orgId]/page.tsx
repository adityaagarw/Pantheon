"use client";

import { themeOf } from "@/office/themes";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ActivityFeed } from "@/components/ActivityFeed";
import { AgentAvatar, StatusDot } from "@/components/agent";
import { AgentInspector } from "@/components/AgentInspector";
import { MeetingDialog } from "@/components/MeetingDialog";
import { WhiteboardOverlay } from "@/components/WhiteboardOverlay";
import { Button, cx, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAssetVersion } from "@/office/assets";
import { generateDesign, isDesign, withExtras, type OfficeDesign } from "@/office/design";
import { CatalogPanel, DesignerToolbar, PropertiesPanel, useDesignerKeys } from "@/office/DesignerPanel";
import { useDesigner } from "@/office/designerStore";
import { useOffice, useOfficeReady } from "@/office/registry";
import { useOrg } from "@/store/org";
import { liveFor } from "@/store/reduce";
import { useReplay, useView } from "@/studio/replay";
import { useStudio } from "@/studio/store";

const ASIDE_KEY = "studio.office.aside";

function readAside(): boolean {
  try {
    return localStorage.getItem(ASIDE_KEY) !== "closed";
  } catch {
    return true;
  }
}

const OfficeCanvas = dynamic(() => import("@/office/OfficeCanvas"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-ink-3">
      <Spinner />
    </div>
  ),
});

export default function OfficePage() {
  const router = useRouter();
  const orgId = useOrg((s) => s.orgId);
  const org = useOrg((s) => s.org);
  const agentsMap = useOrg((s) => s.agents);
  // On the Timeline's time cursor the office shows that moment; otherwise it's live.
  const live = useView((s) => s.live);
  const meetings = useView((s) => s.meetings);
  const replaying = useStudio((s) => s.at !== null);
  const liveBeams = useOrg((s) => s.beams);
  const replayBeams = useReplay((r) => r.beams);
  const beams = replaying ? replayBeams : liveBeams;
  const tasksMap = useOrg((s) => s.tasks);
  const objects = useOrg((s) => s.objects);
  const assetVersion = useAssetVersion();
  // Furniture, characters and themes come from the office packs.
  const officeReady = useOfficeReady();
  const officeError = useOffice((s) => s.error);
  const officeVersion = useOffice((s) => s.version);
  const [selected, setSelectedLocal] = useState<string | null>(null);
  // The Studio's Console pane follows the person picked in the office.
  const setSelected = (id: string | null) => {
    setSelectedLocal(id);
    if (id) {
      setAside(true);
      useStudio.getState().select(id);
    }
  };
  const [aside, setAsideState] = useState(readAside);
  const setAside = (open: boolean) => {
    setAsideState(open);
    try {
      localStorage.setItem(ASIDE_KEY, open ? "open" : "closed");
    } catch {
      // remembered for this session only
    }
  };
  const [meetingOpen, setMeetingOpen] = useState(false);
  const [openBoard, setOpenBoard] = useState<string | null>(null);
  const boardsMap = useOrg((s) => s.whiteboards);
  const boards = useMemo(() => Object.values(boardsMap), [boardsMap]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const designing = useDesigner((s) => s.active);
  const draft = useDesigner((s) => s.draft);
  useDesignerKeys(designing);

  const agents = useMemo(() => Object.values(agentsMap).filter((a) => !a.isSupervisor), [agentsMap]);
  const tasks = useMemo(() => Object.values(tasksMap), [tasksMap]);
  const state = useMemo(() => ({ live, meetings, beams, agents: agentsMap, objects }), [live, meetings, beams, agentsMap, objects]);
  const membership = agents.map((a) => `${a.id}:${a.team}`).join("|");
  const saved = org?.layout?.office;
  const extras = org?.layout?.extras;
  const orgTheme = typeof org?.layout?.theme === "string" ? org.layout.theme : undefined;
  const maybeDesign: OfficeDesign | null = useMemo(() => {
    if (!officeReady) return null; // generating needs the packs' furniture
    const base = isDesign(saved) ? saved : generateDesign(agents, orgTheme);
    const themed = base.theme || !orgTheme ? base : { ...base, theme: orgTheme, floor: themeOf(orgTheme).floor };
    return withExtras(themed, extras);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved, membership, extras, assetVersion, orgTheme, officeVersion, officeReady]);
  const activeMeeting = Object.values(meetings)[0];

  if (!orgId || !org) return null;
  if (!maybeDesign) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-3">
        {officeError ? `The office couldn't load: ${officeError}` : <Spinner />}
      </div>
    );
  }
  const design = maybeDesign;
  if (agents.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <Empty title="This office is empty" icon="🏢">
          <div className="mt-2 flex flex-col items-center gap-3">
            Hire agents on the Team tab, or ask Zeus to design the organization for you.
            <div className="flex gap-2">
              <Link href={`/org/${orgId}/team`}>
                <Button variant="primary">Hire agents</Button>
              </Link>
              <Link href="/supervisor">
                <Button>Ask Zeus</Button>
              </Link>
            </div>
          </div>
        </Empty>
      </div>
    );
  }

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    try {
      const o = await api.updateOrg(org.id, { layout: { ...(org.layout ?? {}), office: draft } });
      useOrg.getState().setOrg(o);
      useDesigner.getState().markSaved();
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="relative flex h-full flex-col md:flex-row">
      <div
        className={cx(
          "relative min-w-0 shrink-0 md:h-auto md:flex-1",
          designing || !aside ? "flex-1" : selected ? "h-[32%]" : "h-[46%]",
        )}
      >
        <div className="absolute inset-0" style={{ background: themeOf((designing && draft ? draft : design).theme).sky }} />
        <OfficeCanvas
          design={designing && draft ? draft : design}
          editing={designing}
          agents={agents}
          state={state}
          tasks={tasks}
          selectedId={selected}
          focusId={selected}
          onSelect={setSelected}
          onBoardClick={() => router.push(`/org/${orgId}/board`)}
          whiteboards={boards}
          onWhiteboardClick={setOpenBoard}
        />

        {designing ? (
          <div className="pointer-events-none absolute inset-0 flex flex-col gap-3 p-3">
            <DesignerToolbar
              agents={agents}
              saving={saving}
              error={saveError}
              onSave={save}
              onCancel={() => useDesigner.getState().exit()}
            />
            <div className="flex min-h-0 flex-1 justify-between gap-3">
              <CatalogPanel />
              <div className="flex flex-col justify-start">
                <PropertiesPanel agents={agents} />
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="no-scrollbar absolute left-2 right-24 top-2 flex gap-1.5 overflow-x-auto md:pointer-events-none md:left-3 md:right-auto md:top-3 md:max-w-[70%] md:flex-wrap md:overflow-visible">
              {agents.map((a) => {
                const st = liveFor(state, a.id).status;
                return (
                  <button
                    key={a.id}
                    onClick={() => setSelected(a.id)}
                    className={cx(
                      "pointer-events-auto flex shrink-0 items-center gap-1.5 rounded-full border py-0.5 pl-0.5 pr-2.5 text-xs shadow-sm backdrop-blur transition-colors cursor-pointer",
                      selected === a.id ? "border-accent bg-accent text-white" : "border-black/10 bg-white/85 text-[#1b1d22] hover:bg-white",
                    )}
                  >
                    <AgentAvatar agent={a} size={20} />
                    {a.name}
                    <StatusDot status={st} />
                  </button>
                );
              })}
            </div>
            <div className="absolute right-2 top-2 flex gap-2 md:right-3 md:top-3">
              <Button size="sm" title="Call a meeting" className="!border-black/10 !bg-white/90 !text-[#1b1d22] hover:!bg-white" onClick={() => setMeetingOpen(true)}>
                ◎<span className="hidden md:inline"> Call a meeting</span>
              </Button>
              {/* The designer needs a mouse and room for its panels. */}
              <Button
                size="sm"
                className="!hidden !border-black/10 !bg-white/90 !text-[#1b1d22] hover:!bg-white md:!inline-flex"
                onClick={() => useDesigner.getState().begin(design)}
              >
                ✎ Design office
              </Button>
              {!aside && (
                <Button
                  size="sm"
                  title="Show live activity"
                  className="!border-black/10 !bg-white/90 !text-[#1b1d22] hover:!bg-white"
                  onClick={() => setAside(true)}
                >
                  ◧<span className="hidden md:inline"> Activity</span>
                </Button>
              )}
            </div>
            {activeMeeting && (
              <div className="absolute bottom-2 left-1/2 w-[min(640px,94%)] -translate-x-1/2 md:bottom-4 rounded-xl border border-accent/40 bg-panel/95 px-4 py-3 shadow-2xl backdrop-blur">
                <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-accent">
                  <span className="size-2 rounded-full bg-accent pulse-ring" /> {activeMeeting.style ?? "meeting"} in progress
                </div>
                <div className="mt-1 text-sm font-medium">{activeMeeting.agenda}</div>
                {activeMeeting.speakerId && (
                  <div className="mt-2 flex gap-2 text-sm text-ink-2">
                    <AgentAvatar agent={agentsMap[activeMeeting.speakerId]} size={20} />
                    <span className="line-clamp-2">{activeMeeting.lastText}</span>
                  </div>
                )}
              </div>
            )}
            <div className="absolute bottom-3 right-3 hidden rounded bg-white/70 md:block px-2 py-0.5 text-[11px] text-[#4a4f5a]">Drag to orbit · scroll to zoom · click a person</div>
          </>
        )}
      </div>

      {!designing && aside && (
        <aside className="flex min-h-0 w-full flex-1 flex-col border-t border-line bg-panel md:w-[400px] md:flex-none md:shrink-0 md:border-l md:border-t-0">
          {selected ? (
            <AgentInspector key={selected} agentId={selected} onClose={() => setSelected(null)} />
          ) : (
            <>
              <div className="flex items-center border-b border-line px-4 py-3 text-sm font-semibold">
                Live activity
                <button
                  title="Hide (the office gets the room)"
                  onClick={() => setAside(false)}
                  className="ml-auto grid size-6 place-items-center rounded-md text-ink-3 hover:bg-white/5 hover:text-ink"
                >
                  ⇥
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <ActivityFeed onSelectAgent={setSelected} />
              </div>
            </>
          )}
        </aside>
      )}
      {openBoard && <WhiteboardOverlay boardId={openBoard} onClose={() => setOpenBoard(null)} />}
      <MeetingDialog open={meetingOpen} onClose={() => setMeetingOpen(false)} rooms={design.rooms.filter((r) => r.type === "meeting").map((r) => r.name)} />
    </div>
  );
}
