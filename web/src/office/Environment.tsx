"use client";

/** Floor, exterior walls with windows, room zones, walls and signage, lighting. */

import { memo, useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import type { Task, WhiteboardInfo } from "@/lib/types";
import { CATALOG } from "./catalog";
import { ROOM_COLORS, roomWalls, type FloorStyle, type OfficeDesign, type PlacedItem, type Room } from "./design";
import { FurnitureLayer } from "./Furniture";

const FLOOR_COLORS: Record<FloorStyle, [string, string]> = {
  oak: ["#c8a57a", "#b8925f"],
  walnut: ["#8a6546", "#735236"],
  concrete: ["#c9c7c2", "#bdbab4"],
  carpet: ["#9aa3ad", "#8e98a2"],
  tile: ["#e3e1dc", "#d4d1ca"],
  grass: ["#7fa35a", "#729650"],
  asphalt: ["#5b5f66", "#53575e"],
};

function plankTexture(style: FloorStyle, w: number, d: number): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 512;
  const g = c.getContext("2d")!;
  const [a, b] = FLOOR_COLORS[style];
  g.fillStyle = a;
  g.fillRect(0, 0, 512, 512);
  if (style === "oak" || style === "walnut") {
    // 8 plank rows, staggered joints, subtle grain.
    for (let row = 0; row < 8; row++) {
      const y = row * 64;
      const offset = (row * 173) % 512;
      for (let x = -offset; x < 512; x += 256) {
        const shade = ((row * 7 + Math.floor((x + offset) / 256) * 13) % 5) / 5;
        g.fillStyle = new THREE.Color(a).lerp(new THREE.Color(b), shade * 0.8).getStyle();
        g.fillRect(x + 1, y + 1, 254, 62);
        g.fillStyle = "rgba(60,40,20,0.08)";
        for (let k = 0; k < 6; k++) g.fillRect(x + 8, y + 8 + k * 9, 240, 1);
      }
      g.fillStyle = "rgba(60,40,20,0.35)";
      g.fillRect(0, y, 512, 1.5);
    }
  } else if (style === "concrete") {
    for (let i = 0; i < 2500; i++) {
      g.fillStyle = `rgba(0,0,0,${Math.random() * 0.05})`;
      g.fillRect(Math.random() * 512, Math.random() * 512, 2, 2);
    }
    g.strokeStyle = "rgba(0,0,0,0.12)";
    g.strokeRect(0, 0, 512, 512);
  } else {
    for (let i = 0; i < 4000; i++) {
      g.fillStyle = `rgba(255,255,255,${Math.random() * 0.05})`;
      g.fillRect(Math.random() * 512, Math.random() * 512, 1, 1);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(w / 4, d / 4);
  t.anisotropy = 8;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function signTexture(text: string): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 256;
  const g = c.getContext("2d")!;
  g.fillStyle = "#1f2733";
  g.beginPath();
  g.roundRect(8, 8, 1008, 240, 36);
  g.fill();
  g.strokeStyle = "#c9a45c";
  g.lineWidth = 10;
  g.stroke();
  g.fillStyle = "#f3eee4";
  g.font = "700 110px system-ui, -apple-system, Segoe UI, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  const label = text.toUpperCase();
  let size = 110;
  while (g.measureText(label).width > 920 && size > 40) {
    size -= 6;
    g.font = `700 ${size}px system-ui, -apple-system, Segoe UI, sans-serif`;
  }
  g.fillText(label, 512, 136);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

export function Lighting({ width, depth }: { width: number; depth: number }) {
  const cx = width / 2;
  const cz = depth / 2;
  const span = Math.max(width, depth);
  return (
    <>
      <color attach="background" args={["#dfe5ec"]} />
      <hemisphereLight args={["#ffffff", "#b9a88f", 1.15]} />
      <ambientLight intensity={0.25} />
      <directionalLight
        position={[cx - span * 0.35, span * 0.9, cz + span * 0.55]}
        color="#fff3df"
        intensity={2.1}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-span * 0.8}
        shadow-camera-right={span * 0.8}
        shadow-camera-top={span * 0.8}
        shadow-camera-bottom={-span * 0.8}
        shadow-camera-far={span * 3}
        shadow-bias={-0.0004}
        shadow-normalBias={0.02}
      >
        <object3D attach="target" position={[cx, 0, cz]} />
      </directionalLight>
    </>
  );
}

/** Window walls along the north and west edges (generated, not part of the design). */
export function exteriorItems(width: number, depth: number): PlacedItem[] {
  const out: PlacedItem[] = [];
  const seg = 2; // wallWindow is 1 Kenney unit = 2 m wide
  for (let x = 0; x + seg <= width + 0.01; x += seg)
    out.push({ id: `ext_n_${x}`, kind: x % 6 === 4 ? "wall" : "wallWindow", x: x + seg / 2, z: -0.04, rot: 0 });
  for (let z = 0; z + seg <= depth + 0.01; z += seg)
    out.push({ id: `ext_w_${z}`, kind: z % 6 === 4 ? "wall" : "wallWindow", x: -0.04, z: z + seg / 2, rot: Math.PI / 2 });
  return out;
}

export const OfficeShell = memo(function OfficeShell({
  design,
  onFloorClick,
}: {
  design: OfficeDesign;
  onFloorClick?: (x: number, z: number) => void;
}) {
  const { width, depth } = design;
  const tex = useMemo(() => plankTexture(design.floor, width, depth), [design.floor, width, depth]);
  const ext = useMemo(() => exteriorItems(width, depth), [width, depth]);
  return (
    <group>
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[width / 2, 0, depth / 2]}
        receiveShadow
        onClick={onFloorClick && ((e) => onFloorClick(e.point.x, e.point.z))}
      >
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial map={tex} roughness={0.85} />
      </mesh>
      {/* Low skirting on the open (camera-facing) sides. */}
      <mesh position={[width / 2, 0.08, depth]} receiveShadow castShadow>
        <boxGeometry args={[width + 0.1, 0.16, 0.1]} />
        <meshStandardMaterial color="#e9e4dc" />
      </mesh>
      <mesh position={[width, 0.08, depth / 2]} receiveShadow castShadow>
        <boxGeometry args={[0.1, 0.16, depth + 0.1]} />
        <meshStandardMaterial color="#e9e4dc" />
      </mesh>
      <FurnitureLayer items={ext} shadows={false} />
      {design.rooms.map((r) => (
        <RoomView key={r.id} room={r} />
      ))}
    </group>
  );
});

function RoomView({ room }: { room: Room }) {
  const walls = useMemo(() => (room.walls === "none" ? [] : roomWalls(room)), [room]);
  const sign = useMemo(() => (room.type !== "team" ? signTexture(room.name) : null), [room.name, room.type]);
  const color = ROOM_COLORS[room.type];
  const signPos = useMemo((): { pos: [number, number, number]; rot: number } => {
    const cx = room.x + room.w / 2;
    const cz = room.z + room.d / 2;
    switch (room.door) {
      case "n":
        return { pos: [cx, 2.3, room.z - 0.07], rot: Math.PI };
      case "s":
        return { pos: [cx, 2.3, room.z + room.d + 0.07], rot: 0 };
      case "e":
        return { pos: [room.x + room.w + 0.07, 2.3, cz], rot: Math.PI / 2 };
      default:
        return { pos: [room.x - 0.07, 2.3, cz], rot: -Math.PI / 2 };
    }
  }, [room]);
  return (
    <group>
      {room.type !== "team" && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[room.x + room.w / 2, 0.006, room.z + room.d / 2]} receiveShadow>
          <planeGeometry args={[room.w, room.d]} />
          <meshStandardMaterial color={color} roughness={1} transparent opacity={room.type === "kitchen" ? 0.55 : 0.75} />
        </mesh>
      )}
      {room.type === "team" && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[room.x + room.w / 2, 0.005, room.z + room.d / 2]}>
          <planeGeometry args={[room.w, room.d]} />
          <meshStandardMaterial color={color} transparent opacity={0.28} roughness={1} />
        </mesh>
      )}
      {walls.map((w, i) => (
        <group key={i}>
          <mesh position={[w.x + w.w / 2, w.kind === "glass" ? 1.2 : 1.25, w.z + w.d / 2]} castShadow={w.kind === "solid"} receiveShadow>
            <boxGeometry args={[w.w, w.kind === "glass" ? 2.4 : 2.5, w.d]} />
            {w.kind === "glass" ? (
              <meshStandardMaterial color="#bcd7ee" transparent opacity={0.22} roughness={0.05} metalness={0.1} depthWrite={false} />
            ) : (
              <meshStandardMaterial color="#eef0f2" roughness={0.9} />
            )}
          </mesh>
          {w.kind === "glass" && (
            <mesh position={[w.x + w.w / 2, 2.42, w.z + w.d / 2]}>
              <boxGeometry args={[w.w + 0.02, 0.05, w.d + 0.02]} />
              <meshStandardMaterial color="#c3cad4" metalness={0.5} roughness={0.35} />
            </mesh>
          )}
        </group>
      ))}
      {sign && (
        <mesh position={signPos.pos} rotation={[0, signPos.rot, 0]}>
          <planeGeometry args={[1.8, 0.45]} />
          <meshStandardMaterial map={sign} roughness={0.6} />
        </mesh>
      )}
    </group>
  );
}

