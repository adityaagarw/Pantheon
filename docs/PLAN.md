# Pantheon 2.0 (Pantheon Studio)

A native multi-agent organisation platform: build orgs like building blocks, watch and steer
them, and extend everything with packs and plugins. One app on Windows, macOS and Linux,
with no Docker.

## Decisions

- **Shell:** Tauri 2 (Rust). It starts and stops the engine, owns the window, tray,
  updates and native capabilities (screen capture, input, files).
- **Engine:** Pantheon v1's Python runtime (FastAPI, LangGraph, tools, Stage), ported
  selectively and bundled as a self-contained sidecar. Postgres becomes SQLite (FTS5 for
  memory search, sqlite-vec for embeddings). Docker paths become the app data directory.
- **UI:** v1's React UI moved from Next.js to Vite + React Router, served by Tauri. Every
  pane is dockable, resizable and collapsible, and the layout is saved per org.
- **Orgs:** each org picks a mode:
  - **Flow:** a DAG of steps. Each agent gets its inputs, produces its output and hands
    off. No free-form chatter.
  - **Team:** v1's free-form messaging, quieter by default.

  Both have an org chart for oversight; a lead can steer its reportees.
- **Fresh history.** v1's private history never comes along.

## Layout

```
apps/desktop/      Tauri shell (Rust): sidecar lifecycle, native computer use, tray
engine/            Python sidecar (from v1 server/): runtime, tools, API
ui/                Vite + React UI (from v1 web/)
packs/             built-in packs: stage libraries, skins, assets, characters, and plugins
                   (a pack with Python): agent kinds (CLI, third-party, human), CUA container
templates/         the 2-3 built-in org templates
```

## Extension points

- **Packs:** data, no code, added by the user from a folder or zip. Each has a
  `pack.json` with id, kind, version and contents.
  - `library`: JS libraries agents can use on the Stage (three.js, GSAP, d3…).
  - `skin`: UI themes and office looks.
  - `assets`: models, characters, icons and props.

  v1's originals ship as the default packs.
- **Plugins:** code that extends the engine and UI through a declared API. Each has a
  `plugin.json` plus a Python entry point and optional UI contributions. Plugins add
  tools, agent kinds, panels and event handlers.
- **Agent kinds:** an agent is a provider of turns.
  - `llm`: v1's runtime.
  - `cli`: Claude Code or Codex driven over their CLIs in a workspace.
  - `remote`: third-party agents (Hermes, Grok bots…) over HTTP or MCP.
  - `human`: a person, whose turns arrive through an inbox in the UI, for human in the
    loop.

  All kinds share the same messages, tasks, flows and oversight.
- **Templates:** an org saved as a template (agents, prompts, chart, flows, packs used,
  no data) to reuse or share as a file. A few minimal built-ins.

## Requirements (from the user, 2026-10-12/13)

Every one of these is a must. Each milestone below says which it delivers.

1. **DAG flows.** An org can run as a directed acyclic graph of steps, with no free-form
   chatter: each step names its agent, its inputs (upstream outputs) and what it hands
   on. There's a visual editor, and the runner shows each step live.
2. **Asset packs (and skin packs).** Characters, props, models, icons and office themes
   come as packs the user can add and remove. v1's originals are the default packs.
3. **Agent-platform plugin.** Any AI tool or agent platform can be a member of an org:
   - CLI agents: Claude Code, Codex;
   - agent frameworks: Hermes Agent, dots;
   - bots: Grok bots;
   - other runtimes over HTTP, MCP or A2A;
   - a human (human in the loop).

   They share messages, tasks, flows and oversight with native agents.
4. **Libraries on demand.** Agents' Stage libraries (three.js, GSAP, d3, Pixi…) are packs
   that are added or removed at run time, and given to an org or agent, with docs that
   follow automatically.
5. **Proper workspaces and worktrees at org creation.**
   - Creating an org makes its workspace: a new folder, or a folder or git repo on this
     computer, with git initialized.
   - Each agent that writes code gets its own git worktree and branch, and the lead
     merges.
   - The setup is shown and editable in the create flow, not bolted on later.
6. **Every pane collapsible and expandable,** with layouts saved; a UI that's a step
   beyond anything else. An org opens on its 3D office, the main view.
7. **Leads steer reportees.**
8. **Templates:** 2-3 minimal built-ins (prompts written by Claude), plus saving and
   sharing any org as a template.
9. **Native computer use and Shadow,** with the Docker CUA as an optional plugin.
   Shadow has one option: this computer.
