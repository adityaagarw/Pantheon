/** Wire types for the Pantheon API (mirrors server/app/*_to_dict). */

export type RuntimeStatus =
  | "idle"
  | "working"
  | "awaiting_approval"
  | "error"
  | "retrying"
  | "cooling_down";

export interface ToolEntry {
  name: string;
  approval?: "auto" | "ask" | "deny";
}

export interface ModelBinding {
  provider_id?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  context_window?: number;
  reasoning_effort?: "off" | "low" | "medium" | "high" | "xhigh";
  vision?: boolean; // the model accepts images (screenshots from the browser / computer)
}

export interface Avatar {
  outfit?: string;
  accent?: string;
  skin?: string;
  hair?: string;
  body?: string;
  voice?: string;
  character?: string;
}

export interface Agent {
  id: string;
  orgId: string;
  name: string;
  role: string;
  team: string;
  persona: string;
  model: ModelBinding;
  tools: ToolEntry[];
  skills: string[];
  worktree: string | null;
  limits: { max_steps_per_turn?: number; max_turns_per_hour?: number };
  permissions: { tasks?: TaskPolicy };
  avatar: Avatar;
  status: "active" | "paused" | "disabled";
  runtimeStatus: RuntimeStatus;
  runtimeDetail: string;
  consecutiveFailures: number;
  retryAt: string | null;
  isSupervisor: boolean;
  metaRole?: "zeus" | "argus" | null;
  location?: AgentLocation | null;
  createdAt: string | null;
}

/** Where an agent chose to be; null = its default spot (desk, meetings, breaks). */
export type AgentLocation =
  | { kind: "room"; room: string }
  | { kind: "agent"; agentId: string; name?: string }
  | { kind: "item"; room: string; itemId: string; itemKind?: string; label?: string };

export interface WorldObject {
  id: string;
  name: string;
  asset: string;
  description: string;
  holderId: string | null;
  place: { kind?: string; room?: string; itemId?: string; nearAgent?: string };
  state: Record<string, unknown>;
}

export interface WorldSnapshot {
  enabled: boolean;
  rooms: { name: string; type: string; items: { id: string; kind: string; label: string }[] }[];
  locations: Record<string, AgentLocation>;
  objects: WorldObject[];
}

export interface PrimitivePart {
  shape: "box" | "cylinder" | "sphere" | "cone";
  size: [number, number, number];
  pos: [number, number, number];
  rot: [number, number, number];
  color: string;
}

/** A runtime 3D asset (built, imported or from a plugin). */
export interface AssetInfo {
  key: string; // "asset:<id>" | "plugin:<plugin>/<id>"
  id: string;
  label: string;
  category: string;
  kind: "glb" | "procedural";
  size: [number, number, number]; // w, d, h meters
  scale: number;
  carryable: boolean;
  blocking: boolean;
  description: string;
  license: string;
  source: string;
  sourceUrl?: string;
  url?: string;
  bounds?: { min: number[]; max: number[] } | null;
  parts?: PrimitivePart[];
}

export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  path: string;
  assets: string[];
  templates: string[];
  tools: string[];
  error: string | null;
}

export interface WhiteboardInfo {
  id: string;
  orgId: string;
  title: string;
  version: number;
  updatedBy: string;
  hasThumbnail: boolean;
  elementCount: number;
  pendingCount: number;
  updatedAt: string | null;
}

export interface WhiteboardScene extends WhiteboardInfo {
  elements: unknown[];
  appState: Record<string, unknown>;
  files: Record<string, unknown>;
  pending: unknown[];
}

/** A file given to an agent (or shared with the org): a document or an image. */
export interface AttachmentInfo {
  id: string;
  orgId: string;
  agentId: string | null;
  messageId: string | null;
  name: string;
  mime: string;
  size: number;
  kind: "document" | "image" | "other";
  status: "processing" | "ready" | "failed";
  error: string;
  info: { pages?: number; chars?: number; width?: number; height?: number; chunks?: number; note?: string; truncated?: boolean; sheets?: number; rows?: number };
  shared: boolean;
  uploadedBy: string;
  createdAt: string | null;
}

/** The small form carried on a message. */
export interface AttachmentBrief {
  id: string;
  name: string;
  kind: "document" | "image" | "other";
  mime: string;
  size: number;
}

