/** Wire types for the Pantheon API (mirrors server/app/*_to_dict). */

export type RuntimeStatus =
  | "idle"
  | "working"
  | "awaiting_approval"
  | "error"
  | "retrying"
  | "cooling_down"
  | "queued"; // waiting for a free slot (Settings > Agents)

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
  video?: boolean; // the model accepts video (unset: what its server says)
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
  /** Stage library ids this agent may import; null: its organization's choice. */
  libraries?: string[] | null;
  /** What runs it: llm (Pantheon's own), cli, remote, human, or a plugin's kind. */
  kind?: string;
  /** The kind's settings (a CLI's command, a remote agent's URL…). */
  runner?: Record<string, unknown>;
  worktree: string | null;
  limits: { max_steps_per_turn?: number; max_turns_per_hour?: number };
  permissions: { tasks?: TaskPolicy };
  avatar: Avatar;
  status: "active" | "standby" | "paused" | "disabled";
  runtimeStatus: RuntimeStatus;
  runtimeDetail: string;
  consecutiveFailures: number;
  retryAt: string | null;
  isSupervisor: boolean;
  metaRole?: "zeus" | "argus" | "kronos" | null;
  location?: AgentLocation | null;
  /** Needs, mood, traits and wants, in organizations where life is on. */
  life?: AgentLife | null;
  createdAt: string | null;
}

export interface AgentLife {
  needs: Record<"energy" | "food" | "hygiene" | "social" | "fun" | "comfort", number>;
  mood: "great" | "good" | "okay" | "low" | "miserable";
  traits: string[];
  wants: { id: string; text: string; since: string }[];
  done: { text: string; at: string }[];
  doing?: string | null;
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
  /** Glow color, e.g. screens and status lights. */
  emissive?: string;
  /** < 1 for glass. */
  opacity?: number;
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

/** A Stage library: names pages can import, served from its pack (engine app/packs.py). */
export interface LibraryInfo {
  id: string;
  name: string;
  summary: string;
  pack: string;
  imports: string[];
  /** Part of the Stage itself (three.js, KaTeX): always on. */
  core: boolean;
  requires: string[];
  hasDocs: boolean;
}

export type PackKind = "library" | "furniture" | "characters" | "themes" | "assets" | "templates" | "plugin";

/** A pack: libraries, 3D assets, templates and plugin code, added and removed as a unit. */
export interface PackInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  path: string;
  /** Ships with Pantheon: it can be switched off, not removed. */
  bundled: boolean;
  core: boolean;
  enabled: boolean;
  kinds: PackKind[];
  requires: string[];
  libraries: LibraryInfo[];
  assets: string[];
  templates: string[];
  tools: string[];
  /** How much office content it adds. */
  office: { items: number; characters: number; themes: number };
  module: string;
  error: string | null;
}

export interface PackListing {
  dir: string;
  packs: PackInfo[];
  libraries: LibraryInfo[];
}

export type StageDocKind = "sheet" | "pdf" | "notebook" | "markdown";

/** A spreadsheet, PDF, notebook or Markdown document on the Stage (list form). */
export interface StageDocInfo {
  id: string;
  orgId: string;
  kind: StageDocKind;
  title: string;
  version: number;
  createdBy: string;
  updatedBy: string;
  createdAt: string | null;
  updatedAt: string | null;
  summary: string;
}

export interface SheetCell {
  v?: string | number | boolean | null;
  f?: string;
}

export interface SheetData {
  sheets: { name: string; cells: Record<string, SheetCell> }[];
}

export interface PdfHighlight {
  id: string;
  page: number;
  text: string;
  note: string;
  by: string;
}

export interface PdfData {
  attachmentId: string;
  name: string;
  pages: number | null;
  page: number;
  highlights: PdfHighlight[];
}

export type NotebookOutput =
  | { type: "stream"; name: string; text: string }
  | { type: "result"; text: string; html?: string }
  | { type: "image"; data: string }
  | { type: "error"; ename: string; evalue: string; traceback: string };

export interface NotebookCell {
  id: string;
  type: "code" | "markdown";
  source: string;
  outputs: NotebookOutput[];
  count: number | null;
  status: "idle" | "running" | "ok" | "error";
  by: string;
}

export interface NotebookData {
  cells: NotebookCell[];
}

export interface MarkdownData {
  text: string;
}

export interface StageDoc extends StageDocInfo {
  data: SheetData | PdfData | NotebookData | MarkdownData;
}

