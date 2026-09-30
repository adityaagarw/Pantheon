"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge, Button, cx, ErrorNote, Field, Input, Select } from "@/components/ui";
import type { Agent } from "@/lib/types";
import { useAssetVersion } from "./assets";
import { CATALOG, CATEGORIES, type Category } from "./catalog";
import { generateDesign, newId, type FloorStyle, type OfficeDesign, type RoomType, type Side, type WallKind } from "./design";
import { patchItem, patchRoom, useDesigner } from "./designerStore";

const ROOM_TYPES: { value: RoomType; label: string; hint: string }[] = [
  { value: "meeting", label: "Meeting room", hint: "Meetings are held here, using its chairs" },
  { value: "lounge", label: "Lounge", hint: "Idle agents take breaks here" },
  { value: "kitchen", label: "Kitchen", hint: "Idle agents take breaks here" },
  { value: "team", label: "Team area", hint: "Agents whose team matches the name sit here" },
  { value: "focus", label: "Focus room", hint: "Decorative" },
  { value: "reception", label: "Reception", hint: "Decorative" },
  { value: "custom", label: "Custom", hint: "Decorative" },
];

export function DesignerToolbar({
  agents,
  onSave,
  onCancel,
  saving,
  error,
}: {
  agents: Agent[];
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  error: string | null;
}) {
  const draft = useDesigner((s) => s.draft)!;
  const tool = useDesigner((s) => s.tool);
  const dirty = useDesigner((s) => s.dirty);
  const canUndo = useDesigner((s) => s.history.length > 0);
  const canRedo = useDesigner((s) => s.future.length > 0);
  const { update, undo, redo, setTool } = useDesigner.getState();
  return (
    <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-xl border border-line-2 bg-panel/95 px-3 py-2 shadow-2xl backdrop-blur">
      <span className="mr-1 text-sm font-semibold">Office designer</span>
      <Button size="sm" variant={tool.kind === "select" ? "primary" : "secondary"} onClick={() => setTool({ kind: "select" })} title="Select & move (Esc)">
        ⬚ Select
      </Button>
      <Button size="sm" variant={tool.kind === "room" ? "primary" : "secondary"} onClick={() => setTool({ kind: "room" })} title="Drag on the floor to draw a room">
        ▭ Draw room
      </Button>
      <span className="mx-1 h-5 w-px bg-line-2" />
      <Button size="sm" variant="ghost" disabled={!canUndo} onClick={undo} title="Undo (Ctrl+Z)">
        ↶
      </Button>
      <Button size="sm" variant="ghost" disabled={!canRedo} onClick={redo} title="Redo (Ctrl+Y)">
        ↷
      </Button>
      <span className="mx-1 h-5 w-px bg-line-2" />
      <Select className="h-7 w-28 text-xs" value={draft.floor} onChange={(e) => update((d) => ({ ...d, floor: e.target.value as FloorStyle }))}>
        <option value="oak">Oak floor</option>
        <option value="walnut">Walnut floor</option>
        <option value="concrete">Concrete</option>
        <option value="carpet">Carpet</option>
        <option value="tile">Tile</option>
        <option value="grass">Grass</option>
        <option value="asphalt">Asphalt</option>
      </Select>
      <label className="flex items-center gap-1 text-xs text-ink-2">
        W
        <Input className="h-7 w-16 text-xs" type="number" min={10} max={120} value={draft.width} onChange={(e) => update((d) => ({ ...d, width: clampNum(e.target.value, 10, 120) }))} />
      </label>
      <label className="flex items-center gap-1 text-xs text-ink-2">
        D
        <Input className="h-7 w-16 text-xs" type="number" min={10} max={120} value={draft.depth} onChange={(e) => update((d) => ({ ...d, depth: clampNum(e.target.value, 10, 120) }))} />
      </label>
      <Button size="sm" variant="ghost" onClick={() => update(() => generateDesign(agents))} title="Replace with a generated office for the current team">
        ⟲ Regenerate
      </Button>
      <div className="ml-auto flex items-center gap-2">
        <ErrorNote error={error} />
        {dirty && <Badge tone="warn">unsaved</Badge>}
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Close
        </Button>
        <Button size="sm" variant="primary" onClick={onSave} loading={saving} disabled={!dirty}>
          Save office
        </Button>
      </div>
    </div>
  );
}

