/**
 * 3D models for the office. Each comes from a pack (registry.ts: its URL, bounds, and the
 * scale of its kit, which makes it life-sized next to the furniture) or is a runtime
 * asset (imported or built by Zeus). Every model is re-centered on its footprint with its
 * base on the floor, so placement coordinates always mean "center of the object".
 */

import { useGLTF } from "@react-three/drei";
import { useMemo } from "react";
import * as THREE from "three";

export const S = 2; // Kenney units -> meters

interface Model {
  url: string;
  min: number[];
  max: number[];
  scale: number; // model units -> meters
}
const MODELS: Record<string, Model> = {};
const FROM_PACKS = new Set<string>();

export function registerModel(key: string, m: Model, fromPack = false): void {
  MODELS[key] = m;
  if (fromPack) FROM_PACKS.add(key);
}

/** Forget the packs' models (before registering the current set). */
export function clearPackModels(): void {
  for (const k of FROM_PACKS) delete MODELS[k];
  FROM_PACKS.clear();
}

/** Every model the packs registered, by key ("furniture/desk"). */
export const allModels = (): string[] => [...FROM_PACKS];

/** Every model in a kit folder (e.g. "nature"), by name. */
export const kitModels = (dir: string): string[] =>
  allModels()
    .filter((k) => k.startsWith(`${dir}/`))
    .map((k) => k.slice(dir.length + 1));

export const scaleOf = (model: string) => MODELS[model]?.scale ?? S;

export interface Footprint {
  w: number; // meters along local x
  d: number; // meters along local z
  h: number;
  cx: number; // model-unit center (for re-centering)
  cz: number;
  minY: number;
}

export function footprint(model: string): Footprint {
  const m = MODELS[model];
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

export const modelUrl = (model: string) => MODELS[model]?.url ?? "";

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
  for (const m of new Set(models)) if (modelUrl(m)) useGLTF.preload(modelUrl(m));
}