/** The last change to a document, as published live. */
export interface StageDocEvent extends StageDocInfo {
  changes?: Record<string, Record<string, SheetCell | null>>;
  source?: string;
  focus?: { page: number };
  data?: StageDoc["data"];
}

export interface WhiteboardInfo {
  id: string;
  orgId: string;
  title: string;
  version: number;
  updatedBy: string;
  hasThumbnail: boolean;
  thumbVersion: number; // the board version its picture shows
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
  kind: "document" | "image" | "video" | "other";
  status: "processing" | "ready" | "failed";
  error: string;
  info: { pages?: number; chars?: number; width?: number; height?: number; chunks?: number; note?: string; truncated?: boolean; sheets?: number; rows?: number; duration?: number; audio?: boolean };
  shared: boolean;
  uploadedBy: string;
  createdAt: string | null;
}

/** The small form carried on a message. */
export interface AttachmentBrief {
  id: string;
  name: string;
  kind: "document" | "image" | "video" | "other";
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
  /** "" on the Stage list, "archive" when put away. */
  folder?: string;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface AgentSchedule {
  id: string;
  agentId: string;
  message: string;
  cron: string | null;
  everySeconds?: number | null;
  endsAt?: string | null;
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

/** Where a recording was made: this computer (older ones may say sandbox, browser or desktop). */
export type ShadowSource = string;

export interface ShadowSession {
  id: string;
  orgId: string;
  agentId: string;
  agentName: string | null;
  title: string;
  note: string;
  source: ShadowSource;
  status: "recording" | "stopped" | "learning" | "learned";
  code: string | null;
  /** "My computer" recordings: the computer Pantheon Host is recording it on. */
  eventCount: number;
  startedAt: string;
  endedAt: string | null;
}

export interface ShadowEvent {
  id: string;
  t: number;
  kind: "click" | "input" | "submit" | "navigate" | "window" | "typing" | "screen" | "note" | "key";
  data: Record<string, unknown>;
  image: string | null;
}

export interface Playbook {
  id: string;
  orgId: string;
  agentId: string;
  sessionId: string | null;
  title: string;
  content: string;
  createdAt: string;
  updatedAt: string | null;
}

/** This computer (Pantheon Host, built in): the source Kronos may change and its folders. */
export interface HostStatus {
  connected: boolean;
  info: {
    repo: string | null;
    branch?: string;
    allowed: string[];
    os?: string;
    host?: string;
    capabilities?: string[];
    openChanges?: { id: string; title: string; path: string }[];
  } | null;
}

/** The sandbox desktop (Docker): what's set up and running. */
export interface SandboxStatus {
  docker: "missing" | "stopped" | "running";
  image: boolean;
  container: "none" | "stopped" | "running";
  healthy: boolean;
  image_name: string;
  setup: { running: boolean; step: string; progress: number; error: string | null };
  view?: { url: string }; // the desktop in a browser, through Pantheon
}

/** Which computer agents use, and who's using it now. */
export interface ComputerStatus {
  backend: string;
  paused: string | null;
  active: { agentId: string; agent: string; orgId: string; at: number } | null;
  inUseBy: string | null;
  backends: { id: string; label: string; description: string }[];
}

export interface ArgusWatch {
  orgId: string;
  orgName: string;
  everyMinutes: number;
  focus: string;
  lastCheck: string;
  createdAt: string;
}

/** What the new-organization wizard offers (engine app/services/wizard.py). */
export interface WizardTools {
  /** Every agent has these. */
  base: string[];
  kinds: Record<"solo" | "team" | "flow", string>;
  tools: { name: string; category: string; summary: string; sideEffects: boolean }[];
}

/** One agent as Zeus drafts it in the wizard. */
export interface WizardAgent {
  name: string;
  role: string;
  lead: boolean;
  persona: string;
  tools: string[];
}

export interface OrgSettings {
  comm_policy: "open" | "structured";
  max_chain_depth: number;
  daily_token_budget: number;
  /** "auto": shell commands (run_command, computer_shell) run without asking; agents set to deny still can't. */
  shell_approval?: "ask" | "auto";
  /** "ste": every agent writes in ASD-STE100 Simplified Technical English. */
  writing?: "plain" | "ste";
  /** Agents of this org working at once; 0: only the global limit. */
  max_concurrent?: number;
  /** The workspace picked on the user's computer (via Pantheon Host): where it is there. */
  default_model?: ModelBinding;
  /** The model the acceptance check runs on; absent: the model of the agent handing in. */
  checker_model?: ModelBinding;
  task_policy?: TaskPolicy;
  meetings?: MeetingSettings;
  world?: WorldSettings;
  /** Stage library ids the org's pages may import; absent: every installed library. */
  libraries?: string[] | null;
  git?: GitSettings;
  /** team: agents coordinate freely; flow: work runs through flows. */
  mode?: "team" | "flow";
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
  kind: "message" | "approval" | "feature_request" | "error" | "alert" | "human";
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
  /** Accepts video; unset: what the server's /models says (Radiance lists capabilities). */
  video?: boolean;
  /** Send earlier reasoning back with each request (preserve_thinking); not every server supports it. */
  preserve_thinking?: boolean;
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
  /** builtin: ships with Pantheon; pack: from a pack; mine: saved by the user. */
  source: "builtin" | "pack" | "mine";
  plugin?: string | null;
  mode: "team" | "flow";
  /** What the template suggests for its workspace. */
  workspace: { git?: boolean; worktrees?: boolean } | null;
  flows: string[];
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

/** An organization's git settings (engine services/workspaces.py). */
export interface GitSettings {
  enabled: boolean;
  worktrees?: boolean;
  base?: string;
}

/** The main checkout or an agent's worktree. */
export interface Worktree {
  path: string; // relative to the workspace; "." is the main checkout
  branch: string | null;
  main: boolean;
  agentId: string | null;
  agentName?: string | null;
  changes: string[]; // "M src/app.py", "?? notes.md"
  ahead: number;
  behind: number;
  last: { hash: string; subject: string; at: string } | null;
}

export interface WorktreeListing {
  root: string;
  git: GitSettings;
  worktrees: Worktree[];
}

export interface WorktreeDiff {
  branch: string;
  base: string;
  stat: string;
  committed: string;
  uncommitted: string;
  untracked: string[];
}

/** A flow's step: an agent and what to do (engine services/flows.py). */
export interface FlowNode {
  id: string;
  title: string;
  agentId: string;
  instructions: string;
  x: number;
  y: number;
}

export interface FlowGraph {
  nodes: FlowNode[];
  edges: { from: string; to: string }[];
}

export interface Flow {
  id: string;
  orgId: string;
  name: string;
  description: string;
  graph: FlowGraph;
  createdAt: string | null;
  updatedAt: string | null;
}

export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface FlowRun {
  id: string;
  flowId: string;
  orgId: string;
  number: number;
  input: string;
  status: "running" | "done" | "failed" | "cancelled";
  graph: FlowGraph;
  steps: Record<string, { status: StepStatus; output?: string; error?: string; startedAt?: string; finishedAt?: string; messageId?: string }>;
  createdAt: string | null;
  finishedAt: string | null;
}

/** A kind of agent (engine app/agents/kinds.py), and how the interface sets it up. */
export interface AgentKind {
  id: string;
  label: string;
  description: string;
  pack: string;
  presets: { id: string; label: string; description: string; runner: Record<string, unknown> }[];
  fields: {
    key: string;
    label: string;
    type: "text" | "secret" | "select" | "list" | "model" | "account";
    hint?: string;
    options?: [string, string][];
    presets?: string[];
  }[];
  /** Has a Check (installed, signed in, answering; which models). */
  checkable?: boolean;
}

/** What an agent kind's Check found (engine app/agents/connect/check.py). */
export interface KindCheck {
  ok: boolean;
  checks: { label: string; ok: boolean; detail: string }[];
  models?: { id: string; name: string }[];
  note?: string;
}

/** A sign-in or API key a command-line agent works with (engine services/accounts.py). */
export interface Account {
  id: string;
  tool: string;
  toolLabel: string;
  name: string;
  method: string;
  methodLabel: string;
  hasKey: boolean;
  status: { signedIn?: boolean; detail?: string; checkedAt?: string };
  coolingUntil: string | null;
  coolingReason: string;
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
  agents?: string[];
}

export interface AccountTool {
  id: string;
  label: string;
  methods: { id: string; label: string; signIn: boolean }[];
}

/** The tool's own login, running (what it printed, the links in it). */
export interface AccountSignIn {
  id: string;
  accountId: string;
  output: string;
  urls: string[];
  done: boolean;
  ok: boolean;
}

/** Server mode: Pantheon in a browser on a chosen port. */
export interface ServerStatus {
  enabled: boolean;
  port: number;
  running: boolean;
  error: string | null;
  accessCode: string;
  urls: string[];
}
