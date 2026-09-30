/**
 * Kenney model loading. All Kenney kits share one unit scale; S converts it
 * to meters. Every model is re-centered on its footprint with its base on the
 * floor, so placement coordinates always mean "center of the object".
 */

import { useGLTF } from "@react-three/drei";
import { useMemo } from "react";
import * as THREE from "three";
import meta from "./modelMeta.json";

export const S = 2; // Kenney units -> meters

type Meta = Record<string, { min: number[]; max: number[] }>;
const META = meta as Meta;

/** Models added at runtime (imported / plugin GLB assets), keyed by catalog key. */
interface RuntimeModel {
  url: string;
  min: number[];
  max: number[];
  scale: number; // model units -> meters
}
const RUNTIME: Record<string, RuntimeModel> = {};

export function registerModel(key: string, m: RuntimeModel): void {
  RUNTIME[key] = m;
}

const scaleOf = (model: string) => RUNTIME[model]?.scale ?? S;

export interface Footprint {
  w: number; // meters along local x
  d: number; // meters along local z
  h: number;
  cx: number; // Kenney-unit center (for re-centering)
  cz: number;
  minY: number;
}

export function footprint(model: string): Footprint {
  const m = RUNTIME[model] ?? META[model];
  if (!m) return { w: 0.5, d: 0.5, h: 0.5, cx: 0, cz: 0, minY: 0 };
  const sc = scaleOf(model);
  return {
    w: (m.max[0] - m.min[0]) * sc,
    d: (m.max[2] - m.min[2]) * sc,
    h: (m.max[1] - m.min[1]) * sc,
    cx: (m.max[0] + m.min[0]) / 2,
    cz: (m.max[2] + m.min[2]) / 2,
    minY: m.min[1],
  };
}

export const modelUrl = (model: string) => RUNTIME[model]?.url ?? `/models/${model}.glb`;

export interface MeshPart {
  geometry: THREE.BufferGeometry;
  material: THREE.Material | THREE.Material[];
  matrix: THREE.Matrix4; // centered + scaled, relative to the placement origin
}

/** Static (non-skinned) mesh parts of a model, centered and scaled to meters. */
export function useModelParts(model: string): MeshPart[] {
  const gltf = useGLTF(modelUrl(model));
  return useMemo(() => {
    const fp = footprint(model);
    const sc = scaleOf(model);
    const root = new THREE.Matrix4()
      .makeScale(sc, sc, sc)
      .premultiply(new THREE.Matrix4())
      .multiply(new THREE.Matrix4().makeTranslation(-fp.cx, -fp.minY, -fp.cz));
    gltf.scene.updateMatrixWorld(true);
    const parts: MeshPart[] = [];
    gltf.scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mat = Array.isArray(mesh.material) ? mesh.material : mesh.material;
      parts.push({ geometry: mesh.geometry, material: mat, matrix: root.clone().multiply(mesh.matrixWorld) });
    });
    return parts;
  }, [gltf, model]);
}

export function preloadModels(models: string[]): void {
  for (const m of new Set(models)) useGLTF.preload(modelUrl(m));
}
