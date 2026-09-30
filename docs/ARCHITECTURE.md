# Architecture

```
Browser (Next.js, :3710)                               Backend (FastAPI, :8710)
┌──────────────────────────────┐   REST /api/v1   ┌───────────────────────────────────────────┐
│ Office (R3F) · Board · Comms │ ───────────────▶ │ services: orgs · comms · tasks · memory   │
│ Sessions · Team · Supervisor │ ◀─────────────── │           supervisor · artifacts          │
│ zustand store ← event reducer│   WS /api/v1/ws  │ runtime:  dispatcher → workers → LangGraph│
└──────────────────────────────┘ ◀══════════════  │ tools:    builtin · MCP manager · approvals│
                                                  │ events:   persisted log + live fan-out     │
                                                  └──────────────┬────────────────────────────┘
                                                                 │ asyncpg
                                                           PostgreSQL 16
```

## The agent runtime

Every agent is an **actor** with two durable pieces of state:

1. **An inbox** — rows in `deliveries`, one per (message, recipient).
2. **A LangGraph thread** — `agent:<id>`, checkpointed to Postgres after every graph step. It is the
   agent's continuous working memory across turns, compacted into a running summary as it grows.

### A turn

```
inbox ──► prepare (compaction) ──► model ──► gate (approvals) ──► tool × N (parallel Send) ─┐
                                     ▲                                                       │
                                     └───────────────────────────────────────────────────────┘
                                     └──► END (plain-text reply, no tool calls)
```

1. The dispatcher wakes a worker for any agent with due deliveries (event-driven, with a safety poll).
   A global semaphore bounds concurrency; each agent runs at most one worker.
2. The worker **claims** its pending deliveries (`FOR UPDATE SKIP LOCKED`, with a lease), appends them
   to the thread in **one atomic checkpoint** (`aupdate_state(as_node="inbox")`), marks them done, and
   runs the graph to quiescence. Message ids already in the thread (`seen`) are never injected twice.
3. The system prompt is rebuilt on **every** model call from live data: identity & persona, the org,
   colleagues and reporting lines, channels, the agent's open tasks, workspace, tool problems, and the
   conversation summary.
4. Tool calls run as separate LangGraph tasks. Each result is also stored by `tool_call_id`, so a call
   that completed before a crash is **never executed twice** on resume.
5. Tools configured with approval `ask` stop at the gate with `interrupt()`. The user decides in the
   inbox; the runtime resumes with `Command(resume=…)`. Denials are returned to the model as results.
6. A final plain-text reply is delivered automatically to anyone who DM'd the agent this turn and
   wasn't answered explicitly — except replies to replies, so agents can't ping-pong politely forever.

### Failure handling

| Failure | Behaviour |
|---|---|
| Transient provider error (timeout, 429, 5xx) | Retried with backoff inside the call (4 attempts), every attempt recorded. |
| Turn fails | Checkpoint kept; the turn resumes after 5s, 20s, 60s, 3m, 10m. After `max_turn_failures` the agent parks in *error*, you get an inbox item, and *Retry* resumes it. Other agents are unaffected. |
| Process crash / restart | Claimed deliveries are released, interrupted turns resume from their last checkpoint. |
| Tool error / timeout / bad arguments | Returned to the model as an error result (JSON-schema validated); never crashes the turn. |
| Broken MCP server | Isolated: the agent is told the server is unavailable; connections restart with backoff. |
| Runaway conversation | Every message carries a causal *depth*; deliveries beyond the org's `max_chain_depth` are dropped and logged. |
| Runaway agent | Per-turn step limit (forced wrap-up), per-agent turns/hour, per-org daily token budget (pauses the org). |
| You need it to stop | *Stop turn* cancels and closes the thread cleanly; *Pause* on agents or the whole org. |

### Why a custom checkpointer?

The official Postgres checkpointer needs async psycopg, which refuses Windows' Proactor event loop —
the loop required to spawn stdio MCP servers. `app/agents/checkpointer.py` implements LangGraph's saver
interface on the app's asyncpg engine instead (same data model, plus pruning of old checkpoints since
full history is recorded separately).

## Collaboration primitives (agent tools)

- **Messaging:** `send_message` (DM a colleague or the user), `post_message` / `read_channel` /
  `create_channel` / `list_channels`, `list_colleagues`. Org policy is *open* or *structured*
  (only related agents / shared channels).
- **Work:** `create_task` (assign, reviewer, parent, dependencies, acceptance criteria),
  `update_task` (status, result, comments, reassignment), `list_tasks`, `get_task`.
- **Meetings:** `hold_meeting` runs a bounded round-table; minutes go to shared memory, the meeting
  channel, and every participant's inbox.
- **Memory:** `remember` (private or shared) and `recall` (Postgres full-text search).
- **Workspace:** `read_file`, `write_file`, `edit_file`, `list_dir`, `search_files`, `run_command`
  (approval by default) — confined to the org workspace, which must live inside `PANTHEON_WORKSPACE_ROOTS`.
- **Web:** `web_search`, `fetch_url` (content marked untrusted).
- **MCP:** any stdio or Streamable-HTTP MCP server, granted per agent as `mcp:<server>:*` or
  `mcp:<server>:<tool>`.
- **Self-improvement:** `request_feature` files a request with the supervisor.

Permissions are re-resolved from the database on every call; a tool not granted to the agent is refused
at execution time regardless of what the model asks for.

## The supervisor

A built-in agent (`is_supervisor`) in the hidden system org `org_pantheon`, running on the same runtime.
It has admin tools to list/inspect/create/update organizations, hire/edit/remove agents, set
relationships, create channels, register and test MCP servers, assign goals, check org health, and
triage feature requests (updating their status and replying to the requesting agent across orgs).

## Observability

Everything is recorded: `turns`, `llm_calls` (full request, response, reasoning, tokens, cost, latency,
errors), `tool_calls`, `messages`, `task_events`, `approvals`, and a global, gap-free `events` log that
the UI replays over the WebSocket (`?after=<seq>` catch-up, bounded per-client queues, resync on
overflow). Token streaming (`agent.stream`) is live-only.

## Frontend

A single zustand store per org is fed by a pure event reducer (`web/src/store/reduce.ts`); on load it is
seeded from a snapshot plus the last 250 events and any running meetings.

### The office (`web/src/office`)

- **Design** (`design.ts`): an office is a list of placed catalog items and rooms, saved on the org as
  `layout.office`. Orgs without one get a generated, furnished default. `resolveDesign` assigns agents to
  workstations (explicit assignment → matching team area → first free), adds desks automatically when
  there are too few, and derives meeting seats (chairs inside meeting rooms), break spots (lounges and
  kitchens), walls with door gaps, and walkable obstacles.
- **Catalog** (`catalog.ts`): Kenney Furniture Kit models plus composites (workstations, conference
  tables). Precomputed footprints (`modelMeta.json`, regenerate with `node scripts/model-meta.mjs`).
- **Rendering**: furniture is drawn with one `InstancedMesh` per sub-mesh per model, so cost is flat in the
  number of desks. Characters are skinned Kenney Mini Characters (or the robot), one clone per agent.
- **Brain** (`brain.ts`): maps live state to a destination, pose and gesture (desk when working, stand
  and raise a hand when awaiting approval, meeting room seats during meetings, breaks when idle); paths
  are planned with grid A* and followed in `useFrame`.
- **Designer** (`DesignerLayer.tsx`, `DesignerPanel.tsx`): place/move/rotate/duplicate/remove items,
  draw and resize rooms, assign desks, undo/redo, save.
