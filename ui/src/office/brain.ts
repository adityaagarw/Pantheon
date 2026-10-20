/**
 * The office "brain": decides where each agent should be and what it should
 * be doing (sit / stand / emote) from its live runtime state, and owns the
 * per-frame motion state (outside React, mutated in useFrame).
 */

import type { AgentLocation } from "@/lib/types";
import type { AgentLive, LiveMeeting } from "@/store/reduce";
import { CATALOG, toWorld } from "./catalog";
import type { ResolvedOffice, Spot } from "./design";
import { findPath, type Grid } from "./pathfinding";

export type Pose = "sit" | "stand";
export type Emote = "wave" | "no" | "yes" | "thumbs" | null;

export interface Intent {
  key: string; // changes whenever the destination changes
  target: [number, number];
  facing: number;
  pose: Pose;
  emote: Emote;
  loopEmote: boolean;
}

export interface Motion {
  pos: [number, number];
  heading: number; // radians, 0 = facing +z
  path: [number, number][];
  intentKey: string;
  pose: Pose;
  emote: Emote;
  loopEmote: boolean;
  facing: number;
  moving: boolean;
  idleSince: number;
  wander: { until: number; spot: number } | null;
  emoteUntil: number; // a deliberate gesture ends at this time (performance.now)
}

export const WALK_SPEED = 1.6; // m/s

/** Which meeting room (and seats) each live meeting uses. */
export function assignMeetingRooms(office: ResolvedOffice, meetings: LiveMeeting[]): Map<string, Spot[]> {
  const out = new Map<string, Spot[]>();
  const used = new Set<string>();
  const rooms = office.meetingRooms;
  for (const m of [...meetings].sort((a, b) => a.id.localeCompare(b.id))) {
    const wanted = m.room ? rooms.find((r) => r.room.name.toLowerCase() === m.room!.toLowerCase()) : undefined;
    const pick =
      (wanted && !used.has(wanted.room.id) ? wanted : undefined) ??
      rooms.find((r) => !used.has(r.room.id) && r.seats.length >= m.participants.length) ??
      rooms.find((r) => !used.has(r.room.id)) ??
      rooms[0];
    if (!pick) continue;
    used.add(pick.room.id);
    out.set(m.id, pick.seats);
  }
  return out;
}

const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

/** Where an explicit location puts an agent (null if it can't be resolved in this office). */
export function locationIntent(
  agentId: string,
  loc: AgentLocation,
  office: ResolvedOffice,
  motions: Map<string, Motion>,
): Intent | null {
  if (loc.kind === "agent") {
    const other = motions.get(loc.agentId);
    if (!other) return null;
    // Stand next to them, on my side, facing them.
    const me = motions.get(agentId);
    const [ox, oz] = other.pos;
    const ang = me ? Math.atan2(me.pos[0] - ox, me.pos[1] - oz) : (hash(agentId) % 628) / 100;
    const target: [number, number] = [ox + Math.sin(ang) * 0.95, oz + Math.cos(ang) * 0.95];
    const key = `agent:${loc.agentId}:${Math.round(ox)}:${Math.round(oz)}`;
    return { key, target, facing: ang + Math.PI, pose: "stand", emote: null, loopEmote: false };
  }
  if (loc.kind === "item") {
    const item = office.design.items.find((i) => i.id === loc.itemId) ?? office.design.items.find((i) => i.kind === loc.itemKind && roomHas(office, loc.room, i.x, i.z));
    if (item) {
      const e = CATALOG[item.kind];
      if (e?.seats.length) {
        const s = e.seats[hash(agentId) % e.seats.length];
        return { key: `item:${item.id}`, target: toWorld(item, s.x, s.z), facing: s.facing + item.rot, pose: "sit", emote: null, loopEmote: false };
      }
      const depth = e?.size[1] ?? 0.5;
      return { key: `item:${item.id}`, target: toWorld(item, 0, depth / 2 + 0.55), facing: item.rot + Math.PI, pose: "stand", emote: null, loopEmote: false };
    }
  }
  const room = "room" in loc ? loc.room : "";
  const spots = office.roomSpots[room.toLowerCase()];
  if (!spots?.spots.length) return null;
  const i = hash(agentId) % spots.spots.length;
  const s = spots.spots[i];
  return { key: `room:${room}:${i}`, target: s.pos, facing: s.facing, pose: spots.seated ? "sit" : "stand", emote: null, loopEmote: false };
}

function roomHas(office: ResolvedOffice, name: string, x: number, z: number): boolean {
  const r = office.design.rooms.find((rm) => rm.name.toLowerCase() === name.toLowerCase());
  return !!r && x >= r.x && x <= r.x + r.w && z >= r.z && z <= r.z + r.d;
}

