"use client";

/**
 * Furniture renderer. Every Kenney model is drawn with one InstancedMesh per
 * sub-mesh for all of its placements, so a 50-desk office costs about the
 * same number of draw calls as a 5-desk one.
 */

import type { ThreeEvent } from "@react-three/fiber";
import { memo, Suspense, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { CATALOG } from "./catalog";
import type { PlacedItem } from "./design";
import { useModelParts } from "./models";
import { ProceduralItems } from "./Procedural";

interface Placement {
  matrix: THREE.Matrix4;
  itemId: string;
}

export function placementsFor(items: PlacedItem[]): Map<string, Placement[]> {
  const byModel = new Map<string, Placement[]>();
  const base = new THREE.Matrix4();
  const local = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const one = new THREE.Vector3(1, 1, 1);
  for (const it of items) {
    const e = CATALOG[it.kind];
    if (!e) continue;
    base.compose(new THREE.Vector3(it.x, it.y ?? 0, it.z), q.setFromAxisAngle(up, it.rot), one);
    for (const p of e.parts) {
      local.compose(new THREE.Vector3(p.x, p.y ?? 0, p.z), new THREE.Quaternion().setFromAxisAngle(up, p.rot ?? 0), one);
      const list = byModel.get(p.model) ?? [];
      list.push({ matrix: base.clone().multiply(local), itemId: it.id });
      byModel.set(p.model, list);
    }
  }
  return byModel;
}

export const FurnitureLayer = memo(function FurnitureLayer({
  items,
  onPick,
  shadows = true,
  pickOn = "click",
}: {
  items: PlacedItem[];
  onPick?: (itemId: string, e: ThreeEvent<MouseEvent | PointerEvent>) => void;
  shadows?: boolean;
  pickOn?: "click" | "pointerdown";
}) {
  const groups = useMemo(() => placementsFor(items), [items]);
  return (
    <>
      <ProceduralItems items={items} onPick={onPick} pickOn={pickOn} shadows={shadows} />
      {[...groups.entries()].map(([model, placements]) => (
        <Suspense key={model} fallback={null}>
          <ModelInstances model={model} placements={placements} onPick={onPick} shadows={shadows} pickOn={pickOn} />
        </Suspense>
      ))}
    </>
  );
});

function ModelInstances({
  model,
  placements,
  onPick,
  shadows,
  pickOn,
}: {
  model: string;
  placements: Placement[];
  onPick?: (itemId: string, e: ThreeEvent<MouseEvent | PointerEvent>) => void;
  shadows: boolean;
  pickOn: "click" | "pointerdown";
}) {
  const parts = useModelParts(model);
  return (
    <>
      {parts.map((part, i) => (
        <PartInstances key={`${i}:${placements.length}`} part={part} placements={placements} onPick={onPick} shadows={shadows} pickOn={pickOn} />
      ))}
    </>
  );
}

function PartInstances({
  part,
  placements,
  onPick,
  shadows,
  pickOn,
}: {
  part: ReturnType<typeof useModelParts>[number];
  placements: Placement[];
  onPick?: (itemId: string, e: ThreeEvent<MouseEvent | PointerEvent>) => void;
  shadows: boolean;
  pickOn: "click" | "pointerdown";
}) {
  const ref = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const m = new THREE.Matrix4();
    placements.forEach((p, i) => mesh.setMatrixAt(i, m.multiplyMatrices(p.matrix, part.matrix)));
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }, [placements, part]);
  return (
    <instancedMesh
      ref={ref}
      args={[part.geometry, part.material, placements.length]}
      castShadow={shadows}
      receiveShadow
      {...{
        [pickOn === "click" ? "onClick" : "onPointerDown"]:
          onPick &&
          ((e: ThreeEvent<MouseEvent | PointerEvent>) => {
            if (e.instanceId === undefined) return;
            e.stopPropagation();
            onPick(placements[e.instanceId].itemId, e);
          }),
      }}
    />
  );
}
