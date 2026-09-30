"use client";

/** Procedural assets: small models built from colored primitives. */

import type { ThreeEvent } from "@react-three/fiber";
import { memo } from "react";
import type { PrimitivePart } from "@/lib/types";
import { CATALOG } from "./catalog";
import type { PlacedItem } from "./design";

/** Every primitive is a unit shape scaled to its [w, h, d] size. */
export const ProceduralModel = memo(function ProceduralModel({ parts, shadows = true }: { parts: PrimitivePart[]; shadows?: boolean }) {
  return (
    <>
      {parts.map((p, i) => (
        <mesh key={i} position={p.pos} rotation={p.rot} scale={p.size} castShadow={shadows} receiveShadow>
          {p.shape === "box" ? (
            <boxGeometry args={[1, 1, 1]} />
          ) : p.shape === "sphere" ? (
            <sphereGeometry args={[0.5, 20, 14]} />
          ) : p.shape === "cone" ? (
            <coneGeometry args={[0.5, 1, 20]} />
          ) : (
            <cylinderGeometry args={[0.5, 0.5, 1, 20]} />
          )}
          <meshStandardMaterial color={p.color} roughness={0.7} />
        </mesh>
      ))}
    </>
  );
});

/** Placed items whose catalog entry is procedural. */
export function ProceduralItems({
  items,
  onPick,
  pickOn = "click",
  shadows = true,
}: {
  items: PlacedItem[];
  onPick?: (itemId: string, e: ThreeEvent<MouseEvent | PointerEvent>) => void;
  pickOn?: "click" | "pointerdown";
  shadows?: boolean;
}) {
  return (
    <>
      {items.map((it) => {
        const parts = CATALOG[it.kind]?.procedural;
        if (!parts) return null;
        const handler =
          onPick &&
          ((e: ThreeEvent<MouseEvent | PointerEvent>) => {
            e.stopPropagation();
            onPick(it.id, e);
          });
        return (
          <group key={it.id} position={[it.x, it.y ?? 0, it.z]} rotation={[0, it.rot, 0]} {...{ [pickOn === "click" ? "onClick" : "onPointerDown"]: handler }}>
            <ProceduralModel parts={parts} shadows={shadows} />
          </group>
        );
      })}
    </>
  );
}