const COLS: { key: Task["status"]; color: string; label: string }[] = [
  { key: "todo", color: "#9aa3b2", label: "To do" },
  { key: "in_progress", color: "#8b7cff", label: "Doing" },
  { key: "blocked", color: "#f2667a", label: "Blocked" },
  { key: "review", color: "#f5b454", label: "Review" },
  { key: "done", color: "#3ecf8e", label: "Done" },
];

function boardTexture(tasks: Task[]): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 512;
  const g = c.getContext("2d")!;
  g.fillStyle = "#fbfbf8";
  g.fillRect(0, 0, 1024, 512);
  g.strokeStyle = "#b8bcc4";
  g.lineWidth = 12;
  g.strokeRect(6, 6, 1012, 500);
  const colW = 1000 / COLS.length;
  COLS.forEach((col, ci) => {
    const x0 = 12 + ci * colW;
    const items = tasks.filter((t) => t.status === col.key);
    g.fillStyle = "#2b303a";
    g.font = "700 30px system-ui, sans-serif";
    g.fillText(`${col.label} · ${items.length}`, x0 + 14, 52);
    if (ci > 0) {
      g.fillStyle = "#e1e3e8";
      g.fillRect(x0, 70, 3, 420);
    }
    items.slice(0, 12).forEach((t, i) => {
      const x = x0 + 14 + (i % 2) * ((colW - 34) / 2 + 6);
      const y = 76 + Math.floor(i / 2) * 70;
      g.fillStyle = col.color;
      g.fillRect(x, y, (colW - 34) / 2, 60);
      g.fillStyle = "rgba(0,0,0,0.75)";
      g.font = "600 17px system-ui, sans-serif";
      g.fillText(t.ref, x + 8, y + 22);
      g.font = "15px system-ui, sans-serif";
      g.fillText(t.title.slice(0, 13), x + 8, y + 44);
    });
  });
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

