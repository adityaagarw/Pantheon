"use client";

/**
 * Movable objects in the world: in someone's hand (following them), or lying
 * in a room (on the item they were left at, or at a stable spot on the floor).
 */

import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { memo, Suspense, useMemo, useRef } from "react";
import * as THREE from "three";
import type { WorldObject } from "@/lib/types";
import type { Motion } from "./brain";
import { CATALOG } from "./catalog";
import type { ResolvedOffice } from "./design";
import { footprint, useModelParts } from "./models";
import { ProceduralModel } from "./Procedural";

const HAND: [number, number, number] = [0.22, 0.95, 0.2]; // right hand, in the holder's frame
const MAX_HELD = 0.45; // held things are shown at most this big (meters)

const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 11);

function restingSpot(o: WorldObject, office: ResolvedOffice): [number, number, number] {
  const place = o.place ?? {};
  if (place.itemId) {
    const it = office.design.items.find((i) => i.id === place.itemId);
    if (it) {
      const e = CATALOG[it.kind];
      const h = e?.parts[0] ? footprint(e.parts[0].model).h : 0.75;
      return [it.x, (it.y ?? 0) + Math.min(h, 1.1), it.z];
    }
  }
  const room = office.design.rooms.find((r) => r.name.toLowerCase() === String(place.room ?? "").toLowerCase());
  const h = hash(o.id);
  if (room) {
    const fx = 0.2 + ((h % 1000) / 1000) * 0.6;
    const fz = 0.2 + (((h >> 10) % 1000) / 1000) * 0.6;
    return [room.x + room.w * fx, 0, room.z + room.d * fz];
  }
  return [office.width / 2 + ((h % 7) - 3) * 0.4, 0, office.depth / 2];
}

export function WorldObjects({
  objects,
  office,
  motions,
}: {
  objects: Record<string, WorldObject>;
  office: ResolvedOffice;
  motions: Map<string, Motion>;
}) {
  return (
    <>
      {Object.values(objects).map((o) => (
        <WorldThing key={o.id} obj={o} office={office} motion={o.holderId ? motions.get(o.holderId) : undefined} />
      ))}
    </>
  );
}

const WorldThing = memo(function WorldThing({ obj, office, motion }: { obj: WorldObject; office: ResolvedOffice; motion?: Motion }) {
  const group = useRef<THREE.Group>(null);
  const entry = obj.asset ? CATALOG[obj.asset] : undefined;
  const size = entry?.asset?.size ?? (entry ? [entry.size[0], entry.size[1], 0.5] : [0.18, 0.18, 0.18]);
  const held = !!motion;
  const shrink = held ? Math.min(1, MAX_HELD / Math.max(size[0], size[1], size[2], 0.01)) : 1;
  const rest = useMemo(() => restingSpot(obj, office), [obj, office]);
  useFrame(() => {
    const g = group.current;
    if (!g) return;
    if (motion) {
      const c = Math.cos(motion.heading);
      const s = Math.sin(motion.heading);
      g.position.set(motion.pos[0] + HAND[0] * c + HAND[2] * s, HAND[1] - (motion.pose === "sit" && !motion.moving ? 0.25 : 0), motion.pos[1] - HAND[0] * s + HAND[2] * c);
      g.rotation.y = motion.heading;
    } else {
      g.position.set(rest[0], rest[1], rest[2]);
    }
  });
  return (
    <group ref={group}>
      <group scale={shrink}>
        {entry?.procedural ? (
          <ProceduralModel parts={entry.procedural} />
        ) : entry?.parts[0] ? (
          <Suspense fallback={null}>
            <GlbThing model={entry.parts[0].model} />
          </Suspense>
        ) : (
          <mesh position={[0, 0.09, 0]} castShadow>
            <boxGeometry args={[0.18, 0.18, 0.18]} />
            <meshStandardMaterial color="#d9a441" roughness={0.6} />
          </mesh>
        )}
      </group>
      {!held && (
        <Html position={[0, Math.min(size[2] ?? 0.3, 1.2) + 0.25, 0]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
          <div className="select-none whitespace-nowrap rounded bg-black/55 px-1.5 py-0.5 text-[10px] text-white">{obj.name}</div>
        </Html>
      )}
    </group>
  );
});

function GlbThing({ model }: { model: string }) {
  const parts = useModelParts(model);
  return (
    <>
      {parts.map((p, i) => (
        <mesh key={i} geometry={p.geometry} material={p.material} matrix={p.matrix} matrixAutoUpdate={false} castShadow />
      ))}
    </>
  );
}
