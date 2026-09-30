"use client";

/**
 * In-canvas editing for the office designer: select and drag items and rooms,
 * a live placement preview, drag-to-draw rooms, selection outlines and a grid.
 */

import type { ThreeEvent } from "@react-three/fiber";
import { useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { CATALOG, itemBounds } from "./catalog";
import { newId, overlap, type OfficeDesign, type Rect } from "./design";
import { patchItem, patchRoom, snap, useDesigner } from "./designerStore";
import { FurnitureLayer } from "./Furniture";

type Drag =
  | { kind: "item"; id: string; dx: number; dz: number }
  | { kind: "room"; id: string; dx: number; dz: number }
  | { kind: "resize"; id: string; corner: "nw" | "ne" | "sw" | "se" }
  | { kind: "draw"; x0: number; z0: number; x1: number; z1: number };

export function DesignerLayer({ design, onDragging }: { design: OfficeDesign; onDragging: (v: boolean) => void }) {
  const tool = useDesigner((s) => s.tool);
  const selection = useDesigner((s) => s.selection);
  const [ghost, setGhost] = useState<[number, number] | null>(null);
  const drag = useRef<Drag | null>(null);
  const [drawRect, setDrawRect] = useState<Rect | null>(null);

  const setDrag = (d: Drag | null) => {
    drag.current = d;
    onDragging(!!d);
  };

  const onMove = (e: ThreeEvent<PointerEvent>) => {
    const x = e.point.x;
    const z = e.point.z;
    const d = drag.current;
    const { update } = useDesigner.getState();
    if (d?.kind === "item") {
      update((dz) => patchItem(dz, d.id, { x: snap(x - d.dx, 0.05), z: snap(z - d.dz, 0.05) }), false);
    } else if (d?.kind === "room") {
      update((dz) => patchRoom(dz, d.id, { x: snap(x - d.dx), z: snap(z - d.dz) }), false);
    } else if (d?.kind === "resize") {
      update((dz) => {
        const r = dz.rooms.find((rr) => rr.id === d.id);
        if (!r) return dz;
        let { x: rx, z: rz, w, d: rd } = r;
        const sx = snap(x);
        const sz = snap(z);
        if (d.corner.includes("w")) {
          w = Math.max(2, rx + w - sx);
          rx = rx + r.w - w;
        } else w = Math.max(2, sx - rx);
        if (d.corner.includes("n")) {
          rd = Math.max(2, rz + rd - sz);
          rz = rz + r.d - rd;
        } else rd = Math.max(2, sz - rz);
        return patchRoom(dz, d.id, { x: rx, z: rz, w, d: rd });
      }, false);
    } else if (d?.kind === "draw") {
      d.x1 = snap(x);
      d.z1 = snap(z);
      setDrawRect(norm(d));
    }
    if (tool.kind === "place") setGhost([snap(x), snap(z)]);
  };

  const onDown = (e: ThreeEvent<PointerEvent>) => {
    if (e.button !== 0) return;
    const x = e.point.x;
    const z = e.point.z;
    const st = useDesigner.getState();
    if (tool.kind === "room") {
      e.stopPropagation();
      setDrag({ kind: "draw", x0: snap(x), z0: snap(z), x1: snap(x), z1: snap(z) });
      return;
    }
    if (tool.kind === "select") {
      const room = [...design.rooms].reverse().find((r) => x >= r.x && x <= r.x + r.w && z >= r.z && z <= r.z + r.d);
      if (room) {
        st.select({ type: "room", id: room.id });
        st.checkpoint();
        setDrag({ kind: "room", id: room.id, dx: x - room.x, dz: z - room.z });
      } else st.select(null);
    }
  };

  const onUp = () => {
    const d = drag.current;
    if (d?.kind === "draw") {
      const r = norm(d);
      if (r.w >= 2 && r.d >= 2) {
        const id = newId("rm");
        useDesigner.getState().update((dz) => ({
          ...dz,
          rooms: [...dz.rooms, { id, name: "New room", type: "meeting", ...r, walls: "glass", door: "w" }],
        }));
        useDesigner.getState().setTool({ kind: "select" });
        useDesigner.getState().select({ type: "room", id });
      }
      setDrawRect(null);
    }
    setDrag(null);
  };

  const onClickFloor = (e: ThreeEvent<MouseEvent>) => {
    if (tool.kind !== "place") return;
    e.stopPropagation();
    const e2 = CATALOG[tool.key];
    if (!e2) return;
    const id = newId("it");
    useDesigner.getState().update((dz) => ({
      ...dz,
      items: [...dz.items, { id, kind: tool.key, x: snap(e.point.x), z: snap(e.point.z), rot: tool.rot }],
    }));
    if (!e.nativeEvent.shiftKey) {
      useDesigner.getState().setTool({ kind: "select" });
      useDesigner.getState().select({ type: "item", id });
    }
  };

  const onPickItem = (id: string, e: ThreeEvent<MouseEvent | PointerEvent>) => {
    if (tool.kind !== "select") return;
    const it = design.items.find((i) => i.id === id);
    if (!it) return;
    const st = useDesigner.getState();
    st.select({ type: "item", id });
    st.checkpoint();
    setDrag({ kind: "item", id, dx: e.point.x - it.x, dz: e.point.z - it.z });
  };

  const ghostItem = tool.kind === "place" && ghost ? { id: "__ghost", kind: tool.key, x: ghost[0], z: ghost[1], rot: tool.rot } : null;
  const ghostItems = useMemo(() => (ghostItem ? [ghostItem] : []), [ghostItem?.kind, ghostItem?.x, ghostItem?.z, ghostItem?.rot]); // eslint-disable-line react-hooks/exhaustive-deps
  const collides =
    ghostItem &&
    design.items.some((i) => CATALOG[i.kind]?.blocking !== false && overlap(itemBounds(i), shrink(itemBounds(ghostItem), 0.05)));

  const selItem = selection?.type === "item" ? design.items.find((i) => i.id === selection.id) : undefined;
  const selRoom = selection?.type === "room" ? design.rooms.find((r) => r.id === selection.id) : undefined;

  return (
    <group>
      <gridHelper args={[Math.max(design.width, design.depth) * 2, Math.max(design.width, design.depth) * 4, "#9aa4b1", "#c9ced6"]} position={[design.width / 2, 0.012, design.depth / 2]} />
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[design.width / 2, 0.015, design.depth / 2]}
        onPointerMove={onMove}
        onPointerDown={onDown}
        onPointerUp={onUp}
        onPointerLeave={() => setGhost(null)}
        onClick={onClickFloor}
      >
        <planeGeometry args={[design.width + 40, design.depth + 40]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <FurnitureLayer items={design.items} onPick={tool.kind === "select" ? onPickItem : undefined} pickOn="pointerdown" />
      {ghostItem && (
        <group>
          <FurnitureLayer items={ghostItems} shadows={false} />
          <Outline rect={itemBounds(ghostItem)} color={collides ? "#e5484d" : "#30a46c"} />
        </group>
      )}
      {selItem && <Outline rect={itemBounds(selItem)} color="#6d5dfc" />}
      {selRoom && (
        <group>
          <Outline rect={selRoom} color="#6d5dfc" y={0.03} />
          {(["nw", "ne", "sw", "se"] as const).map((c) => (
            <mesh
              key={c}
              position={[c.includes("w") ? selRoom.x : selRoom.x + selRoom.w, 0.05, c.includes("n") ? selRoom.z : selRoom.z + selRoom.d]}
              onPointerDown={(e) => {
                e.stopPropagation();
                useDesigner.getState().checkpoint();
                setDrag({ kind: "resize", id: selRoom.id, corner: c });
              }}
              onPointerMove={onMove}
              onPointerUp={onUp}
            >
              <boxGeometry args={[0.35, 0.1, 0.35]} />
              <meshBasicMaterial color="#6d5dfc" />
            </mesh>
          ))}
        </group>
      )}
      {drawRect && <Outline rect={drawRect} color="#6d5dfc" y={0.03} dashed />}
    </group>
  );
}

function Outline({ rect, color, y = 0.02, dashed }: { rect: Rect; color: string; y?: number; dashed?: boolean }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(rect.x, y, rect.z),
      new THREE.Vector3(rect.x + rect.w, y, rect.z),
      new THREE.Vector3(rect.x + rect.w, y, rect.z + rect.d),
      new THREE.Vector3(rect.x, y, rect.z + rect.d),
      new THREE.Vector3(rect.x, y, rect.z),
    ]);
    return g;
  }, [rect.x, rect.z, rect.w, rect.d, y]);
  return (
    <>
      <line>
        <primitive object={geom} attach="geometry" />
        {dashed ? <lineDashedMaterial color={color} dashSize={0.3} gapSize={0.2} /> : <lineBasicMaterial color={color} linewidth={2} />}
      </line>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[rect.x + rect.w / 2, y - 0.005, rect.z + rect.d / 2]}>
        <planeGeometry args={[rect.w, rect.d]} />
        <meshBasicMaterial color={color} transparent opacity={0.12} depthWrite={false} />
      </mesh>
    </>
  );
}

function norm(d: { x0: number; z0: number; x1: number; z1: number }): Rect {
  return { x: Math.min(d.x0, d.x1), z: Math.min(d.z0, d.z1), w: Math.abs(d.x1 - d.x0), d: Math.abs(d.z1 - d.z0) };
}

function shrink(r: Rect, m: number): Rect {
  return { x: r.x + m, z: r.z + m, w: Math.max(0.01, r.w - 2 * m), d: Math.max(0.01, r.d - 2 * m) };
}