export function TaskBoards({ items, tasks, onClick }: { items: PlacedItem[]; tasks: Task[]; onClick?: () => void }) {
  const boards = items.filter((i) => CATALOG[i.kind]?.special === "taskboard");
  const sig = tasks.map((t) => `${t.id}:${t.status}`).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const tex = useMemo(() => boardTexture(tasks), [sig]);
  return (
    <>
      {boards.map((b) => (
        <group
          key={b.id}
          position={[b.x, 0, b.z]}
          rotation={[0, b.rot, 0]}
          onClick={
            onClick &&
            ((e) => {
              e.stopPropagation();
              onClick();
            })
          }
          onPointerOver={onClick && (() => (document.body.style.cursor = "pointer"))}
          onPointerOut={onClick && (() => (document.body.style.cursor = ""))}
        >
          <mesh position={[0, 1.55, 0]} castShadow>
            <boxGeometry args={[4.1, 2.1, 0.08]} />
            <meshStandardMaterial color="#9aa0aa" metalness={0.3} roughness={0.5} />
          </mesh>
          <mesh position={[0, 1.55, 0.045]}>
            <planeGeometry args={[4, 2]} />
            <meshStandardMaterial map={tex} roughness={0.7} />
          </mesh>
          {[-1.6, 1.6].map((x) => (
            <mesh key={x} position={[x, 0.25, 0]}>
              <boxGeometry args={[0.06, 0.5, 0.06]} />
              <meshStandardMaterial color="#6b717c" />
            </mesh>
          ))}
        </group>
      ))}
    </>
  );
}