export function intentFor(
  agentId: string,
  live: AgentLive,
  office: ResolvedOffice,
  meetings: LiveMeeting[],
  meetingSeats: Map<string, Spot[]>,
  motion: Motion | undefined,
  now: number,
  location?: AgentLocation | null,
  motions?: Map<string, Motion>,
): Intent {
  const desk = office.desks.find((d) => d.agentId === agentId);
  const deskSeat: [number, number] = desk?.seat ?? [office.width / 2, office.depth / 2];
  const deskFacing = desk?.facing ?? 0;

  const meeting = meetings.find((m) => m.participants.includes(agentId));
  const seats = meeting ? meetingSeats.get(meeting.id) : undefined;
  if (meeting && seats?.length) {
    const i = meeting.participants.indexOf(agentId);
    const seat = seats[i % seats.length];
    return {
      key: `meeting:${meeting.id}:${i}`,
      target: seat.pos,
      facing: seat.facing,
      pose: i < seats.length ? "sit" : "stand",
      emote: meeting.speakerId === agentId ? "yes" : null,
      loopEmote: false,
    };
  }
  // Somewhere they chose (or were sent) to be: stay there, whatever they're doing.
  const there = location && motions ? locationIntent(agentId, location, office, motions) : null;
  if (there) {
    if (live.status === "awaiting_approval") return { ...there, pose: "stand", emote: "wave", loopEmote: true };
    return there;
  }
  const stand: [number, number] = desk?.stand ?? deskSeat;
  if (live.status === "awaiting_approval") {
    return { key: "approval", target: stand, facing: deskFacing + Math.PI, pose: "stand", emote: "wave", loopEmote: true };
  }
  if (live.status === "error") {
    return { key: "error", target: stand, facing: deskFacing + Math.PI, pose: "stand", emote: "no", loopEmote: false };
  }
  if (live.status === "working" || live.status === "retrying") {
    return { key: "desk", target: deskSeat, facing: deskFacing, pose: "sit", emote: null, loopEmote: false };
  }
  // Idle: sit at the desk; after a while, take a break in the lounge/kitchen.
  if (motion?.wander && now < motion.wander.until && office.loungeSpots.length) {
    const spot = office.loungeSpots[motion.wander.spot % office.loungeSpots.length];
    return { key: `break:${motion.wander.spot}`, target: spot.pos, facing: spot.facing, pose: "sit", emote: null, loopEmote: false };
  }
  return { key: "desk", target: deskSeat, facing: deskFacing, pose: "sit", emote: null, loopEmote: false };
}

export function newMotion(pos: [number, number], facing = 0): Motion {
  return {
    pos: [...pos],
    heading: facing,
    path: [],
    intentKey: "",
    pose: "sit",
    emote: null,
    loopEmote: false,
    facing,
    moving: false,
    idleSince: 0,
    wander: null,
    emoteUntil: 0,
  };
}

/** Apply an intent: re-plan the path if the destination changed. */
export function applyIntent(m: Motion, intent: Intent, grid: Grid, now = performance.now()): void {
  if (!intent.emote && m.emoteUntil && now > m.emoteUntil) {
    m.emoteUntil = 0;
    if (!m.loopEmote) m.emote = null;
  }
  if (m.intentKey !== intent.key) {
    m.intentKey = intent.key;
    const d = Math.hypot(intent.target[0] - m.pos[0], intent.target[1] - m.pos[1]);
    m.path = d > 0.08 ? findPath(grid, m.pos, intent.target).slice(1) : [];
    m.emote = intent.emote;
  } else if (intent.emote && intent.emote !== m.emote) {
    m.emote = intent.emote;
  }
  m.pose = intent.pose;
  m.loopEmote = intent.loopEmote;
  m.facing = intent.facing;
}

/** Advance along the path. Returns true while walking. */
export function step(m: Motion, dt: number): boolean {
  if (!m.path.length) {
    m.moving = false;
    m.heading = turnToward(m.heading, m.facing, dt * 6);
    return false;
  }
  m.moving = true;
  let budget = WALK_SPEED * Math.min(dt, 0.1);
  while (budget > 0 && m.path.length) {
    const [tx, tz] = m.path[0];
    const dx = tx - m.pos[0];
    const dz = tz - m.pos[1];
    const dist = Math.hypot(dx, dz);
    if (dist <= budget) {
      m.pos = [tx, tz];
      budget -= dist;
      m.path.shift();
    } else {
      m.pos = [m.pos[0] + (dx / dist) * budget, m.pos[1] + (dz / dist) * budget];
      m.heading = turnToward(m.heading, Math.atan2(dx, dz), dt * 10);
      budget = 0;
    }
  }
  return true;
}

function turnToward(cur: number, want: number, maxStep: number): number {
  let d = want - cur;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return cur + Math.max(-maxStep, Math.min(maxStep, d));
}