export function CatalogPanel() {
  const tool = useDesigner((s) => s.tool);
  const [cat, setCat] = useState<Category>("Workstations");
  const [q, setQ] = useState("");
  const assetVersion = useAssetVersion();
  const list = useMemo(
    () => Object.values(CATALOG).filter((e) => (q ? e.label.toLowerCase().includes(q.toLowerCase()) : e.category === cat)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cat, q, assetVersion],
  );
  return (
    <div className="pointer-events-auto flex h-full w-64 flex-col rounded-xl border border-line-2 bg-panel/95 shadow-2xl backdrop-blur">
      <div className="border-b border-line p-2.5">
        <Input className="h-8 text-xs" placeholder="Search furniture…" value={q} onChange={(e) => setQ(e.target.value)} />
        {!q && (
          <div className="mt-2 flex flex-wrap gap-1">
            {CATEGORIES.map((c) => (
              <button
                key={c}
                onClick={() => setCat(c)}
                className={cx("rounded px-1.5 py-0.5 text-[11px] cursor-pointer", cat === c ? "bg-accent-2 text-white" : "bg-panel-2 text-ink-2 hover:text-ink")}
              >
                {c}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-2 content-start gap-1.5 overflow-y-auto p-2">
        {list.map((e) => (
          <button
            key={e.key}
            onClick={() => useDesigner.getState().setTool({ kind: "place", key: e.key, rot: 0 })}
            className={cx(
              "flex flex-col items-center rounded-lg border p-1.5 text-center transition-colors cursor-pointer",
              tool.kind === "place" && tool.key === e.key ? "border-accent bg-accent/15" : "border-line hover:border-line-2 hover:bg-panel-2",
            )}
            title={e.label}
          >
            {e.thumb ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={e.thumb} alt="" className="h-14 w-14 object-contain" loading="lazy" />
            ) : (
              <span className="flex h-14 w-14 items-center justify-center rounded bg-black/5 text-2xl">{e.procedural ? "◆" : "▣"}</span>
            )}
            <span className="mt-1 line-clamp-2 text-[10.5px] leading-tight text-ink-2">{e.label}</span>
          </button>
        ))}
      </div>
      <div className="border-t border-line p-2.5 text-[10.5px] leading-relaxed text-ink-3">
        Click an item, then click the floor to place it (hold <b>Shift</b> to place several). <b>R</b> rotates, <b>Del</b> removes,
        <b> Ctrl+D</b> duplicates, arrows nudge. Right-drag pans, scroll zooms.
      </div>
    </div>
  );
}

export function PropertiesPanel({ agents }: { agents: Agent[] }) {
  const draft = useDesigner((s) => s.draft)!;
  const selection = useDesigner((s) => s.selection);
  const tool = useDesigner((s) => s.tool);
  const { update } = useDesigner.getState();
  const item = selection?.type === "item" ? draft.items.find((i) => i.id === selection.id) : undefined;
  const room = selection?.type === "room" ? draft.rooms.find((r) => r.id === selection.id) : undefined;
  const assigned = new Map(draft.items.filter((i) => i.agentId).map((i) => [i.agentId!, i.id]));

  if (tool.kind === "place") {
    const e = CATALOG[tool.key];
    return (
      <Panel title={`Placing: ${e?.label}`}>
        <p className="text-xs text-ink-2">Click the floor to place. Press R to rotate before placing.</p>
        <Button size="sm" className="mt-3" onClick={() => useDesigner.getState().setTool({ ...tool, rot: tool.rot + Math.PI / 2 })}>
          ⟳ Rotate
        </Button>
      </Panel>
    );
  }
  if (item) {
    const e = CATALOG[item.kind];
    return (
      <Panel title={e?.label ?? item.kind}>
        <div className="grid grid-cols-2 gap-2">
          <Field label="X">
            <Input className="h-8" type="number" step={0.25} value={item.x} onChange={(ev) => update((d) => patchItem(d, item.id, { x: Number(ev.target.value) }))} />
          </Field>
          <Field label="Z">
            <Input className="h-8" type="number" step={0.25} value={item.z} onChange={(ev) => update((d) => patchItem(d, item.id, { z: Number(ev.target.value) }))} />
          </Field>
        </div>
        <div className="mt-2 flex gap-1.5">
          <Button size="sm" onClick={() => update((d) => patchItem(d, item.id, { rot: item.rot - Math.PI / 2 }))}>⟲ 90°</Button>
          <Button size="sm" onClick={() => update((d) => patchItem(d, item.id, { rot: item.rot + Math.PI / 2 }))}>⟳ 90°</Button>
          <Button size="sm" onClick={() => duplicate(item.id)}>Duplicate</Button>
        </div>
        {e?.workstation && (
          <Field label="Assigned to">
            <Select
              className="mt-1 h-8"
              value={item.agentId ?? ""}
              onChange={(ev) =>
                update((d) => ({
                  ...d,
                  items: d.items.map((i) =>
                    i.id === item.id ? { ...i, agentId: ev.target.value || null } : i.agentId === ev.target.value ? { ...i, agentId: null } : i,
                  ),
                }))
              }
            >
              <option value="">Automatic (first free agent)</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {assigned.has(a.id) && assigned.get(a.id) !== item.id ? " (moves here)" : ""}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Button size="sm" variant="danger" className="mt-3" onClick={() => remove()}>
          Remove
        </Button>
      </Panel>
    );
  }
  if (room) {
    return (
      <Panel title="Room">
        <div className="grid gap-2.5">
          <Field label="Name">
            <Input className="h-8" value={room.name} onChange={(e) => update((d) => patchRoom(d, room.id, { name: e.target.value }), false)} onBlur={() => useDesigner.getState().checkpoint()} />
          </Field>
          <Field label="Purpose" hint={ROOM_TYPES.find((t) => t.value === room.type)?.hint}>
            <Select className="h-8" value={room.type} onChange={(e) => update((d) => patchRoom(d, room.id, { type: e.target.value as RoomType }))}>
              {ROOM_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Walls">
              <Select className="h-8" value={room.walls} onChange={(e) => update((d) => patchRoom(d, room.id, { walls: e.target.value as WallKind }))}>
                <option value="none">None (zone)</option>
                <option value="glass">Glass</option>
                <option value="solid">Solid</option>
              </Select>
            </Field>
            <Field label="Door">
              <Select className="h-8" value={room.door} onChange={(e) => update((d) => patchRoom(d, room.id, { door: e.target.value as Side }))}>
                <option value="n">North</option>
                <option value="s">South</option>
                <option value="e">East</option>
                <option value="w">West</option>
              </Select>
            </Field>
            {(["x", "z", "w", "d"] as const).map((k) => (
              <Field key={k} label={{ x: "X", z: "Z", w: "Width", d: "Depth" }[k]}>
                <Input className="h-8" type="number" step={0.25} value={room[k]} onChange={(e) => update((d) => patchRoom(d, room.id, { [k]: Math.max(k === "w" || k === "d" ? 2 : 0, Number(e.target.value)) }))} />
              </Field>
            ))}
          </div>
          <Button size="sm" variant="danger" onClick={() => remove()}>
            Remove room
          </Button>
        </div>
      </Panel>
    );
  }
  const stations = draft.items.filter((i) => CATALOG[i.kind]?.workstation).length;
  return (
    <Panel title="Office">
      <div className="space-y-1.5 text-xs text-ink-2">
        <div>{draft.items.length} items · {draft.rooms.length} rooms</div>
        <div className={stations < agents.length ? "text-warn" : ""}>
          {stations} workstations for {agents.length} agents
          {stations < agents.length && " — extra desks are added automatically until you place more"}
        </div>
        <p className="pt-2 text-ink-3">Select something to edit it, pick furniture from the catalog, or draw a room.</p>
      </div>
    </Panel>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="pointer-events-auto w-72 rounded-xl border border-line-2 bg-panel/95 p-3.5 shadow-2xl backdrop-blur">
      <div className="mb-2.5 text-sm font-semibold">{title}</div>
      {children}
    </div>
  );
}

function clampNum(v: string, lo: number, hi: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : lo;
}

function duplicate(id: string) {
  const st = useDesigner.getState();
  const it = st.draft?.items.find((i) => i.id === id);
  if (!it) return;
  const copy = { ...it, id: newId("it"), x: it.x + 0.5, z: it.z + 0.5, agentId: null };
  st.update((d) => ({ ...d, items: [...d.items, copy] }));
  st.select({ type: "item", id: copy.id });
}

function remove() {
  const st = useDesigner.getState();
  const sel = st.selection;
  if (!sel) return;
  st.update((d) =>
    sel.type === "item" ? { ...d, items: d.items.filter((i) => i.id !== sel.id) } : { ...d, rooms: d.rooms.filter((r) => r.id !== sel.id) },
  );
  st.select(null);
}

/** Keyboard shortcuts while the designer is open. */
export function useDesignerKeys(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const st = useDesigner.getState();
      const sel = st.selection;
      const ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) st.redo();
        else st.undo();
      } else if (ctrl && e.key.toLowerCase() === "y") {
        e.preventDefault();
        st.redo();
      } else if (ctrl && e.key.toLowerCase() === "d" && sel?.type === "item") {
        e.preventDefault();
        duplicate(sel.id);
      } else if (e.key === "Escape") {
        st.setTool({ kind: "select" });
        st.select(null);
      } else if (e.key === "Delete" || e.key === "Backspace") {
        remove();
      } else if (e.key.toLowerCase() === "r") {
        const step = e.shiftKey ? Math.PI / 12 : Math.PI / 2;
        if (st.tool.kind === "place") st.setTool({ ...st.tool, rot: st.tool.rot + step });
        else if (sel?.type === "item") {
          const it = st.draft?.items.find((i) => i.id === sel.id);
          if (it) st.update((d) => patchItem(d, sel.id, { rot: it.rot + step }));
        }
      } else if (e.key.startsWith("Arrow") && sel) {
        e.preventDefault();
        const dx = e.key === "ArrowLeft" ? -0.25 : e.key === "ArrowRight" ? 0.25 : 0;
        const dz = e.key === "ArrowUp" ? -0.25 : e.key === "ArrowDown" ? 0.25 : 0;
        st.update((d) => {
          if (sel.type === "item") {
            const it = d.items.find((i) => i.id === sel.id);
            return it ? patchItem(d, sel.id, { x: it.x + dx, z: it.z + dz }) : d;
          }
          const r = d.rooms.find((x) => x.id === sel.id);
          return r ? patchRoom(d, sel.id, { x: r.x + dx, z: r.z + dz }) : d;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
}

export type { OfficeDesign };
