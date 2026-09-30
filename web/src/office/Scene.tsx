"use client";

import { Html, OrbitControls, QuadraticBezierLine } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { Agent, RuntimeStatus, Task, WhiteboardInfo } from "@/lib/types";
import { liveFor, type Beam, type LiveMeeting, type OrgState } from "@/store/reduce";
import { applyIntent, assignMeetingRooms, intentFor, newMotion, step, type Motion } from "./brain";
import { resolveDesign, type OfficeDesign, type ResolvedOffice } from "./design";
import { DesignerLayer } from "./DesignerLayer";
import { Lighting, OfficeShell, TaskBoards, Whiteboards } from "./Environment";
import { FurnitureLayer } from "./Furniture";
import { WorldObjects } from "./WorldObjects";
import { buildGrid } from "./pathfinding";
import { characterFor, Person } from "./Person";

export type MotionRegistry = Map<string, Motion>;

const SCREEN: Record<RuntimeStatus, string> = {
  idle: "#27303f",
  working: "#7c6cff",
  awaiting_approval: "#f5b454",
  error: "#f2667a",
  retrying: "#f5b454",
  cooling_down: "#5cc8ff",
};

export interface SceneProps {
  design: OfficeDesign;
  editing: boolean;
  agents: Agent[];
  state: Pick<OrgState, "live" | "meetings" | "beams" | "agents" | "objects">;
  tasks: Task[];
  selectedId: string | null;
  focusId: string | null;
  onSelect: (id: string | null) => void;
  onBoardClick: () => void;
  whiteboards?: WhiteboardInfo[];
  onWhiteboardClick?: (boardId: string) => void;
}

export function OfficeScene(props: SceneProps) {
  const { design, editing, agents, selectedId, onSelect } = props;
  const membership = agents.map((a) => `${a.id}:${a.team}:${a.createdAt}`).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const office = useMemo(() => resolveDesign(design, agents), [design, membership]);
  const grid = useMemo(() => buildGrid(office), [office]);
  const [motions] = useState<MotionRegistry>(() => new Map());
  const [dragging, setDragging] = useState(false);

  for (const a of agents) {
    if (!motions.has(a.id)) {
      const desk = office.desks.find((d) => d.agentId === a.id);
      motions.set(a.id, newMotion(desk?.seat ?? [office.width / 2, office.depth / 2], desk?.facing ?? 0));
    }
  }

  return (
    <>
      <Lighting width={office.width} depth={office.depth} />
      <OfficeShell design={office.design} />
      {editing ? (
        <DesignerLayer design={design} onDragging={setDragging} />
      ) : (
        <>
          <FurnitureLayer items={office.design.items} />
          <Monitors office={office} state={props.state} />
          <Director office={office} grid={grid} motions={motions} agents={agents} state={props.state} />
          {agents.map((a) => (
            <AgentFigure
              key={a.id}
              agent={a}
              motion={motions.get(a.id)!}
              live={liveFor(props.state, a.id)}
              meeting={Object.values(props.state.meetings).find((m) => m.participants.includes(a.id))}
              selected={selectedId === a.id}
              dim={a.status !== "active"}
              onSelect={() => onSelect(a.id)}
            />
          ))}
          <Beams beams={props.state.beams} motions={motions} />
          <WorldObjects objects={props.state.objects} office={office} motions={motions} />
        </>
      )}
      <TaskBoards items={office.design.items} tasks={props.tasks} onClick={editing ? undefined : props.onBoardClick} />
      <Whiteboards items={office.design.items} boards={props.whiteboards ?? []} onClick={editing ? undefined : props.onWhiteboardClick} />
      <CameraRig office={office} focusId={editing ? null : props.focusId} motions={motions} editing={editing} locked={dragging} />
    </>
  );
}

