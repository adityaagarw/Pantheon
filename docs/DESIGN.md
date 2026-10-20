# Pantheon Studio: interface design

Agent tools today are chat windows with a sidebar. You type, something happens out of
sight, text comes back. Pantheon runs organizations: many agents working at once,
handing work to each other, stuck or fine, cheap or expensive. The interface has to make
an organization visible the way a city is visible from above, and let you dive from the
skyline into one agent's thoughts without losing your place.

## Ideas

1. **The org is a living map, not a list.**
   - **What's on it:** the Canvas shows every agent as a node, placed by the org chart
     (or the DAG, in flow mode).
   - **Movement:** work moves on it. A message is a pulse travelling from sender to
     recipient; a task hand-off is a heavier pulse; a step in a flow lights its edge.
   - **Agent state:** an agent's ring shows what it's doing (thinking, using a tool,
     waiting for you, failing, idle), and its node breathes while it works.

2. **Semantic zoom.** Zoom is how you go from overview to detail; there's no separate
   page for it.
   - **Far out:** the org's shape and where work is moving.
   - **Closer:** each agent becomes a card with its live line of thought and current
     tool.
   - **Closest:** the card opens into the agent's console (chat, reasoning, tools,
     files) in place.

3. **Every surface is a pane, and every pane is yours.**
   - Chat, Stage, Board, Comms, Sessions, Memory, Files, Team, Inspector and Timeline
     are panes in a dock.
   - Each can be dragged, split, tabbed, collapsed to a sliver, maximized to the whole
     window, or floated.
   - The arrangement is saved per org, so a coding org and a book studio each keep the
     layout that suits them.

4. **Time is a dimension.** Every event is in a gap-free log, so the org can be replayed.
   - The Timeline at the bottom is a scrubber over the org's history, with a density
     strip showing when work happened.
   - Drag it back and the Canvas shows the org as it was: who was working, what moved
     where.
   - Let go and it returns to live.

   That's a DVR for an organization: "what happened at 3 a.m.?" becomes a drag, not a
   log search.

5. **One command line for everything.**
   - ⌘K / Ctrl+K opens the command bar: go anywhere, open any pane, find any agent,
     message, task or file, run any action ("pause the org", "steer Mira", "new flow").
   - Writing `@Mira fix the Ch 8 layout` sends it.

6. **Calm by default, loud when it matters.** A quiet org is a dark, still map. Colour and
   motion are spent on what changed: an agent waiting for approval glows amber until you
   act; a failure is red and stays red until it's handled. Nothing blinks for decoration.

## Visual language

- **Surfaces:**
  - **Background:** near-black with a deep blue cast, plus a faint dot grid that moves
    with the Canvas (parallax) so space reads as space.
  - **Panes:** layered glass, translucent with a hairline edge and a soft inner light at
    the top. Depth comes from blur and light, not drop shadows.
- **Colour:**
  - **Agents:** each agent has a hue, derived from its name, that is the same
    everywhere: its node, its messages, its pulses and its name in text.
  - **Accents:** violet for Pantheon itself; amber for waiting on you; red for failure;
    green for done.
- **Type:**
  - Geist for the interface.
  - Geist Mono for tools, code and numbers.
  - A wide display weight for org names and big numbers.
- **Motion:** springs, not easings, so panes settle like objects. Pulses travel at a
  speed you can follow. Anything that moves while you aren't looking is something you
  should know about. Motion is off when the OS asks for reduced motion.
- **Density:** generous at the top level, dense inside a pane. Every pane has a header
  with its title, a live count and its controls (collapse, maximize, float).

## Structure

```
┌ rail ┬──────────────── command bar (org · ⌘K · live status) ───────────────┐
│ orgs │ ┌─ Canvas ─────────────────────────┐ ┌─ Chat · Inspector ──────────┐ │
│  as  │ │  agents, pulses, semantic zoom   │ │  tabs, collapsible          │ │
│ glyphs│ │                                  │ ├─ Board · Stage ─────────────┤ │
│      │ └──────────────────────────────────┘ └─────────────────────────────┘ │
│      ├─ Timeline: density strip and scrubber (live ◉) ──────────────────────┤
└──────┴───────────────────────────────────────────────────────────────────────┘
```

- **Rail:** each org as a glyph with a live activity ring. Home, Zeus, Argus, Kronos,
  Packs and Settings sit below.
- **Command bar:** the org name, connection and live status, the ⌘K field, and global
  actions (pause or resume the org, the inbox with its count).
- **Dock:** every pane, laid out per org, with a reset to the default.
- **Timeline:** always present, collapsible to a single line.

## Build order

1. Design tokens and the glass surface components.
2. The shell: rail, command bar, dock with every v1 view as a pane, layout saved per org.
3. The Canvas: org graph, agent nodes with live state, message and task pulses, semantic
   zoom into the agent console.
4. The command bar (⌘K) with navigation, search and actions.
5. The Timeline: density strip, scrubbing, replay on the Canvas.
