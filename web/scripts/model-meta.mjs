// Precompute bounding boxes of the furniture/character models (Kenney units).
// Usage: node scripts/model-meta.mjs > src/office/modelMeta.json
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

globalThis.self = globalThis;
const root = path.resolve(import.meta.dirname, "..", "public", "models");
const loader = new GLTFLoader();
const out = {};

async function measure(dir, file) {
  const buf = readFileSync(path.join(root, dir, file));
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const gltf = await new Promise((res, rej) =>
    loader.parse(ab, path.join(root, dir) + "/", res, rej),
  ).catch(() => null);
  if (!gltf) return null;
  gltf.scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(gltf.scene);
  const r = (v) => Math.round(v * 1000) / 1000;
  return { min: box.min.toArray().map(r), max: box.max.toArray().map(r), anims: gltf.animations.map((a) => a.name) };
}

for (const dir of ["furniture", "people"]) {
  for (const file of readdirSync(path.join(root, dir)).filter((f) => f.endsWith(".glb"))) {
    const m = await measure(dir, file);
    if (m) out[`${dir}/${file.replace(".glb", "")}`] = dir === "people" ? { min: m.min, max: m.max } : { min: m.min, max: m.max };
  }
}
process.stdout.write(JSON.stringify(out, null, 0));