/** Whiteboards on stands, showing the live board (its latest thumbnail). */
export function Whiteboards({
  items,
  boards,
  onClick,
}: {
  items: PlacedItem[];
  boards: WhiteboardInfo[];
  onClick?: (boardId: string) => void;
}) {
  const placed = items.filter((i) => CATALOG[i.kind]?.special === "whiteboard");
  if (!placed.length || !boards.length) return null;
  return (
    <>
      {placed.map((it) => {
        const board = boards.find((b) => b.id === it.boardId) ?? boards[0];
        return <WhiteboardStand key={it.id} item={it} board={board} onClick={onClick} />;
      })}
    </>
  );
}

const blankBoard = (() => {
  let tex: THREE.CanvasTexture | null = null;
  return (title: string) => {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 320;
    const g = c.getContext("2d")!;
    g.fillStyle = "#ffffff";
    g.fillRect(0, 0, 512, 320);
    g.fillStyle = "#9aa0aa";
    g.font = "600 30px ui-sans-serif, system-ui";
    g.textAlign = "center";
    g.fillText(title, 256, 150);
    g.font = "22px ui-sans-serif, system-ui";
    g.fillText("click to draw", 256, 190);
    tex?.dispose();
    tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  };
})();

function WhiteboardStand({ item, board, onClick }: { item: PlacedItem; board: WhiteboardInfo; onClick?: (boardId: string) => void }) {
  const [tex, setTex] = useState<THREE.Texture>(() => blankBoard(board.title));
  useEffect(() => {
    if (!board.hasThumbnail) {
      setTex(blankBoard(board.title));
      return;
    }
    let alive = true;
    new THREE.TextureLoader().load(`/api/v1/whiteboards/${board.id}/thumbnail.png?v=${board.version}`, (t) => {
      if (!alive) return t.dispose();
      t.colorSpace = THREE.SRGBColorSpace;
      setTex((old) => (old.dispose(), t));
    });
    return () => {
      alive = false;
    };
  }, [board.id, board.version, board.hasThumbnail, board.title]);
  // Fit the drawing inside the 2.4 x 1.4 m panel without stretching it.
  const img = tex.image as { width?: number; height?: number } | undefined;
  const aspect = img?.width && img?.height ? img.width / img.height : 1.6;
  const [w, h] = aspect > 2.4 / 1.4 ? [2.4, 2.4 / aspect] : [1.4 * aspect, 1.4];
  return (
    <group
      position={[item.x, 0, item.z]}
      rotation={[0, item.rot, 0]}
      onClick={
        onClick &&
        ((e) => {
          e.stopPropagation();
          onClick(board.id);
        })
      }
      onPointerOver={onClick && (() => (document.body.style.cursor = "pointer"))}
      onPointerOut={onClick && (() => (document.body.style.cursor = ""))}
    >
      <mesh position={[0, 1.45, 0]} castShadow>
        <boxGeometry args={[2.56, 1.56, 0.06]} />
        <meshStandardMaterial color="#c9ced6" metalness={0.4} roughness={0.4} />
      </mesh>
      <mesh position={[0, 1.45, 0.032]}>
        <planeGeometry args={[2.46, 1.46]} />
        <meshStandardMaterial color="#ffffff" roughness={0.35} />
      </mesh>
      <mesh position={[0, 1.45, 0.034]}>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={tex} toneMapped={false} />
      </mesh>
      <mesh position={[0, 0.62, 0.08]}>
        <boxGeometry args={[2.2, 0.04, 0.12]} />
        <meshStandardMaterial color="#aeb4bd" />
      </mesh>
      {[-1.1, 1.1].map((x) => (
        <mesh key={x} position={[x, 0.36, 0]}>
          <boxGeometry args={[0.05, 0.72, 0.05]} />
          <meshStandardMaterial color="#6b717c" />
        </mesh>
      ))}
    </group>
  );
}