export interface MemoryItem {
  id: string;
  orgId: string;
  agentId: string | null;
  kind: string;
  content: string;
  source: "agent" | "user";
  shared: boolean;
  score?: number;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface StagePageInfo {
  id: string;
  orgId: string;
  agentId: string | null;
  title: string;
  kind: "scene" | "html";
  version: number;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface AgentSchedule {
  id: string;
  agentId: string;
  message: string;
  cron: string | null;
  timezone: string;
  enabled: boolean;
  runs: number;
  nextRunAt: string | null;
  lastRunAt: string | null;
}

export interface CustomTool {
  name: string;
  description: string;
  kind: "action" | "http" | "prompt";
  parameters: Record<string, unknown>;
  config: Record<string, unknown>;
  approval: "auto" | "ask" | "deny";
  createdBy: string;
  createdAt: string | null;
}

export interface Activity {
  id: string;
  orgId: string;
  title: string;
  instructions: string;
  participants: string[];
  room: string | null;
  schedule: { every_minutes?: number; daily_at?: string };
  enabled: boolean;
  createdBy: string;
  nextRunAt: string | null;
  lastRunAt: string | null;
}

export interface ArgusWatch {
  orgId: string;
  orgName: string;
  everyMinutes: number;
  focus: string;
  lastCheck: string;
  createdAt: string;
}

export interface OrgSettings {
  comm_policy: "open" | "structured";
  max_chain_depth: number;
  daily_token_budget: number;
  default_model?: ModelBinding;
  task_policy?: TaskPolicy;
  meetings?: MeetingSettings;
  world?: WorldSettings;
}

export interface WorldSettings {
  enabled?: boolean; // agents have bodies and presence tools (default on)
  witness?: boolean; // people in a room notice each other's movements and actions
  rules?: string; // the scene's premise/rules, added to every agent's prompt
}

export interface TaskPolicy {
  create?: boolean;
  assign?: "anyone" | "reports" | "self";
  edit?: "any" | "involved" | "assigned";
  require_review?: boolean;
}

export interface MeetingSettings {
  max_participants?: number;
  max_rounds?: number;
  default_rounds?: number;
  default_style?: string;
  create_tasks?: boolean;
}

export interface Org {
  id: string;
  name: string;
  description: string;
  kind: "user" | "system";
  status: "running" | "paused";
  workspace: string | null;
  settings: OrgSettings;
  layout: Record<string, unknown>;
  createdAt: string | null;
  agentCount?: number;
  openTasks?: number;
  workingAgents?: number;
}

export interface Relationship {
  id: string;
  orgId: string;
  fromId: string;
  toId: string;
  kind: "manages" | "peer" | "advises" | "custom";
  label: string;
}

export interface Channel {
  id: string;
  orgId: string;
  kind: "channel" | "dm" | "meeting";
  key: string;
  name: string;
  topic: string;
  members: string[];
  notify: "all" | "mentions";
  createdBy: string;
  archived: boolean;
  lastMessageAt?: string | null;
  messageCount?: number;
}

export interface Message {
  id: string;
  orgId: string;
  channelId: string | null;
  taskId: string | null;
  replyTo: string | null;
  senderType: "user" | "agent" | "system";
  senderId: string;
  kind: string;
  content: string;
  depth: number;
  turnId: string | null;
  meta: Record<string, unknown>;
  createdAt: string;
}

export type TaskStatus =
  | "backlog"
  | "todo"
  | "in_progress"
  | "blocked"
  | "review"
  | "done"
  | "cancelled";

export interface Task {
  id: string;
  ref: string;
  orgId: string;
  boardId: string;
  number: number;
  title: string;
  description: string;
  acceptance: string;
  status: TaskStatus;
  priority: "low" | "normal" | "high" | "urgent";
  assigneeId: string | null;
  reporterId: string;
  reviewerId: string | null;
  parentId: string | null;
  dependsOn: string[];
  watchers: string[];
  labels: string[];
  result: string;
  position: number;
  dueAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  closedAt: string | null;
}

export interface TaskEvent {
  id: number;
  actorId: string;
  kind: "created" | "status" | "assignee" | "comment" | "edit";
  data: Record<string, unknown>;
  createdAt: string | null;
}

export interface Board {
  id: string;
  orgId: string;
  name: string;
  description: string;
  columns: { key: TaskStatus; name: string; wip_limit?: number }[];
}

export interface OrgSnapshot {
  org: Org;
  agents: Agent[];
  relationships: Relationship[];
  channels: Channel[];
  boards: Board[];
  mcpServers: { id: string; name: string; transport: string }[];
}

export interface Turn {
  id: string;
  orgId: string;
  agentId: string;
  status: "running" | "completed" | "failed" | "awaiting_approval" | "stopped";
  triggerMessageIds: string[];
  steps: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  summary: string;
  error: string | null;
  attempts: number;
  startedAt: string | null;
  endedAt: string | null;
}

export interface WireMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
}

export interface LlmCall {
  id: string;
  turnId: string | null;
  agentId: string;
  purpose: "turn" | "compaction" | "meeting";
  provider: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  latencyMs: number;
  error: string | null;
  createdAt: string | null;
  response: {
    content?: string;
    reasoning?: string;
    tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  };
  request?: { messages: WireMessage[]; tools: string[]; model: string; provider: string };
}

export interface ToolCall {
  id: string;
  turnId: string | null;
  agentId: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "ok" | "error" | "denied";
  result: string;
  durationMs: number;
  createdAt: string | null;
}

export interface TurnDetail extends Turn {
  llmCalls: LlmCall[];
  toolCalls: ToolCall[];
  inbox: Message[];
  sent: Message[];
}

export interface Approval {
  id: string;
  orgId: string;
  agentId: string;
  turnId: string | null;
  toolCallId: string;
  tool: string;
  args: Record<string, unknown>;
  status: "pending" | "approved" | "denied";
  note: string;
  createdAt: string | null;
  decidedAt: string | null;
}

export interface InboxItem {
  id: string;
  kind: "message" | "approval" | "feature_request" | "error" | "alert";
  refId: string;
  title: string;
  read: boolean;
  createdAt: string | null;
}

export interface FeatureRequest {
  id: string;
  orgId: string | null;
  requesterId: string;
  title: string;
  description: string;
  rationale: string;
  status: "open" | "triaged" | "accepted" | "in_progress" | "done" | "rejected";
  resolution: string;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ProviderModel {
  id: string;
  vision?: boolean;
  context_window?: number;
  input_per_mtok?: number;
  output_per_mtok?: number;
}

export interface Provider {
  id: string;
  name: string;
  type: "openai" | "openrouter" | "openai_compatible" | "anthropic" | "ollama" | "mock";
  baseUrl: string | null;
  hasApiKey: boolean;
  models: ProviderModel[];
  defaultModel: string | null;
  isDefault: boolean;
  options: Record<string, unknown>;
}

export interface McpServer {
  id: string;
  orgId: string | null;
  name: string;
  description: string;
  transport: "stdio" | "http";
  command: string | null;
  args: string[];
  cwd: string | null;
  url: string | null;
  enabled: boolean;
  envKeys: string[];
  headerKeys: string[];
  status: "starting" | "ready" | "error" | "stopped";
  error: string | null;
  toolCount: number;
}

export interface CatalogTool {
  name: string;
  description: string;
  category: string;
  defaultApproval: "auto" | "ask";
  supervisorOnly: boolean;
  sideEffects: boolean;
  parameters: Record<string, unknown>;
}

export interface Template {
  key: string;
  name: string;
  description: string;
  plugin?: string | null;
  agents: { name: string; role: string }[];
}

export interface Artifact {
  id: string;
  orgId: string;
  agentId: string | null;
  taskId: string | null;
  path: string;
  title: string;
  mime: string;
  size: number;
  createdAt: string | null;
}

export interface Stats {
  agents: {
    agentId: string;
    name: string;
    calls: number;
    inputTokens: number;
    outputTokens: number;
    costUsd: number;
    avgLatencyMs: number;
    errors: number;
  }[];
  days: { day: string; inputTokens: number; outputTokens: number; costUsd: number; calls: number }[];
  models: { model: string; calls: number; tokens: number; costUsd: number }[];
  tools: { name: string; calls: number; avgMs: number; failures: number }[];
  turns: Record<string, number>;
  heatmap: { dow: number; hour: number; calls: number }[];
}

export interface PantheonEvent {
  seq: number | null;
  type: string;
  orgId: string | null;
  agentId: string | null;
  ts: number;
  payload: Record<string, unknown>;
}
