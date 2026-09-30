/**
 * Pure event reducer for an organization's live state.
 * Every realtime view (office, board, comms, sessions, feed) reads from this.
 */

import type {
  Agent,
  Approval,
  Channel,
  Message,
  PantheonEvent,
  Relationship,
  RuntimeStatus,
  Task,
  StagePageInfo,
  WhiteboardInfo,
  WorldObject,
} from "@/lib/types";

export interface Beam {
  id: string;
  from: string;
  to: string;
  kind: "message" | "task";
  at: number;
}

export interface LiveMeeting {
  id: string;
  agenda: string;
  facilitatorId: string;
  participants: string[];
  speakerId: string | null;
  lastText: string;
  room?: string | null;
  style?: string;
}

export interface AgentLive {
  status: RuntimeStatus;
  detail: string;
  turnId: string | null;
  stream: string; // text the model is currently producing
  thinking: string; // the model's reasoning for the current step, as it streams
  tool: string | null; // tool currently running
  lastSaid: string; // latest thing it said/posted (for bubbles)
  lastSaidAt: number;
  emote?: "wave" | "yes" | "no" | "thumbs" | null; // a deliberate gesture
  emoteAt?: number;
}

export interface FeedItem {
  id: string;
  ts: number;
  agentId: string | null;
  kind: string;
  text: string;
  ref?: string;
}

export interface OrgState {
  agents: Record<string, Agent>;
  relationships: Relationship[];
  channels: Record<string, Channel>;
  tasks: Record<string, Task>;
  approvals: Record<string, Approval>;
  messagesByChannel: Record<string, Message[]>;
  live: Record<string, AgentLive>;
  meetings: Record<string, LiveMeeting>;
  objects: Record<string, WorldObject>;
  stagePages: Record<string, StagePageInfo>;
  whiteboards: Record<string, WhiteboardInfo>;
  browsers: Record<string, { url: string; title: string; at: number }>;
  computerAt: number; // last computer action (for the live view)
  beams: Beam[];
  feed: FeedItem[];
  turnsVersion: number;
  inboxVersion: number;
  artifactsVersion: number;
  channelsVersion: number;
  attachmentsVersion: number; // bumps when any uploaded file is added, processed or removed
}

export const emptyState = (): OrgState => ({
  agents: {},
  relationships: [],
  channels: {},
  tasks: {},
  approvals: {},
  messagesByChannel: {},
  live: {},
  meetings: {},
  objects: {},
  stagePages: {},
  whiteboards: {},
  browsers: {},
  computerAt: 0,
  beams: [],
  feed: [],
  turnsVersion: 0,
  inboxVersion: 0,
  artifactsVersion: 0,
  channelsVersion: 0,
  attachmentsVersion: 0,
});

const FEED_MAX = 400;
const BEAM_TTL = 2600;

export function liveFor(s: Pick<OrgState, "live"> & Partial<Pick<OrgState, "agents">>, agentId: string): AgentLive {
  return (
    s.live[agentId] ?? {
      status: s.agents?.[agentId]?.runtimeStatus ?? "idle",
      detail: s.agents?.[agentId]?.runtimeDetail ?? "",
      turnId: null,
      stream: "",
      thinking: "",
      tool: null,
      lastSaid: "",
      lastSaidAt: 0,
    }
  );
}

function setLive(s: OrgState, agentId: string, patch: Partial<AgentLive>): OrgState {
  return { ...s, live: { ...s.live, [agentId]: { ...liveFor(s, agentId), ...patch } } };
}

function pushFeed(s: OrgState, item: FeedItem): OrgState {
  const feed = [item, ...s.feed];
  if (feed.length > FEED_MAX) feed.length = FEED_MAX;
  return { ...s, feed };
}

function name(s: OrgState, id: string | null | undefined): string {
  if (!id) return "someone";
  if (id === "user") return "You";
  if (id === "system") return "System";
  return s.agents[id]?.name ?? id;
}