10. **Server mode.** Pantheon can also run as a server, reached from a browser on a
    port the user chooses (and on the network, if they allow it), without the
    desktop window.
11. **Worktree page.** A pane showing the org's workspace and each agent's worktree as a
    file tree.
12. **Pantheon Host built in.** What v1's Pantheon Host did on the host computer is part
    of the app itself, with nothing separate to install.
13. **An installable package.** The whole software ships as an installer anyone can
    download and run, with nothing else to set up (no Python, Node or Docker).

## Milestones

1. **Native foundation:** monorepo; engine on SQLite with no Docker; Tauri launches the
   engine; UI on Vite inside the Tauri window; v1 features working natively (agents,
   chat, Stage, files, native Playwright browser).
2. **UI shell (req 6):** the new interface (docs/DESIGN.md): dockable, collapsible panes,
   a visual redesign, layout persistence.
3. **Packs (req 2, 4):** pack format, loader and manager UI; v1's libraries, skins and
   assets as default packs; libraries added and removed per org or agent. *Done
   (2026-10-13): packs/README.md. Library packs plus npm on demand; the office's
   furniture, characters and themes as the office-classic pack. Still to come: UI skins
   (the Studio's own colours) as packs.*
4. **Org model (req 1, 5, 7, 8, 11):** org creation with workspace and worktrees, and
   the worktree page; flow mode
   (DAG editor and runner), team mode, lead steering, 2-3 templates, save and share orgs
   as templates. *Done (2026-10-13): services/workspaces.py (new folder, picked folder or
   clone; git; a worktree and branch per agent; git tools; the Worktrees pane),
   services/flows.py (flows, runs, finish_step, the Flows pane; per-org mode team or
   flow), the steer tool for leads, and templates (solo, build team, research flow; v1's in
   the templates-classic pack, off by default; save as template, import a template file).*
5. **Plugins (req 3):** plugin API; agent kinds `cli` (Claude Code, Codex), `remote`
   (Hermes, dots, Grok bots, HTTP, MCP, A2A) and `human` as built-in plugins. *Done
   (2026-10-14): app/agents/kinds.py (PackApi.agent_kind), app/agents/external.py (turns
   for other kinds; Pantheon's tools through the stdio MCP bridge app/mcpbridge.py), the
   agent-kinds pack (cli: Claude Code, Codex, Hermes, any command; remote: OpenAI-compatible
   incl. Grok, A2A, webhook), services/humans.py (a person answers from the Console). Claude
   Code and Hermes checked for real; Codex needs `codex login` on this machine. "dots" has
   no preset of its own: A2A, webhook or a command covers it.*
6. **Native capabilities (req 9, 12):** native computer use and Shadow (this computer
   only) from the shell, with the CUA container as an optional plugin; Kronos and
   Pantheon Host folded into the app. *Done (2026-10-14): app/computer (backends off,
   native, or a pack's: cua-desktop; Settings → This computer; Stop and the top-left
   corner failsafe; the "X is using your computer" banner), native Shadow
   (app/computer/recorder.py: windows, clicks, typing counted not recorded, screenshots,
   private windows skipped), notebooks on this computer, and Kronos's operations in the
   engine (app/host/ops.py; allowed folders as a setting; code changes only when running
   from source). Done in the engine (Python), not the Rust shell, so server mode gets it too.*
7. **Distribution (req 10, 13):** installers for Windows, macOS and Linux with a bundled
   Python, auto-update, and server mode (`pantheon serve --port N`, plus a setting in
   the app) that serves the UI to browsers. *Done (2026-10-14): scripts/bundle_engine.py
   (a relocatable python-build-standalone CPython with the engine's dependencies, the
   engine, packs and built UI, as the app's `runtime/` resource), installers per system
   (NSIS for the user, .app/.dmg, .deb/.rpm/AppImage; tauri.bundle.json), the release
   workflow that builds, installs and starts each one (scripts/smoke_installer.py) and
   drafts releases with latest.json (scripts/release_files.py), the updater plugin and
   banner, server mode (services/server.py, app/core/access.py access codes,
   app/core/webui.py serving the UI; Settings → Server; `pantheon-serve`), and Chromium
   downloaded on first use. The Windows installer is checked locally; macOS and Linux by
   the workflow.*
8. **Benchmarks redone (2026-10-14):** six new hard, team-shaped tasks from real work
   (finance close, contract review, API migration, SLA analytics, security audit,
   warehouse allocation) replace the eight old ones; see engine/bench/README.md.