/** Recomputes each agent's intent a few times a second; motion runs every frame. */
function Director({
  office,
  grid,
  motions,
  agents,
  state,
}: {
  office: ResolvedOffice;
  grid: ReturnType<typeof buildGrid>;
  motions: MotionRegistry;
  agents: Agent[];
  state: SceneProps["state"];
}) {
  const acc = useRef(1);
  const stateRef = useRef(state);
  stateRef.current = state;
  const officeRef = useRef(office);
  if (officeRef.current !== office) {
    officeRef.current = office;
    for (const m of motions.values()) m.intentKey = ""; // floor plan changed: re-plan
  }
  useFrame((_, dt) => {
    acc.current += dt;
    const replan = acc.current > 0.25;
    if (replan) acc.current = 0;
    const now = performance.now();
    const meetings = Object.values(stateRef.current.meetings);
    const seats = replan ? assignMeetingRooms(office, meetings) : null;
    for (const a of agents) {
      const m = motions.get(a.id);
      if (!m) continue;
      if (replan && seats) {
        const live = liveFor(stateRef.current, a.id);
        if (live.status === "idle" && !meetings.some((x) => x.participants.includes(a.id))) {
          if (!m.idleSince) m.idleSince = now;
          if (!m.wander && now - m.idleSince > 45000 && Math.random() < 0.004) {
            m.wander = { until: now + 20000 + Math.random() * 25000, spot: Math.floor(Math.random() * 64) };
          }
          if (m.wander && now > m.wander.until) {
            m.wander = null;
            m.idleSince = now;
          }
        } else {
          m.idleSince = 0;
          m.wander = null;
        }
        let intent = intentFor(a.id, live, office, meetings, seats, m, now, stateRef.current.agents[a.id]?.location, motions);
        // A deliberate gesture (the emote tool) plays for a moment wherever they are.
        if (live.emote && live.emoteAt && Date.now() - live.emoteAt < 2600) {
          intent = { ...intent, emote: live.emote, loopEmote: false };
          m.emoteUntil = now + 2600 - (Date.now() - live.emoteAt);
        }
        applyIntent(m, intent, grid, now);
      }
      step(m, dt);
    }
  });
  return null;
}

