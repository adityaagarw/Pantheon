"use client";

/**
 * Furniture renderer. Every Kenney model is drawn with one InstancedMesh per
 * sub-mesh for all of its placements, so a 50-desk office costs about the
 * same number of draw calls as a 5-desk one.
 */

import { useGLTF } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { memo, Suspense, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";
import { CATALOG } from "./catalog";
import type { PlacedItem } from "./design";
import { footprint, modelUrl, scaleOf, useModelParts } from "./models";
import { ProceduralItems, ProceduralModel } from "./Procedural";

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
  // Animated things are drawn one by one; everything else shares instanced meshes.
  const still = useMemo(() => items.filter((i) => !i.anim), [items]);
  const moving = useMemo(() => items.filter((i) => i.anim), [items]);
  const groups = useMemo(() => placementsFor(still), [still]);
  return (
    <>
      <ProceduralItems items={still} onPick={onPick} pickOn={pickOn} shadows={shadows} />
      {[...groups.entries()].map(([model, placements]) => (
        <Suspense key={model} fallback={null}>
          <ModelInstances model={model} placements={placements} onPick={onPick} shadows={shadows} pickOn={pickOn} />
        </Suspense>
      ))}
      {moving.map((it) => (
        <Suspense key={it.id} fallback={null}>
          <AnimatedItem item={it} onPick={onPick} pickOn={pickOn} shadows={shadows} />
        </Suspense>
      ))}
    </>
  );
});

/** One placed item that moves: spin, bob, pulse, swing, or an animation clip in its model. */
function AnimatedItem({
  item,
  onPick,
  pickOn,
  shadows,
}: {
  item: PlacedItem;
  onPick?: (itemId: string, e: ThreeEvent<MouseEvent | PointerEvent>) => void;
  pickOn: "click" | "pointerdown";
  shadows: boolean;
}) {
  const e = CATALOG[item.kind];
  const inner = useRef<THREE.Group>(null);
  const a = item.anim ?? {};
  // A phase from the id, so not every spinning lamp moves in step.
  const t0 = useMemo(() => [...item.id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 997, 7) / 97, [item.id]);
  useFrame(({ clock }) => {
    const g = inner.current;
    if (!g) return;
    const t = clock.elapsedTime + t0;
    g.rotation.y = (a.spin ? t * a.spin * Math.PI * 2 : 0) + (a.swing ? Math.sin(t * 1.4) * THREE.MathUtils.degToRad(a.swing) : 0);
    g.position.y = a.bob ? (Math.sin(t * 2) * 0.5 + 0.5) * a.bob : 0;
    const s = a.pulse ? 1 + Math.sin(t * 3) * a.pulse : 1;
    g.scale.setScalar(s);
  });
  if (!e) return null;
  const pick = onPick && {
    [pickOn === "click" ? "onClick" : "onPointerDown"]: (ev: ThreeEvent<MouseEvent | PointerEvent>) => {
      ev.stopPropagation();
      onPick(item.id, ev);
    },
  };
  const single = e.parts.length === 1 ? e.parts[0] : null;
  return (
    <group position={[item.x, item.y ?? 0, item.z]} rotation={[0, item.rot, 0]} {...pick}>
      <group ref={inner}>
        {e.procedural ? (
          <ProceduralModel parts={e.procedural} shadows={shadows} />
        ) : a.clip && single ? (
          <group position={[single.x, single.y ?? 0, single.z]} rotation={[0, single.rot ?? 0, 0]}>
            <ClipModel model={single.model} clip={a.clip} shadows={shadows} />
          </group>
        ) : (
          e.parts.map((p, i) => (
            <group key={i} position={[p.x, p.y ?? 0, p.z]} rotation={[0, p.rot ?? 0, 0]}>
              <ModelMeshes model={p.model} shadows={shadows} />
            </group>
          ))
        )}
      </group>
    </group>
  );
}

function ModelMeshes({ model, shadows }: { model: string; shadows: boolean }) {
  const parts = useModelParts(model);
  return (
    <>
      {parts.map((part, i) => (
        <mesh key={i} geometry={part.geometry} material={part.material} matrix={part.matrix} matrixAutoUpdate={false} castShadow={shadows} receiveShadow />
      ))}
    </>
  );
}

/** A model playing one of its own animation clips (imported models often have some). */
function ClipModel({ model, clip, shadows }: { model: string; clip: string; shadows: boolean }) {
  const gltf = useGLTF(modelUrl(model));
  const scene = useMemo(() => {
    const s = cloneSkinned(gltf.scene) as THREE.Group;
    s.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).castShadow = shadows;
    });
    return s;
  }, [gltf.scene, shadows]);
  const mixer = useMemo(() => new THREE.AnimationMixer(scene), [scene]);
  useEffect(() => {
    const found = gltf.animations.find((c) => c.name === clip) ?? gltf.animations.find((c) => c.name.toLowerCase().includes(clip.toLowerCase()));
    if (!found) return;
    const action = mixer.clipAction(found);
    action.play();
    return () => {
      action.stop();
    };
  }, [gltf.animations, mixer, clip]);
  useFrame((_, dt) => mixer.update(Math.min(dt, 0.1)));
  const fp = footprint(model);
  const sc = scaleOf(model);
  return (
    <group scale={sc}>
      <primitive object={scene} position={[-fp.cx, -fp.minY, -fp.cz]} />
    </group>
  );
}

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
