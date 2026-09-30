# Pantheon plugins

A plugin is a folder here with a `plugin.json`. It can add any of:

| | What it gives you |
|---|---|
| **templates** | Organizations or *scenarios* people can create from Home → New organization: agents with personas, relationships, channels, **a physical space** (rooms + furniture), **objects** (who holds what, what lies where), starting **positions**, and **world rules**. |
| **assets** | 3D things for offices and scenes: `.glb` files (scaled to real size with `size_m` or `height_m`) or procedural models built from boxes, cylinders, spheres and cones. They appear in the office designer and can be the look of objects. |
| **module** | Python: extra agent **tools** (`api.tool`) and **object behaviour** (`api.on_use`), e.g. handcuffs that restrain, a radio that reaches every officer, a door that locks. |

`town-square/` is a complete example (a market-day social simulation) and its `plugin.py` is the
reference for the Python API. Restart the backend after adding or changing a plugin; Zeus →
*Assets & plugins* shows what loaded and any errors. In Docker, this folder is mounted at `/plugins`.

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

Plugins run inside the backend with full access: only install ones you trust.
