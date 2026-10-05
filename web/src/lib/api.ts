/** Typed client for the Pantheon REST API (relative /api, proxied to the backend). */

import type {
  Activity,
  AttachmentInfo,
  WhiteboardInfo,
  WhiteboardScene,
  MemoryItem,
  AgentSchedule,
  StagePageInfo,
  CustomTool,
  Agent,
  Approval,
  ArgusWatch,
  Artifact,
  AssetInfo,
  PluginInfo,
  WorldObject,
  WorldSnapshot,
  Board,
  CatalogTool,
  Channel,
  FeatureRequest,
  InboxItem,
  LlmCall,
  McpServer,
  Message,
  Org,
  OrgSnapshot,
  PantheonEvent,
  Provider,
  Relationship,
  Stats,
  Task,
  TaskEvent,
  Template,
  Turn,
  TurnDetail,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(p: string) => req<T>("GET", p);
const post = <T>(p: string, b?: unknown) => req<T>("POST", p, b ?? {});
const patch = <T>(p: string, b: unknown) => req<T>("PATCH", p, b);
const del = (p: string) => req<void>("DELETE", p);
const q = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const api = {
  // orgs
  orgs: () => get<Org[]>("/orgs"),
  createOrg: (b: { name?: string; description?: string; workspace?: string; template?: string }) =>
    post<Org>("/orgs", b),
  org: (id: string) => get<OrgSnapshot>(`/orgs/${id}`),
  updateOrg: (id: string, b: Partial<Org> & { settings?: Partial<Org["settings"]> }) =>
    patch<Org>(`/orgs/${id}`, b),
  deleteOrg: (id: string) => del(`/orgs/${id}`),
  exportOrg: (id: string) => get<Record<string, unknown>>(`/orgs/${id}/export`),
  importOrg: (definition: unknown, name?: string) => post<Org>("/orgs/import", { definition, name }),
  templates: () => get<Template[]>("/templates"),

  // agents
  createAgent: (orgId: string, b: Partial<Agent> & { manager?: string; preset?: string }) =>
    post<Agent>(`/orgs/${orgId}/agents`, b),
  agent: (id: string) => get<Agent & { busy: boolean }>(`/agents/${id}`),
  updateAgent: (id: string, b: Partial<Agent>) => patch<Agent>(`/agents/${id}`, b),
  deleteAgent: (id: string) => del(`/agents/${id}`),
  stopAgent: (id: string) => post<{ stopped: boolean }>(`/agents/${id}/stop`),
  retryAgent: (id: string) => post(`/agents/${id}/retry`),
  resetAgentMemory: (id: string) => post(`/agents/${id}/reset-memory`),
  compactAgent: (id: string) => post<{ removed: number; summary: string }>(`/agents/${id}/compact`),
  agentThread: (id: string) =>
    get<{ next: string[]; context: { tokens: number; window?: number; compactAt?: number }; summary: string; turnId: string | null; steps: number; messages: import("./types").WireMessage[] }>(
      `/agents/${id}/thread`,
    ),

  // relationships
  setRelationship: (orgId: string, b: { fromId: string; toId: string; kind: string; label?: string }) =>
    post<Relationship>(`/orgs/${orgId}/relationships`, b),
  removeRelationship: (orgId: string, relId: string) => del(`/orgs/${orgId}/relationships/${relId}`),

  // comms
  channels: (orgId: string) => get<Channel[]>(`/orgs/${orgId}/channels`),
  createChannel: (orgId: string, b: { name: string; members: string[]; topic?: string; notify?: string }) =>
    post<Channel>(`/orgs/${orgId}/channels`, b),
  updateChannel: (id: string, b: Partial<Channel> & { add?: string[]; remove?: string[] }) => patch<Channel>(`/channels/${id}`, b),
  deleteChannel: (id: string) => del(`/channels/${id}`),
  channelMessages: (id: string, before?: string) =>
    get<Message[]>(`/channels/${id}/messages${q({ limit: 200, before })}`),
  send: (orgId: string, b: { to?: string; channel?: string; content: string; attachments?: string[] }) =>
    post<Message>(`/orgs/${orgId}/messages`, b),
  orgMessages: (orgId: string, params: { agent_id?: string; task_id?: string; limit?: number } = {}) =>
    get<Message[]>(`/orgs/${orgId}/messages${q(params)}`),
  inbox: (orgId: string) => get<InboxItem[]>(`/orgs/${orgId}/inbox`),
  markRead: (orgId: string, ids?: string[]) => post(`/orgs/${orgId}/inbox/read`, { ids }),

  // work
  boards: (orgId: string) => get<Board[]>(`/orgs/${orgId}/boards`),
  createBoard: (orgId: string, b: { name: string; description?: string }) => post<Board>(`/orgs/${orgId}/boards`, b),
  tasks: (orgId: string) => get<Task[]>(`/orgs/${orgId}/tasks`),
  createTask: (orgId: string, b: Partial<Task>) => post<Task>(`/orgs/${orgId}/tasks`, b),
  task: (id: string) => get<Task & { timeline: TaskEvent[]; messages: Message[] }>(`/tasks/${id}`),
  updateTask: (id: string, b: Partial<Task> & { comment?: string }) => patch<Task>(`/tasks/${id}`, b),
  artifacts: (orgId: string) => get<Artifact[]>(`/orgs/${orgId}/artifacts`),
  files: (orgId: string, path = "") =>
    get<{
      path: string;
      type: "dir" | "file";
      root?: string;
      entries?: { name: string; type: "dir" | "file"; size: number | null; path: string }[];
    }>(`/orgs/${orgId}/files${q({ path })}`),
  rawFileUrl: (orgId: string, path: string) => `/api/v1/orgs/${orgId}/files/raw${q({ path })}`,

  // observability
  turns: (orgId: string, params: { agent_id?: string; status?: string; limit?: number; before?: string } = {}) =>
    get<Turn[]>(`/orgs/${orgId}/turns${q(params)}`),
  turn: (id: string) => get<TurnDetail>(`/turns/${id}`),
  llmCall: (id: string) => get<LlmCall>(`/llm-calls/${id}`),
  search: (orgId: string, query: string) =>
    get<{ messages: Message[]; turns: Turn[]; toolCalls: import("./types").ToolCall[]; llmCalls: LlmCall[] }>(
      `/orgs/${orgId}/search${q({ q: query })}`,
    ),
  stats: (orgId: string, days = 14) => get<Stats>(`/orgs/${orgId}/stats${q({ days })}`),
  events: (orgId: string, after: number) => get<PantheonEvent[]>(`/orgs/${orgId}/events${q({ after })}`),
  recentEvents: (orgId: string, last = 250) => get<PantheonEvent[]>(`/orgs/${orgId}/events${q({ last })}`),
  meetings: (orgId: string) => get<MeetingRecord[]>(`/orgs/${orgId}/meetings`),

  // approvals & feature requests
  approvals: (orgId: string, status = "pending") => get<Approval[]>(`/orgs/${orgId}/approvals${q({ status })}`),
  decide: (id: string, approved: boolean, note = "", always = false) =>
    post<Approval>(`/approvals/${id}`, { approved, note, always }),
  featureRequests: (orgId?: string) => get<FeatureRequest[]>(`/feature-requests${q({ org_id: orgId })}`),
  createFeatureRequest: (b: { orgId?: string; title: string; description?: string }) =>
    post<FeatureRequest>("/feature-requests", b),
  updateFeatureRequest: (id: string, b: Partial<FeatureRequest>) =>
    patch<FeatureRequest>(`/feature-requests/${id}`, b),

  // admin
  providers: () => get<Provider[]>("/providers"),
  createProvider: (b: Record<string, unknown>) => post<Provider>("/providers", b),
  updateProvider: (id: string, b: Record<string, unknown>) => patch<Provider>(`/providers/${id}`, b),
  deleteProvider: (id: string) => del(`/providers/${id}`),
  testProvider: (id: string, model?: string) =>
    post<{ ok: boolean; models?: string[]; modelsError?: string; reply?: string; error?: string; model?: string }>(
      `/providers/${id}/test`,
      { model },
    ),
  mcpServers: (orgId?: string) => get<McpServer[]>(`/mcp-servers${q({ org_id: orgId })}`),
  createMcpServer: (b: Record<string, unknown>) => post<McpServer>("/mcp-servers", b),
  updateMcpServer: (id: string, b: Record<string, unknown>) => patch<McpServer>(`/mcp-servers/${id}`, b),
  deleteMcpServer: (id: string) => del(`/mcp-servers/${id}`),
  testMcpServer: (id: string) =>
    post<{ ok: boolean; error?: string; tools?: { name: string; description: string }[] }>(
      `/mcp-servers/${id}/test`,
    ),
  toolCatalog: () => get<CatalogTool[]>("/tools/catalog"),
  supervisor: () => get<{ orgId: string; agent: Agent; channel: Channel; dmKey: string }>("/supervisor"),
  messageThinking: (id: string) =>
    get<{ turnId: string | null; steps: { reasoning: string; text: string; tools: string[]; at: string | null }[] }>(`/messages/${id}/thinking`),
  whiteboards: (orgId: string) => get<WhiteboardInfo[]>(`/orgs/${orgId}/whiteboards`),
  createWhiteboard: (orgId: string, title: string) => post<WhiteboardInfo>(`/orgs/${orgId}/whiteboards`, { title }),
  whiteboard: (id: string) => get<WhiteboardScene>(`/whiteboards/${id}`),
  saveWhiteboard: (id: string, b: { elements: unknown[]; appState?: Record<string, unknown>; files?: Record<string, unknown> }) =>
    req<WhiteboardScene>("PUT", `/whiteboards/${id}/scene`, b),
  claimWhiteboardPending: (id: string) => post<{ pending: unknown[] }>(`/whiteboards/${id}/pending/claim`),
  saveWhiteboardThumbnail: (id: string, dataUrl: string) => req<void>("PUT", `/whiteboards/${id}/thumbnail`, { dataUrl }),
  attachments: (orgId: string, p: { agentId?: string; scope?: "all" | "agent" | "shared" } = {}) =>
    get<AttachmentInfo[]>(`/orgs/${orgId}/attachments?${new URLSearchParams({ agent_id: p.agentId ?? "", scope: p.scope ?? "all" })}`),
  attachmentInfo: (id: string) => get<AttachmentInfo>(`/attachments/${id}`),
  attachmentText: (id: string, offset = 0) => get<{ text: string; total: number }>(`/attachments/${id}/text?offset=${offset}&limit=8000`),
  deleteAttachment: (id: string) => del(`/attachments/${id}`),
  uploadAttachment: async (orgId: string, file: File, agentId?: string | null): Promise<AttachmentInfo> => {
    const form = new FormData();
    form.append("file", file, file.name || "pasted-image.png");
    form.append("agent_id", agentId ?? "");
    const res = await fetch(`/api/v1/orgs/${orgId}/attachments`, { method: "POST", body: form });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* keep the status */
      }
      throw new Error(detail);
    }
    return res.json();
  },
  memories: (orgId: string, p: { q?: string; scope?: string; agent?: string }) =>
    get<{ items: MemoryItem[]; counts: Record<string, number>; embeddings: { mode: string; model: string; loaded: boolean; error: string | null } }>(
      `/orgs/${orgId}/memories?${new URLSearchParams({ q: p.q ?? "", scope: p.scope ?? "all", agent: p.agent ?? "" })}`,
    ),
  addMemory: (orgId: string, content: string, agentId: string | null) => post<MemoryItem>(`/orgs/${orgId}/memories`, { content, agentId }),
  updateMemory: (id: string, content: string) => patch<MemoryItem>(`/memories/${id}`, { content }),
  deleteMemory: (id: string) => del(`/memories/${id}`),
  stagePages: (orgId: string) => get<StagePageInfo[]>(`/orgs/${orgId}/stage`),
  deleteStagePage: (id: string) => del(`/stage/${id}`),
  agentAttention: (agentId: string, view: AttentionView) => post<void>(`/agents/${agentId}/attention`, view),
  clearAttention: (agentId: string) => post<void>(`/agents/${agentId}/attention`, { kind: "none" }),
  agentBrowser: (agentId: string) => get<{ open: boolean; url: string; title: string }>(`/agents/${agentId}/browser`),
  computer: () => get<{ ok: boolean; url: string; error?: string; inUseBy: string | null }>("/computer"),
  agentSchedules: (agentId: string) => get<AgentSchedule[]>(`/agents/${agentId}/schedules`),
  cancelSchedule: (id: string) => del(`/schedules/${id}`),
  customTools: () => get<CustomTool[]>("/custom-tools"),
  deleteCustomTool: (name: string) => del(`/custom-tools/${name}`),
  activities: (orgId: string) => get<Activity[]>(`/orgs/${orgId}/activities`),
  createActivity: (orgId: string, b: Partial<Activity>) => post<Activity>(`/orgs/${orgId}/activities`, b),
  updateActivity: (id: string, b: Partial<Activity>) => patch<Activity>(`/activities/${id}`, b),
  deleteActivity: (id: string) => del(`/activities/${id}`),
  runActivity: (id: string) => post<{ notified: string[] }>(`/activities/${id}/run`),
  metaAgent: (role: "zeus" | "argus") => get<{ orgId: string; agent: Agent; channel: Channel; dmKey: string }>(`/meta/${role}`),
  argusWatches: () => get<ArgusWatch[]>("/argus/watches"),
  setArgusWatch: (orgId: string, b: { everyMinutes: number; focus: string }) => req<ArgusWatch>("PUT", `/argus/watches/${orgId}`, b),
  removeArgusWatch: (orgId: string) => del(`/argus/watches/${orgId}`),

  // physical world, assets, plugins
  world: (orgId: string) => get<WorldSnapshot>(`/orgs/${orgId}/world`),
  moveAgent: (agentId: string, place: string) => post<{ where: string }>(`/agents/${agentId}/move`, { place }),
  createObject: (orgId: string, b: Partial<WorldObject>) => post<WorldObject>(`/orgs/${orgId}/world/objects`, b),
  deleteObject: (orgId: string, id: string) => del(`/orgs/${orgId}/world/objects/${id}`),
  assets: () => get<AssetInfo[]>("/assets"),
  deleteAsset: (id: string) => del(`/assets/${id}`),
  plugins: () => get<{ dir: string; plugins: PluginInfo[] }>("/plugins"),

  // voice
  voiceConfig: () => get<VoiceConfig>("/voice/config"),
  saveVoiceConfig: (b: Partial<VoiceConfig>) => req<VoiceConfig>("PUT", "/voice/config", b),
  voiceVoices: () => get<{ voices: string[]; default: string; model: string; error?: string }>("/voice/voices"),
  voiceHealth: () =>
    get<{ ok: boolean; models?: string[]; error?: string; baseUrl: string; vad?: string; vadError?: string | null; shareDir?: string; shareHostDir?: string | null }>(
      "/voice/health",
    ),
};

/** What the user is looking at on the Stage, reported to the agent that owns it. */
export type AttentionView =
  | { kind: "page"; pageId: string; title: string; text: string; held: boolean }
  | { kind: "browser"; title: string; url: string }
  | { kind: "whiteboard"; title: string };

export interface VoiceConfig {
  baseUrl: string;
  sttModel: string;
  ttsModel: string;
  defaultVoice: string;
  autoSpeak: boolean;
  language: string;
  /** Read aloud with the browser's voices when no speech server is reachable. */
  browserTts: boolean;
  /** Browser speech recognition for the mic when there's no server (Chrome/Edge/Safari send audio to Google/Apple). */
  browserStt: boolean;
  /** Write-only: send a new key (or "" to clear it); reads report hasApiKey. */
  apiKey?: string;
  hasApiKey?: boolean;
}


export interface MeetingRecord {
  id: string;
  orgId: string;
  channelId: string | null;
  facilitatorId: string;
  participants: string[];
  agenda: string;
  style: string;
  options: { rounds?: number; detail?: string; create_tasks?: boolean; room?: string | null };
  status: "running" | "done" | "failed";
  minutes: string;
  taskIds: string[];
  createdAt: string | null;
  endedAt: string | null;
}