const AgentFigure = memo(function AgentFigure({
  agent,
  motion,
  live,
  meeting,
  selected,
  dim,
  onSelect,
}: {
  agent: Agent;
  motion: Motion;
  live: ReturnType<typeof liveFor>;
  meeting: LiveMeeting | undefined;
  selected: boolean;
  dim: boolean;
  onSelect: () => void;
}) {
  const bubble = bubbleFor(live, meeting, agent.id);
  const accent = agent.avatar.outfit ?? "#6d5dfc";
  return (
    <Person
      motion={motion}
      character={characterFor(agent)}
      outfit={agent.avatar.outfit ?? "#6d5dfc"}
      accent={accent}
      selected={selected}
      dim={dim}
      onClick={onSelect}
    >
      <Html position={[0, 1.75, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
        <div className="flex select-none flex-col items-center gap-1" style={{ width: 220, transform: "translateY(-50%)" }}>
          {bubble && (
            <div
              className={
                "max-w-[210px] rounded-xl px-2.5 py-1.5 text-center text-[12px] leading-snug shadow-lg " +
                (bubble.tone === "warn"
                  ? "bg-[#f5b454] text-black"
                  : bubble.tone === "bad"
                    ? "bg-[#e5484d] text-white"
                    : bubble.tone === "accent"
                      ? "bg-white/95 text-[#3b2fb3] ring-1 ring-[#8b7cff]/60"
                      : "bg-white/95 text-[#1b1d22]")
              }
            >
              {bubble.text}
            </div>
          )}
          <div
            className="flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold text-white shadow"
            style={{ background: selected ? accent : "rgba(20,22,28,0.78)" }}
          >
            <span className="size-1.5 rounded-full" style={{ background: live.status === "idle" ? "#9aa3b2" : SCREEN[live.status] }} />
            {agent.name}
          </div>
        </div>
      </Html>
    </Person>
  );
});

function bubbleFor(
  live: ReturnType<typeof liveFor>,
  meeting: LiveMeeting | undefined,
  agentId: string,
): { text: string; tone: "warn" | "bad" | "accent" | "plain" } | null {
  const now = Date.now();
  if (meeting && meeting.speakerId === agentId && meeting.lastText) return { text: clip(meeting.lastText, 140), tone: "plain" };
  if (live.status === "awaiting_approval") return { text: `✋ Needs your approval: ${live.detail}`, tone: "warn" };
  // In a meeting only the speaker talks — keeps bubbles from piling up.
  if (meeting) return null;
  if (live.status === "error") return { text: "⚠ Stopped — needs attention", tone: "bad" };
  if (live.lastSaid && now - live.lastSaidAt < 7000) return { text: clip(live.lastSaid, 140), tone: "plain" };
  if (live.status === "working") {
    if (live.tool) return { text: `🛠 ${live.tool.replace(/^mcp__/, "").replace(/__/g, " · ")}`, tone: "accent" };
    if (live.stream.trim()) return { text: `✎ …${clip(live.stream.trim().slice(-110), 110)}`, tone: "accent" };
    return { text: "thinking…", tone: "accent" };
  }
  return null;
}

function clip(s: string, n: number) {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

/** Monitor screens glow in their owner's status color; working screens pulse. */
function Monitors({ office, state }: { office: ResolvedOffice; state: SceneProps["state"] }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const screens = useMemo(() => office.desks.filter((d) => d.itemId), [office]);
  const color = useMemo(() => new THREE.Color(), []);
  const stateRef = useRef(state);
  stateRef.current = state;
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const obj = new THREE.Object3D();
    screens.forEach((d, i) => {
      // Screen faces the sitter (opposite the sitter's facing).
      const back = d.facing + Math.PI;
      obj.position.set(d.screen[0] + Math.sin(back) * 0.07, d.screen[1], d.screen[2] + Math.cos(back) * 0.07);
      obj.rotation.set(0, back, 0);
      obj.updateMatrix();
      mesh.setMatrixAt(i, obj.matrix);
      mesh.setColorAt(i, color.set(SCREEN.idle));
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [screens, color]);
  useFrame(({ clock }) => {
    const mesh = ref.current;
    if (!mesh) return;
    const t = clock.elapsedTime;
    screens.forEach((d, i) => {
      const live = liveFor(stateRef.current, d.agentId);
      color.set(SCREEN[live.status] ?? SCREEN.idle);
      if (live.status === "working") color.multiplyScalar(0.8 + 0.2 * Math.sin(t * 5 + i));
      mesh.setColorAt(i, color);
    });
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  });
  return (
    <instancedMesh key={screens.length} ref={ref} args={[undefined, undefined, Math.max(1, screens.length)]} count={screens.length}>
      <planeGeometry args={[0.66, 0.4]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}

function Beams({ beams, motions }: { beams: Beam[]; motions: MotionRegistry }) {
  return (
    <>
      {beams.map((b) => (
        <BeamArc key={b.id} beam={b} motions={motions} />
      ))}
    </>
  );
}

function BeamArc({ beam, motions }: { beam: Beam; motions: MotionRegistry }) {
  const from = motions.get(beam.from);
  const to = motions.get(beam.to);
  const dot = useRef<THREE.Mesh>(null);
  const start = useMemo(() => new THREE.Vector3(), []);
  const end = useMemo(() => new THREE.Vector3(), []);
  const mid = useMemo(() => new THREE.Vector3(), []);
  const curve = useMemo(() => new THREE.QuadraticBezierCurve3(start, mid, end), [start, mid, end]);
  const color = beam.kind === "task" ? "#e0902a" : "#6d5dfc";
  const compute = () => {
    if (!from || !to) return;
    start.set(from.pos[0], 1.5, from.pos[1]);
    end.set(to.pos[0], 1.5, to.pos[1]);
    mid.copy(start).add(end).multiplyScalar(0.5);
    mid.y += 1.4 + start.distanceTo(end) * 0.15;
  };
  compute();
  useFrame(() => {
    compute();
    const t = Math.min(1, (Date.now() - beam.at) / 1400);
    if (dot.current) {
      dot.current.position.copy(curve.getPoint(t));
      dot.current.visible = t < 1;
    }
  });
  if (!from || !to) return null;
  return (
    <group>
      <QuadraticBezierLine start={start} end={end} mid={mid} color={color} lineWidth={2} transparent opacity={0.7} dashed dashScale={6} />
      <mesh ref={dot}>
        <sphereGeometry args={[0.1, 12, 12]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
    </group>
  );
}

function CameraRig({
  office,
  focusId,
  motions,
  editing,
  locked,
}: {
  office: ResolvedOffice;
  focusId: string | null;
  motions: MotionRegistry;
  editing: boolean;
  locked: boolean;
}) {
  const controls = useRef<OrbitControlsImpl>(null);
  const { camera } = useThree();
  const target = useMemo(() => new THREE.Vector3(), []);
  useEffect(() => {
    const pinned = new URLSearchParams(window.location.search).get("cam")?.split(",").map(Number);
    const span = Math.max(office.width, office.depth);
    if (pinned && pinned.length === 6 && pinned.every(Number.isFinite)) {
      camera.position.set(pinned[0], pinned[1], pinned[2]);
      controls.current?.target.set(pinned[3], pinned[4], pinned[5]);
    } else if (editing) {
      camera.position.set(office.width / 2, span * 1.05, office.depth / 2 + span * 0.18);
      controls.current?.target.set(office.width / 2, 0, office.depth / 2);
    } else {
      camera.position.set(office.width * 0.62, span * 0.52, office.depth + span * 0.42);
      controls.current?.target.set(office.width * 0.46, 0, office.depth * 0.46);
    }
    controls.current?.update();
  }, [office.width, office.depth, editing, camera]);
  useFrame(() => {
    const c = controls.current;
    if (!c) return;
    const m = focusId ? motions.get(focusId) : null;
    if (m) {
      target.set(m.pos[0], 0.8, m.pos[1]);
      c.target.lerp(target, 0.06);
      c.update();
    }
  });
  const span = Math.max(office.width, office.depth);
  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enabled={!locked}
      enableDamping
      dampingFactor={0.1}
      maxPolarAngle={editing ? Math.PI / 3 : Math.PI / 2.3}
      minDistance={3}
      maxDistance={span * 2.2}
      mouseButtons={editing ? { LEFT: undefined as unknown as THREE.MOUSE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN } : undefined}
    />
  );
}
