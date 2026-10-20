# Pantheon packs

Everything Pantheon uses beyond its core comes in packs. A pack is a folder with a
`pack.json` (Pantheon 1's `plugin.json` also works). The packs here ship with the app.
Packs you add go in your data folder (`<data>/packs`), and the **Packs** page in the app
adds, switches off and removes them. A pack of yours with the same id as one here
replaces it, so a newer three.js is just a pack.

A pack can hold any of:

| | What it gives you |
|---|---|
| **libraries** | Stage libraries agents' pages import by name (`import { gsap } from "gsap"`), served offline and copied into packaged sites. Each comes with the docs agents read. An organization, or one agent, chooses which it gets. |
| **templates** | Organizations or *scenarios* people can create from Home → New organization: agents with personas, relationships, channels, **a physical space** (rooms + furniture), **objects** (who holds what, what lies where), starting **positions**, and **world rules**. |
| **assets** | 3D things for offices and scenes: `.glb` files (scaled to real size with `size_m` or `height_m`) or procedural models built from boxes, cylinders, spheres and cones. They appear in the office designer and can be the look of objects. |
| **module** | Python, which makes the pack a plugin: extra agent **tools** (`api.tool`) and **object behaviour** (`api.on_use`), e.g. handcuffs that restrain, a radio that reaches every officer, a door that locks. |

A pack can ship switched off with `"enabled": false`: it's listed on the Packs page and does
nothing until the user switches it on. `templates-classic/` (Pantheon 1's eighteen
organization templates) is one.

`town-square/` is a complete example (a market-day social simulation) and its `plugin.py` is the
reference for the Python API. The library packs (`three/`, `gsap/`, `d3/`…) are the examples
for libraries. Restart the app after changing a plugin's Python; everything else is picked
up by **Reload** on the Packs page.

## The office: furniture, characters and themes

`office-classic/` is the original office as a pack, and the example to copy. Its
`pack.json` says `"office": "office.json"`, and that file holds:

```jsonc
{
  "kits":   { "furniture": { "scale": 2 } },            // model folder -> model units to meters
  "models": { "furniture/desk": { "min": [x, y, z], "max": [x, y, z] } },  // file: models/<key>.glb
  "items":  [{ "key": "workstation", "label": "Workstation (monitor)", "category": "Workstations",
               "parts": [{ "model": "furniture/desk", "x": 0, "z": -0.35 }],   // meters, rot in radians
               "size": [1.47, 1.73], "blocking": true,
               "seats": [{ "x": 0, "z": 0.62, "facing": 3.1416 }], "workstation": true,
               "thumb": "thumbs/furniture/desk.png" }],
  "rigs":   { "kenney": { "scale": 1.7, "sitLift": 0.3,
                          "clips": { "idle": "idle", "walk": "walk", "sit": "sit", "wave": "interact-right",
                                     "yes": "emote-yes", "no": "emote-no", "thumbs": "emote-yes" } } },
  "characters": [{ "key": "female-a", "label": "Female a", "group": "People", "rig": "kenney",
                   "model": "models/people/character-female-a.glb",
                   "thumb": "thumbs/people/character-female-a.png", "default": true }],
  "themes": [{ "id": "classic", "label": "Classic studio", "floor": "oak", "sky": "#dfe5ec", "…": "…" }]
}
```

- Item and character keys are shared by every pack. Prefix yours with your pack's id
  (`mypack:lamp`). A later pack's key replaces an earlier one's, and the shipped packs
  come first.
- An item is built from models (`parts`), or from coloured shapes (`procedural`, the same
  parts as a pack's `assets`). `seats` are where people sit, and `workstation` makes it
  an assignable desk.
- A character uses a rig from its own pack: its animation clip names and size (`scale`,
  or `fit` to a height in meters). `default: true` characters are the ones agents get
  when they haven't picked one.
- A theme is a look: `floor` (one of oak, walnut, concrete, polished, slate, epoxy,
  carpet, tile, grass, asphalt), and the `sky`, `hemi`, `sun`, `ambient`, `wall`, `glass`,
  `trim`, `sign` and `rooms` colours. Anything it leaves out is classic. A new theme
  gets the classic floor plan when an office is generated.
- The engine checks the content when the pack loads. A missing model or an unknown rig is
  shown on the Packs page as the pack's error.

## Libraries

```jsonc
{
  "id": "postprocessing", "name": "postprocessing", "version": "6",
  "description": "Cinematic 3D effects for three.js.",
  "libraries": [{
    "id": "postprocessing",                    // the URL segment: /api/v1/stage-lib/postprocessing/…
    "name": "postprocessing",
    "summary": "One line for the library list.",
    "imports": { "postprocessing": "postprocessing.js" },  // import-map name -> file in the pack
    "requires": ["three"],                     // libraries it imports; they come along
    "docs": "docs.md",                         // what agents read in stage_docs
    "core": false                              // core: the Stage itself needs it (always on)
  }]
}
```

- An import name ending in `/` maps a folder (`"three/addons/": "addons/"`).
- Files are ES modules. Bare imports resolve through the page's import map, so a library
  that imports `three` shares the Stage's three.js.
- Any npm package becomes a library pack from the Packs page, or when Zeus runs
  `install_library`. It's fetched from esm.sh, every module it imports is downloaded, and
  its README becomes the docs (`engine/app/services/npmpack.py`).

## Scenario templates

A template is an org export plus a few scene fields (see `town-square/templates/saturday-market.json`):

```jsonc
{
  "name": "…", "description": "…",
  "settings": { "world": { "enabled": true, "witness": true, "rules": "The premise and rules…" } },
  "agents": [{ "name": "Marco", "role": "Chef", "team": "Market", "persona": "…",
               "tools": [{"name": "send_message"}], "avatar": {"character": "male-e"} }],
  "office": { "width": 31, "depth": 25, "floor": "asphalt", "autoDesks": false,
              "rooms": [{ "name": "Market", "type": "custom", "x": 1, "z": 14, "w": 16, "d": 9,
                          "walls": "none", "door": "n" }],
              "items": [{ "kind": "plugin:town-square/market-stall", "x": 5, "z": 17.5, "rot": 3.14 },
                        { "kind": "workstation", "x": 21, "z": 14, "rot": 0, "agent": "Officer Reyes" }] },
  "objects": [{ "name": "chef's knife", "asset": "plugin:town-square/knife", "holder": "Marco" },
              { "name": "coffee cup", "asset": "plugin:town-square/coffee-cup", "room": "Café" }],
  "positions": { "Marco": "Market" }
}
```

- Coordinates are meters; `rot` is radians. Item `kind` is a built-in furniture key (see the
  office designer), `asset:<id>` or `plugin:<plugin>/<asset id>`.
- `autoDesks: false` stops Pantheon adding desks for people without a workstation; their home is
  their team's room (name the team after a room).
- `witness: true` makes people in a room notice each other's movements and actions (each
  notice wakes them, so it costs tokens; that's what makes social dynamics emerge).
- Every agent in a world-enabled org automatically has `look_around`, `move_to`, `emote`,
  `say_aloud`, `pick_up`, `put_down`, `give` and `use_object`.

## Agent kinds

A plugin can add a kind of agent: something other than Pantheon's own model loop that
takes an agent's turns. `agent-kinds/` is the example; it adds command-line agents
(Claude Code, Codex, Hermes Agent, any command) and remote agents (OpenAI-compatible
endpoints such as Grok, A2A agents, webhooks).

```python
async def run(turn):          # turn: app.agents.kinds.ExternalTurn
    # turn.inbox: the new messages, as text; turn.system: who the agent is and its org
    # turn.workdir: its folder (its git worktree, when the org uses them)
    # turn.bridge: Pantheon's tools as a stdio MCP server (.command, .args, .env,
    #              .mcp_config()) to hand to the agent's process
    # turn.runner: the agent's settings for this kind; turn.remember(**kept) keeps
    #              something (a session id) for the next turn
    await turn.emit("streamed text")   # shown live
    return "the reply"                 # delivered like any agent's; finishes flow steps

def register(api):
    api.agent_kind("mykind", "My agents", "What they are.", run,
                   presets=[{"id": "x", "label": "X", "description": "…", "runner": {…}}],
                   fields=[{"key": "url", "label": "Endpoint", "type": "text"}],
                   validate=lambda runner: runner)
```

Everything around the turn (claiming messages, the turn record, delivering the reply,
flow steps, failures and retries) is Pantheon's. A failed turn's messages wait for the
retry. A person (`human`) is a built-in kind.

## Python API

```python
def register(api):
    @api.tool("radio_call", "Speak on the police radio.", {"type": "object",
              "properties": {"message": {"type": "string"}}, "required": ["message"]})
    async def radio_call(args, ctx):          # ctx.org, ctx.agent, ctx.sender
        ...
        return "Heard on the radio by: …"      # what the agent sees

    @api.on_use("handcuffs")                  # glob on object name or asset key
    async def cuffs(event):                   # event.obj / .action / .actor / .target / .world
        event.obj.holder_id = event.target.id
        event.obj.state = {"restraining": event.target.name}
        return f"{event.target.name} is now in handcuffs."
```

Plugins run inside the engine with full access: only install ones you trust.