export function reduce(s: OrgState, e: PantheonEvent, now = Date.now()): OrgState {
  const p = e.payload as Record<string, unknown>;
  const aid = e.agentId;
  const fid = `${e.seq ?? "x"}-${e.type}-${e.ts}`;
  switch (e.type) {
    case "agent.status": {
      if (!aid) return s;
      const status = p.status as RuntimeStatus;
      const detail = String(p.detail ?? "");
      const next = setLive(s, aid, {
        status,
        detail,
        turnId: (p.turnId as string) ?? liveFor(s, aid).turnId,
        tool: detail.startsWith("using ") ? detail.slice(6) : status === "working" ? liveFor(s, aid).tool : null,
        stream: status === "working" ? liveFor(s, aid).stream : "",
      });
      const agent = s.agents[aid];
      const agents = agent ? { ...next.agents, [aid]: { ...agent, runtimeStatus: status, runtimeDetail: detail } } : next.agents;
      let out = { ...next, agents };
      if (e.seq !== null && ["error", "awaiting_approval", "retrying", "cooling_down"].includes(status)) {
        out = pushFeed(out, {
          id: fid,
          ts: e.ts,
          agentId: aid,
          kind: status,
          text:
            status === "awaiting_approval"
              ? `${name(s, aid)} is waiting for your approval (${detail})`
              : status === "error"
                ? `${name(s, aid)} stopped: ${detail}`
                : status === "retrying"
                  ? `${name(s, aid)} will retry: ${detail}`
                  : `${name(s, aid)} is cooling down: ${detail}`,
        });
      }
      return out;
    }
    case "agent.stream": {
      if (!aid) return s;
      const cur = liveFor(s, aid);
      const sameTurn = cur.turnId === p.turnId;
      const stream = ((sameTurn ? cur.stream : "") + String(p.delta ?? "")).slice(-4000);
      return setLive(s, aid, { stream, turnId: (p.turnId as string) ?? cur.turnId, status: "working" });
    }
    case "agent.thinking": {
      if (!aid) return s;
      const cur = liveFor(s, aid);
      const sameTurn = cur.turnId === p.turnId;
      const thinking = ((sameTurn ? cur.thinking : "") + String(p.delta ?? "")).slice(-6000);
      return setLive(s, aid, { thinking, turnId: (p.turnId as string) ?? cur.turnId, status: "working" });
    }
    case "agent.thought":
      if (!aid) return s;
      return pushFeed(setLive(s, aid, { lastSaid: String(p.text ?? ""), lastSaidAt: now }), {
        id: fid,
        ts: e.ts,
        agentId: aid,
        kind: "thought",
        text: String(p.text ?? ""),
      });
    case "turn.started":
      if (!aid) return s;
      return {
        ...setLive(s, aid, { turnId: String(p.turnId), stream: "", thinking: "", tool: null, status: "working" }),
        turnsVersion: s.turnsVersion + 1,
      };
    case "turn.completed":
    case "turn.failed":
    case "turn.stopped": {
      if (!aid) return s;
      let out: OrgState = { ...setLive(s, aid, { stream: "", thinking: "", tool: null }), turnsVersion: s.turnsVersion + 1 };
      if (e.type === "turn.failed")
        out = pushFeed(out, { id: fid, ts: e.ts, agentId: aid, kind: "error", text: `${name(s, aid)}'s turn failed: ${String(p.error ?? "")}` });
      return out;
    }
    case "tool.started":
      if (!aid) return s;
      return pushFeed(setLive(s, aid, { tool: String(p.tool), stream: "" }), {
        id: fid,
        ts: e.ts,
        agentId: aid,
        kind: "tool",
        text: `${name(s, aid)} → ${String(p.tool)}${summarizeArgs(p.args)}`,
        ref: String(p.toolCallId ?? ""),
      });
    case "tool.completed": {
      if (!aid) return s;
      const out = setLive(s, aid, { tool: null });
      if (p.status !== "ok")
        return pushFeed(out, {
          id: fid,
          ts: e.ts,
          agentId: aid,
          kind: "tool_error",
          text: `${String(p.tool)} ${p.status}: ${String(p.preview ?? "").slice(0, 160)}`,
        });
      return out;
    }
    case "message.created": {
      const m = p as unknown as Message;
      let out: OrgState = m.channelId && !s.channels[m.channelId] ? { ...s, channelsVersion: s.channelsVersion + 1 } : s;
      if (m.channelId) {
        const list = s.messagesByChannel[m.channelId];
        if (list && !list.some((x) => x.id === m.id))
          out = { ...out, messagesByChannel: { ...out.messagesByChannel, [m.channelId]: [...list, m] } };
      }
      if (m.senderType === "agent") {
        out = setLive(out, m.senderId, { lastSaid: m.content, lastSaidAt: now });
        const ch = m.channelId ? s.channels[m.channelId] : undefined;
        const targets = ch ? ch.members.filter((x) => x !== m.senderId && x !== "user" && s.agents[x]) : [];
        if (targets.length && m.kind !== "meeting") {
          const beams = [
            ...s.beams.filter((b) => now - b.at < BEAM_TTL),
            ...targets.slice(0, 6).map((t, i) => ({ id: `${m.id}-${i}`, from: m.senderId, to: t, kind: "message" as const, at: now })),
          ];
          out = { ...out, beams };
        }
      }
      if (m.kind === "meeting") return out;
      const ch = m.channelId ? s.channels[m.channelId] : undefined;
      const where = ch
        ? ch.kind === "dm"
          ? `→ ${name(s, ch.members.find((x) => x !== m.senderId))}`
          : ch.key
        : m.kind === "speech"
          ? `(out loud in the ${String(m.meta?.room ?? "room")})`
          : "";
      if (m.senderType === "system") {
        return pushFeed(out, { id: fid, ts: e.ts, agentId: null, kind: "notice", text: m.content.split("\n")[0], ref: m.id });
      }
      return pushFeed(out, {
        id: fid,
        ts: e.ts,
        agentId: m.senderType === "agent" ? m.senderId : null,
        kind: "message",
        text: `${name(s, m.senderId)} ${where}: ${m.content}`,
        ref: m.channelId ?? undefined,
      });
    }
    case "channel.created": {
      const c = p as unknown as Channel;
      return { ...s, channels: { ...s.channels, [c.id]: c } };
    }
    case "task.created":
    case "task.updated": {
      const t = p as unknown as Task;
      const prev = s.tasks[t.id];
      let out: OrgState = { ...s, tasks: { ...s.tasks, [t.id]: t } };
      if (!prev || prev.status !== t.status || prev.assigneeId !== t.assigneeId) {
        const verb = !prev
          ? `created ${t.ref}: ${t.title}${t.assigneeId ? ` → ${name(s, t.assigneeId)}` : ""}`
          : prev.status !== t.status
            ? `moved ${t.ref} to ${t.status.replace("_", " ")}`
            : `assigned ${t.ref} to ${name(s, t.assigneeId)}`;
        out = pushFeed(out, { id: fid, ts: e.ts, agentId: aid, kind: "task", text: `${aid ? name(s, aid) : "You"} ${verb}`, ref: t.id });
        if (aid && t.assigneeId && t.assigneeId !== aid && (!prev || prev.assigneeId !== t.assigneeId) && s.agents[t.assigneeId]) {
          out = {
            ...out,
            beams: [...out.beams.filter((b) => now - b.at < BEAM_TTL), { id: `${fid}-task`, from: aid, to: t.assigneeId, kind: "task", at: now }],
          };
        }
      }
      return out;
    }
    case "approval.requested": {
      const a = p as unknown as Approval;
      return { ...s, approvals: { ...s.approvals, [a.id]: a }, inboxVersion: s.inboxVersion + 1 };
    }
    case "approval.decided": {
      const a = p as unknown as Approval;
      const approvals = { ...s.approvals };
      delete approvals[a.id];
      return pushFeed(
        { ...s, approvals, inboxVersion: s.inboxVersion + 1 },
        { id: fid, ts: e.ts, agentId: a.agentId, kind: "approval", text: `You ${a.status} ${name(s, a.agentId)}'s ${a.tool}` },
      );
    }
    case "meeting.started":
      return pushFeed(
        {
          ...s,
          meetings: {
            ...s.meetings,
            [String(p.meetingId)]: {
              id: String(p.meetingId),
              agenda: String(p.agenda ?? ""),
              facilitatorId: String(p.facilitatorId ?? ""),
              participants: (p.participants as string[]) ?? [],
              speakerId: null,
              lastText: "",
              room: (p.room as string) ?? null,
              style: String(p.style ?? "discussion"),
            },
          },
        },
        { id: fid, ts: e.ts, agentId: aid, kind: "meeting", text: `${name(s, aid)} started a meeting: ${String(p.agenda ?? "")}` },
      );
    case "meeting.turn": {
      const m = s.meetings[String(p.meetingId)];
      const withLive = aid ? setLive(s, aid, { lastSaid: String(p.text ?? ""), lastSaidAt: now }) : s;
      if (!m) return withLive;
      return {
        ...withLive,
        meetings: { ...withLive.meetings, [m.id]: { ...m, speakerId: aid, lastText: String(p.text ?? "") } },
      };
    }
    case "meeting.ended": {
      const meetings = { ...s.meetings };
      delete meetings[String(p.meetingId)];
      return pushFeed({ ...s, meetings }, { id: fid, ts: e.ts, agentId: aid, kind: "meeting", text: `Meeting ended (${String(p.status)})` });
    }
    case "agent.created":
    case "agent.updated": {
      const a = p as unknown as Agent;
      return { ...s, agents: { ...s.agents, [a.id]: a } };
    }
    case "agent.deleted": {
      const agents = { ...s.agents };
      delete agents[String(p.id)];
      return { ...s, agents, relationships: s.relationships.filter((r) => r.fromId !== p.id && r.toId !== p.id) };
    }
    case "relationship.changed": {
      const r = p as unknown as Relationship;
      return { ...s, relationships: [...s.relationships.filter((x) => x.id !== r.id), r] };
    }
    case "relationship.removed":
      return { ...s, relationships: s.relationships.filter((x) => x.id !== p.id) };
    case "feature_request.created":
      return pushFeed(
        { ...s, inboxVersion: s.inboxVersion + 1 },
        { id: fid, ts: e.ts, agentId: aid, kind: "feature", text: `${name(s, aid)} requested a feature: ${String(p.title ?? "")}` },
      );
    case "agent.moved": {
      if (!aid || !s.agents[aid]) return s;
      const agents = { ...s.agents, [aid]: { ...s.agents[aid], location: (p.location as Agent["location"]) ?? null } };
      return pushFeed({ ...s, agents }, { id: fid, ts: e.ts, agentId: aid, kind: "move", text: `${name(s, aid)} ${String(p.text ?? "moves")}` });
    }
    case "agent.emote":
      if (!aid) return s;
      return setLive(s, aid, { emote: p.emote as AgentLive["emote"], emoteAt: now });
    case "world.object": {
      const o = p as unknown as WorldObject;
      return { ...s, objects: { ...s.objects, [o.id]: o } };
    }
    case "world.object.removed": {
      const objects = { ...s.objects };
      delete objects[String(p.id)];
      return { ...s, objects };
    }
    case "stage.updated": {
      const pg = p as unknown as StagePageInfo;
      const isNew = !s.stagePages[pg.id];
      return pushFeed(
        { ...s, stagePages: { ...s.stagePages, [pg.id]: pg } },
        { id: fid, ts: e.ts, agentId: aid, kind: "stage", text: `${name(s, aid)} ${isNew ? "put" : "updated"} “${pg.title}” on the Stage`, ref: pg.id },
      );
    }
    case "whiteboard.updated": {
      const b = p as unknown as WhiteboardInfo;
      const prev = s.whiteboards[b.id];
      const out = { ...s, whiteboards: { ...s.whiteboards, [b.id]: b } };
      if (b.updatedBy && b.updatedBy !== "user" && (!prev || prev.pendingCount < b.pendingCount))
        return pushFeed(out, { id: fid, ts: e.ts, agentId: aid, kind: "whiteboard", text: `${b.updatedBy} drew on “${b.title}”`, ref: b.id });
      return out;
    }
    case "attachment.created":
    case "attachment.updated":
    case "attachment.deleted":
      return { ...s, attachmentsVersion: s.attachmentsVersion + 1 };
    case "stage.deleted": {
      const stagePages = { ...s.stagePages };
      delete stagePages[String(p.id)];
      return { ...s, stagePages };
    }
    case "browser.updated":
      if (!aid) return s;
      return { ...s, browsers: { ...s.browsers, [aid]: { url: String(p.url ?? ""), title: String(p.title ?? ""), at: now } } };
    case "computer.action":
      return { ...s, computerAt: now };
    case "activity.ran":
      return pushFeed(s, { id: fid, ts: e.ts, agentId: null, kind: "activity", text: `Activity: ${String(p.title ?? "")}${p.room ? ` in the ${String(p.room)}` : ""}` });
    case "world.action":
      return pushFeed(s, { id: fid, ts: e.ts, agentId: aid, kind: "action", text: String(p.text ?? "") });
    case "alert.raised":
      return pushFeed(
        { ...s, inboxVersion: s.inboxVersion + 1 },
        { id: fid, ts: e.ts, agentId: null, kind: "alert", text: `${String(p.by ?? "Argus")} raised an alert: ${String(p.title ?? "")}` },
      );
    case "artifact.updated":
      return { ...s, artifactsVersion: s.artifactsVersion + 1 };
    case "system.warning":
      return pushFeed(s, { id: fid, ts: e.ts, agentId: aid, kind: "warning", text: String(p.reason ?? "warning") });
    case "org.status":
      return pushFeed(s, { id: fid, ts: e.ts, agentId: null, kind: "warning", text: `Organization ${String(p.status)}: ${String(p.reason ?? "")}` });
    default:
      return s;
  }
}

function summarizeArgs(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  const key = ["path", "command", "to", "channel", "task", "title", "query", "url", "agenda"].find((k) => typeof a[k] === "string");
  if (!key) return "";
  const v = String(a[key]);
  return ` ${v.length > 60 ? `${v.slice(0, 59)}…` : v}`;
}

export function pruneBeams(s: OrgState, now = Date.now()): OrgState {
  const beams = s.beams.filter((b) => now - b.at < BEAM_TTL);
  return beams.length === s.beams.length ? s : { ...s, beams };
}
