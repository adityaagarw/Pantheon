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
  AgentKind,
  KindCheck,
  Account,
  AccountSignIn,
  AccountTool,
  Approval,
  ArgusWatch,
  ComputerStatus,
  SandboxStatus,
  ServerStatus,
  HostStatus,
  NotebookCell,
  SheetCell,
  StageDoc,
  StageDocInfo,
  StageDocKind,
  Playbook,
  ShadowEvent,
  ShadowSession,
  Artifact,
  AssetInfo,
  LibraryInfo,
  PackInfo,
  PackListing,
  Flow,
  FlowGraph,
  FlowRun,
  WorktreeDiff,
  WorktreeListing,
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
  WizardTools,
  Turn,
  TurnDetail,
} from "./types";
import { engineHttp } from "@/lib/engine";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${engineHttp()}/api/v1${path}`, {
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
const put = <T>(p: string, b: unknown) => req<T>("PUT", p, b);
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
  benchTasks: () => get<{ tasks: BenchTask[]; workflows: BenchWorkflow[] }>("/bench/tasks"),
  benchRuns: (orgId?: string) => get<BenchRun[]>(`/bench/runs${q({ org_id: orgId })}`),
  startBenchRun: (b: { orgId: string; tasks: string[]; workflow: string; repeat: number; jobs: number; timeoutMinutes?: number; lead?: string }) =>
    post<BenchRun>("/bench/runs", b),
  cancelBenchRun: (id: string) => post<BenchRun>(`/bench/runs/${id}/cancel`),
  deleteBenchRun: (id: string) => del(`/bench/runs/${id}`),
  benchImport: () => get<BenchImport>("/bench/import"),
  startBenchImport: (b: { benchmark: "jobbench" | "lab"; limit?: number; split?: string; areas?: string }) => post<BenchImport>("/bench/import", b),
  tunings: () => get<Tuning[]>("/bench/tunings"),
  startTuning: (b: { orgId: string; task: string }) => post<Tuning>("/bench/tunings", b),
  cancelTuning: (id: string) => post<Tuning>(`/bench/tunings/${id}/cancel`),
  applyTuning: (id: string) => post<{ changed: string[] }>(`/bench/tunings/${id}/apply`),
  skills: () => get<{ name: string; description: string; builtin: boolean; files: string[]; body: string }[]>("/skills"),
  createOrg: (b: {
    name?: string;
    description?: string;
    workspace?: string;
    template?: string;
    /** A whole organization made in the new-organization wizard (export format). */
    definition?: Record<string, unknown>;
    settings?: Partial<Org["settings"]>;
    /** A new folder, a folder on this computer or a clone, with git and worktrees. */
    setup?: { source: "new" | "folder" | "clone" | "current"; path?: string; url?: string; git?: boolean; worktrees?: boolean };
  }) => post<Org>("/orgs", b),
  setUpWorkspace: (orgId: string, b: { source: "new" | "folder" | "clone" | "current"; path?: string; url?: string; git?: boolean; worktrees?: boolean }) =>
    post<Org>(`/orgs/${orgId}/workspace`, b),
  flows: (orgId: string) => get<Flow[]>(`/orgs/${orgId}/flows`),
  createFlow: (orgId: string, b: { name: string; graph?: FlowGraph; description?: string }) => post<Flow>(`/orgs/${orgId}/flows`, b),
  updateFlow: (id: string, b: { name?: string; graph?: FlowGraph; description?: string }) => patch<Flow>(`/flows/${id}`, b),
  deleteFlow: (id: string) => del(`/flows/${id}`),
  flowRuns: (flowId: string) => get<FlowRun[]>(`/flows/${flowId}/runs`),
  startFlowRun: (flowId: string, input: string) => post<FlowRun>(`/flows/${flowId}/runs`, { input }),
  flowRun: (runId: string) => get<FlowRun>(`/runs/${runId}`),
  cancelFlowRun: (runId: string) => post<FlowRun>(`/runs/${runId}/cancel`),
  worktrees: (orgId: string) => get<WorktreeListing>(`/orgs/${orgId}/worktrees`),
  worktreeDiff: (orgId: string, path: string) => get<WorktreeDiff>(`/orgs/${orgId}/worktrees/diff${q({ path })}`),
  mergeWorktree: (orgId: string, path: string) =>
    post<{ merged: boolean; branch: string; conflicts: string[]; output: string }>(`/orgs/${orgId}/worktrees/merge`, { path }),
  org: (id: string) => get<OrgSnapshot>(`/orgs/${id}`),
  updateOrg: (id: string, b: Partial<Org> & { settings?: Partial<Org["settings"]> }) =>
    patch<Org>(`/orgs/${id}`, b),
  deleteOrg: (id: string) => del(`/orgs/${id}`),
  exportOrg: (id: string) => get<Record<string, unknown>>(`/orgs/${id}/export`),
  importOrg: (definition: unknown, name?: string) => post<Org>("/orgs/import", { definition, name }),
  wizardTools: () => get<WizardTools>("/wizard/tools"),
  /** Zeus drafts the organization ({goal, kind, current?, ask?}) or improves one agent ({org, agent, ask?}). */
  wizardDraft: <T>(b: Record<string, unknown>) => post<T>("/wizard/draft", b),
  templates: () => get<Template[]>("/templates"),
  template: (key: string) => get<Record<string, unknown>>(`/templates/${key}`),
  addTemplate: (definition: unknown, name?: string) => post<Template>("/templates", { definition, name }),
  deleteTemplate: (key: string) => del(`/templates/${key}`),
  saveAsTemplate: (orgId: string, name: string, description?: string) => post<Template>(`/orgs/${orgId}/save-template`, { name, description }),

  // agents
  createAgent: (orgId: string, b: Partial<Agent> & { manager?: string; preset?: string }) =>
    post<Agent>(`/orgs/${orgId}/agents`, b),
  agent: (id: string) => get<Agent & { busy: boolean }>(`/agents/${id}`),
  updateAgent: (id: string, b: Partial<Agent>) => patch<Agent>(`/agents/${id}`, b),
  deleteAgent: (id: string) => del(`/agents/${id}`),
  agentKinds: () => get<AgentKind[]>("/agent-kinds"),
  checkKind: (kind: string, runner: Record<string, unknown>) => post<KindCheck>(`/agent-kinds/${kind}/check`, { runner }),
  accounts: () => get<{ accounts: Account[]; tools: AccountTool[] }>("/accounts"),
  createAccount: (body: { tool: string; method: string; name: string; key?: string }) => post<Account>("/accounts", body),
  updateAccount: (id: string, body: { name?: string; key?: string; cooling?: false }) => patch<Account>(`/accounts/${id}`, body),
  deleteAccount: (id: string) => del(`/accounts/${id}`),
  checkAccount: (id: string) => post<Account>(`/accounts/${id}/check`, {}),
  signIn: (id: string) => post<AccountSignIn>(`/accounts/${id}/sign-in`, {}),
  signInView: (id: string) => get<AccountSignIn>(`/accounts/sign-ins/${id}`),
  signInInput: (id: string, text: string) => post<AccountSignIn>(`/accounts/sign-ins/${id}/input`, { text }),
  signInCancel: (id: string) => del(`/accounts/sign-ins/${id}`),
  humanWaiting: (id: string) => get<{ waiting: Message[] }>(`/agents/${id}/human`),
  humanAnswer: (id: string, content: string, messageIds?: string[]) =>
    post<{ turnId: string; answered: string[] }>(`/agents/${id}/human/answer`, { content, messageIds }),
  stopAgent: (id: string) => post<{ stopped: boolean }>(`/agents/${id}/stop`),
  retryAgent: (id: string) => post(`/agents/${id}/retry`),
  resetAgentMemory: (id: string) => post(`/agents/${id}/reset-memory`),
  clearAgentChat: (id: string) => post<{ ok: boolean; removed: number }>(`/agents/${id}/clear-chat`),
  resetMetaAgent: (role: "zeus" | "argus" | "kronos", forget = false) => post(`/meta/${role}/reset`, { forget }),
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
  send: (orgId: string, b: { to?: string; channel?: string; content: string; attachments?: string[]; steer?: boolean }) =>
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
  rawFileUrl: (orgId: string, path: string) => `${engineHttp()}/api/v1/orgs/${orgId}/files/raw${q({ path })}`,

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
  approveAll: (orgId: string, agentId?: string) => post<{ approved: number }>(`/orgs/${orgId}/approvals/approve-all`, agentId ? { agentId } : {}),
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
  stageDocs: (orgId: string) => get<StageDocInfo[]>(`/orgs/${orgId}/docs`),
  createStageDoc: (orgId: string, b: { kind: StageDocKind; title?: string; attachmentId?: string }) =>
    post<StageDoc>(`/orgs/${orgId}/docs`, b),
  stageDoc: (id: string) => get<StageDoc>(`/docs/${id}`),
  renameStageDoc: (id: string, title: string) => patch<StageDocInfo>(`/docs/${id}`, { title }),
  deleteStageDoc: (id: string) => del(`/docs/${id}`),
  stageDocExportUrl: (id: string) => `${engineHttp()}/api/v1/docs/${id}/export`,
  saveSheetCells: (id: string, changes: Record<string, Record<string, SheetCell | null>>, sheetNames?: string[]) =>
    req<{ version: number }>("PUT", `/docs/${id}/cells`, { changes, sheetNames }),
  /** Saves a Markdown document; 409 (ApiError) when it changed since `base`. */
  saveStageText: (id: string, text: string, base?: number) => req<{ version: number }>("PUT", `/docs/${id}/text`, { text, base }),
  pdfView: (id: string, b: { page?: number; highlight?: string; note?: string; clear?: boolean }) => post<StageDoc>(`/docs/${id}/pdf`, b),
  addNotebookCell: (id: string, b: { source?: string; type?: "code" | "markdown"; after?: string }) => post<NotebookCell>(`/docs/${id}/cells`, b),
  editNotebookCell: (id: string, cellId: string, b: { source?: string; type?: "code" | "markdown"; to?: number }) =>
    patch<NotebookCell>(`/docs/${id}/cells/${cellId}`, b),
  deleteNotebookCell: (id: string, cellId: string) => del(`/docs/${id}/cells/${cellId}`),
  runNotebookCell: (id: string, cellId: string) => post<{ started: boolean }>(`/docs/${id}/cells/${cellId}/run`),
  runNotebook: (id: string) => post<{ started: boolean }>(`/docs/${id}/run-all`),
  notebookKernel: (id: string, action: "interrupt" | "restart") => post<{ ok: boolean }>(`/docs/${id}/kernel/${action}`),
  whiteboards: (orgId: string) => get<WhiteboardInfo[]>(`/orgs/${orgId}/whiteboards`),
  createWhiteboard: (orgId: string, title: string) => post<WhiteboardInfo>(`/orgs/${orgId}/whiteboards`, { title }),
  whiteboard: (id: string) => get<WhiteboardScene>(`/whiteboards/${id}`),
  saveWhiteboard: (id: string, b: { elements: unknown[]; appState?: Record<string, unknown>; files?: Record<string, unknown> }) =>
    req<WhiteboardScene>("PUT", `/whiteboards/${id}/scene`, b),
  claimWhiteboardPending: (id: string) => post<{ pending: unknown[] }>(`/whiteboards/${id}/pending/claim`),
  saveWhiteboardThumbnail: (id: string, dataUrl: string, version?: number) =>
    req<void>("PUT", `/whiteboards/${id}/thumbnail`, { dataUrl, version }),
  attachments: (orgId: string, p: { agentId?: string; scope?: "all" | "agent" | "shared" } = {}) =>
    get<AttachmentInfo[]>(`/orgs/${orgId}/attachments?${new URLSearchParams({ agent_id: p.agentId ?? "", scope: p.scope ?? "all" })}`),
  attachmentInfo: (id: string) => get<AttachmentInfo>(`/attachments/${id}`),
  attachmentText: (id: string, offset = 0) => get<{ text: string; total: number }>(`/attachments/${id}/text?offset=${offset}&limit=8000`),
  deleteAttachment: (id: string) => del(`/attachments/${id}`),
  uploadAttachment: async (orgId: string, file: File, agentId?: string | null): Promise<AttachmentInfo> => {
    const form = new FormData();
    form.append("file", file, file.name || "pasted-image.png");
    form.append("agent_id", agentId ?? "");
    const res = await fetch(`${engineHttp()}/api/v1/orgs/${orgId}/attachments`, { method: "POST", body: form });
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
  moveStagePage: (id: string, folder: "" | "archive") => patch<StagePageInfo>(`/stage/${id}`, { folder }),
  agentAttention: (agentId: string, view: AttentionView) => post<void>(`/agents/${agentId}/attention`, view),
  clearAttention: (agentId: string) => post<void>(`/agents/${agentId}/attention`, { kind: "none" }),
  agentBrowser: (agentId: string) => get<{ open: boolean; url: string; title: string }>(`/agents/${agentId}/browser`),
  computer: () => get<ComputerStatus>("/computer"),
  server: () => get<ServerStatus>("/server"),
  setServer: (b: { enabled?: boolean; port?: number; newAccessCode?: boolean }) => put<ServerStatus>("/server", b),
  setComputer: (backend: string) => put<ComputerStatus>("/computer", { backend }),
  computerStop: () => post<ComputerStatus>("/computer/stop"),
  computerResume: () => post<ComputerStatus>("/computer/resume"),
  sandbox: () => get<SandboxStatus>("/computer/sandbox"),
  /** Where agents browse the web: the built-in headless browser or the sandbox desktop's. */
  browserWhere: () => get<{ where: "auto" | "builtin" | "sandbox"; sandbox: boolean }>("/computer/browser"),
  setBrowserWhere: (where: string) => put<{ where: "auto" | "builtin" | "sandbox"; sandbox: boolean }>("/computer/browser", { where }),
  sandboxSetup: () => post<SandboxStatus>("/computer/sandbox/setup"),
  sandboxStart: () => post<SandboxStatus>("/computer/sandbox/start"),
  sandboxStop: () => post<SandboxStatus>("/computer/sandbox/stop"),
  /** The sandbox desktop to see and control in a page (KasmVNC, relayed by the engine). */
  sandboxDesktopUrl: (viewOnly = false) =>
    `${engineHttp()}/api/v1/computer/sandbox/desktop/index.html${q({ path: "api/v1/computer/sandbox/desktop/websockify", resize: "scale", view_only: viewOnly ? "1" : "0" })}`,
  dockerStart: () => post<{ message: string }>("/computer/docker/start"),
  computerPreview: () => get<{ image: string | null; where?: string; agent?: string | null; working?: boolean; reason?: string }>("/computer/preview"),
  computerPermissions: () => post<{ ok: boolean; message: string }>("/computer/permissions"),
  agentSchedules: (agentId: string) => get<AgentSchedule[]>(`/agents/${agentId}/schedules`),
  cancelSchedule: (id: string) => del(`/schedules/${id}`),
  customTools: () => get<CustomTool[]>("/custom-tools"),
  deleteCustomTool: (name: string) => del(`/custom-tools/${name}`),
  activities: (orgId: string) => get<Activity[]>(`/orgs/${orgId}/activities`),
  createActivity: (orgId: string, b: Partial<Activity>) => post<Activity>(`/orgs/${orgId}/activities`, b),
  updateActivity: (id: string, b: Partial<Activity>) => patch<Activity>(`/activities/${id}`, b),
  deleteActivity: (id: string) => del(`/activities/${id}`),
  runActivity: (id: string) => post<{ notified: string[] }>(`/activities/${id}/run`),
  metaAgent: (role: "zeus" | "argus" | "kronos") => get<{ orgId: string; agent: Agent; channel: Channel; dmKey: string }>(`/meta/${role}`),
  shadowSessions: (orgId: string) => get<ShadowSession[]>(`/orgs/${orgId}/shadow`),
  startShadow: (orgId: string, b: { agentId: string; title: string; note?: string }) =>
    post<ShadowSession>(`/orgs/${orgId}/shadow`, b),
  shadowSession: (id: string) => get<ShadowSession & { events: ShadowEvent[] }>(`/shadow/${id}`),
  stopShadow: (id: string) => post<ShadowSession>(`/shadow/${id}/stop`),
  learnShadow: (id: string) => post<ShadowSession>(`/shadow/${id}/learn`),
  deleteShadow: (id: string) => del(`/shadow/${id}`),
  playbooks: (orgId: string, agentId?: string) => get<Playbook[]>(`/orgs/${orgId}/playbooks${agentId ? `?agentId=${agentId}` : ""}`),
  updatePlaybook: (id: string, b: { title?: string; content?: string }) => patch<Playbook>(`/playbooks/${id}`, b),
  deletePlaybook: (id: string) => del(`/playbooks/${id}`),
  runtimeLimits: () => get<{ maxConcurrent: number; running: number; waiting: number; max: number }>("/runtime/limits"),
  setRuntimeLimits: (maxConcurrent: number) =>
    req<{ maxConcurrent: number; running: number; waiting: number; max: number }>("PUT", "/runtime/limits", { maxConcurrent }),
  hostStatus: () => get<HostStatus>("/host/status"),
  hostAddFolder: (path: string) => post<HostStatus>("/host/folders", { path }),
  hostRemoveFolder: (path: string) => post<HostStatus>("/host/folders/remove", { path }),
  argusWatches: () => get<ArgusWatch[]>("/argus/watches"),
  setArgusWatch: (orgId: string, b: { everyMinutes: number; focus: string }) => req<ArgusWatch>("PUT", `/argus/watches/${orgId}`, b),
  removeArgusWatch: (orgId: string) => del(`/argus/watches/${orgId}`),

  // physical world, assets
  world: (orgId: string) => get<WorldSnapshot>(`/orgs/${orgId}/world`),
  moveAgent: (agentId: string, place: string) => post<{ where: string }>(`/agents/${agentId}/move`, { place }),
  createObject: (orgId: string, b: Partial<WorldObject>) => post<WorldObject>(`/orgs/${orgId}/world/objects`, b),
  deleteObject: (orgId: string, id: string) => del(`/orgs/${orgId}/world/objects/${id}`),
  assets: () => get<AssetInfo[]>("/assets"),
  deleteAsset: (id: string) => del(`/assets/${id}`),

  // packs: libraries, assets, templates, plugins
  packs: () => get<PackListing>("/packs"),
  reloadPacks: () => post<PackListing>("/packs/reload"),
  setPackEnabled: (id: string, enabled: boolean) => patch<PackInfo>(`/packs/${id}`, { enabled }),
  removePack: (id: string) => del(`/packs/${id}`),
  addPackFolder: (path: string) => post<PackInfo>("/packs/folder", { path }),
  addNpmLibrary: (spec: string) => post<PackInfo & { warnings: string[] }>("/packs/npm", { spec }),
  libraryDocs: (id: string) => get<LibraryInfo & { docs: string }>(`/packs/libraries/${id}/docs`),
  uploadPack: async (file: File): Promise<PackInfo> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch(`${engineHttp()}/api/v1/packs/upload`, { method: "POST", body: form });
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

  // secrets (values are write-only: never returned)
  secrets: (orgId: string) => get<SecretInfo[]>(`/orgs/${orgId}/secrets`),
  createSecret: (orgId: string, b: SecretInput & { name: string; value: string }) => post<SecretInfo>(`/orgs/${orgId}/secrets`, b),
  updateSecret: (id: string, b: Partial<SecretInput> & { value?: string; enabled?: boolean }) => patch<SecretInfo>(`/secrets/${id}`, b),
  deleteSecret: (id: string) => del(`/secrets/${id}`),
  secretUsage: (orgId: string) => get<SecretUse[]>(`/orgs/${orgId}/secrets/usage?limit=30`),

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
  | { kind: "whiteboard"; title: string }
  | { kind: StageDocKind; docId: string; title: string; text: string };

export interface SecretInput {
  description: string;
  agents: string[];
  domains: string[];
  allowShell: boolean;
}

export interface SecretInfo {
  id: string;
  name: string;
  description: string;
  agentIds: string[];
  agents: string[];
  domains: string[];
  allowShell: boolean;
  enabled: boolean;
  hasValue: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  lastUsedAt: string | null;
}

export interface SecretUse {
  secret: string;
  agent: string;
  tool: string;
  target: string;
  at: string | null;
}

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

// --- benchmarks (SwarmBench) ---------------------------------------------------------------

export interface BenchTask {
  id: string;
  title: string;
  category: string;
  difficulty: string;
  workflow: string;
  summary: string;
  minutes: number;
  /** "tests" (hidden unit tests) or "rubric" (a judge checks each criterion). */
  grading?: string;
  /** "SwarmBench", "JobBench" or "LAB (Harvey)". */
  source?: string;
}

export interface BenchImport {
  running: boolean;
  detail: string;
  made: string[];
}

/** A team tuned on a benchmark task (engine services/tuning.py). */
export interface Tuning {
  id: string;
  org_id: string;
  org_name: string;
  task_id: string;
  status: "running" | "done" | "failed" | "cancelled" | "applied";
  stage: string;
  detail: string;
  started: number;
  finished: number | null;
  runs: Record<string, { status?: string; detail?: string; score?: number; minutes?: number; tokens?: number; tool_calls?: number }>;
  edits: { id: string; kind: string; agent?: string; name?: string; summary?: string; evidence?: string }[];
  adopted: string[];
  verdicts: Record<string, string>;
  why: string;
  lesson: { cue?: string; lesson?: string; evidence?: string; confidence?: string };
  library_entry: string;
  skills_made: string[];
}

export interface BenchWorkflow {
  name: string;
  summary: string;
  roles: string[];
}

export interface BenchMetrics {
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  turns?: number;
  tool_calls?: number;
  messages?: number;
  agents?: number;
  agents_active?: number;
  wall_seconds?: number;
}

export interface BenchResult {
  task_id: string;
  task_key: string;
  title: string;
  category: string;
  workflow: string | null;
  score: number;
  weights: Record<string, number>;
  parts: Record<string, ({ score: number; passed?: number; total?: number } & Record<string, unknown>) | null>;
  run: { status: string; detail: string; timed_out: boolean; wall_seconds: number };
  metrics: BenchMetrics;
}

export interface BenchRun {
  id: string;
  orgId: string | null;
  orgName: string;
  status: "running" | "done" | "cancelled" | "failed";
  config: { tasks: string[]; workflow: string; repeat: number; jobs: number; timeoutMinutes?: number | null; lead?: string | null };
  results: BenchResult[];
  detail: string;
  progress: {
    done: number;
    total: number;
    current: { id: string; label: string; status: string; detail: string; seconds: number; cloneId: string | null; metrics: BenchMetrics }[];
  };
  summary: {
    meanScore: number | null;
    fullScores: number;
    costUsd: number;
    tokens: number;
    wallSeconds: number;
    timeouts: number;
    /** Where the time and round trips went (absent on runs from before they were counted). */
    modelCalls?: number;
    modelMinutes?: number;
    cachedPct?: number | null;
    toolCalls?: number;
    messages?: number;
    edits?: number;
    editFailures?: number;
    subagents?: number;
    acceptanceChecks?: number;
  };
  createdAt: string | null;
  finishedAt: string | null;
}
