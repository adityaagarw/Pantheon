/**
 * Palette previews for procedural assets: one shared offscreen renderer draws each
 * model once from a three-quarter view and caches the result as a data URL.
 */

import * as THREE from "three";
import type { PrimitivePart } from "@/lib/types";

const SIZE = 128;
const cache = new Map<string, string>();
let renderer: THREE.WebGLRenderer | null = null;

const geometries: Record<PrimitivePart["shape"], () => THREE.BufferGeometry> = {
  box: () => new THREE.BoxGeometry(1, 1, 1),
  sphere: () => new THREE.SphereGeometry(0.5, 20, 14),
  cone: () => new THREE.ConeGeometry(0.5, 1, 20),
  cylinder: () => new THREE.CylinderGeometry(0.5, 0.5, 1, 20),
};

export function proceduralThumb(key: string, parts: PrimitivePart[]): string | null {
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  try {
    renderer ??= new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setSize(SIZE, SIZE);
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight("#ffffff", "#8a8f99", 1.6));
    const sun = new THREE.DirectionalLight("#ffffff", 1.6);
    sun.position.set(3, 6, 5);
    scene.add(sun);

    const group = new THREE.Group();
    const disposables: { dispose(): void }[] = [];
    for (const p of parts) {
      const glass = p.opacity !== undefined && p.opacity < 1;
      const geo = geometries[p.shape]?.() ?? geometries.box();
      const mat = new THREE.MeshStandardMaterial({
        color: p.color,
        roughness: glass ? 0.08 : 0.7,
        emissive: p.emissive ?? "#000000",
        emissiveIntensity: p.emissive ? 1.4 : 0,
        transparent: glass,
        opacity: p.opacity ?? 1,
        depthWrite: !glass,
      });
      disposables.push(geo, mat);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(...p.pos);
      mesh.rotation.set(...p.rot);
      mesh.scale.set(...p.size);
      group.add(mesh);
    }
    scene.add(group);

    // Frame the model: look from the front-right and fit its bounding sphere.
    const sphere = new THREE.Box3().setFromObject(group).getBoundingSphere(new THREE.Sphere());
    const camera = new THREE.PerspectiveCamera(30, 1, 0.01, 1000);
    const dist = sphere.radius / Math.sin(THREE.MathUtils.degToRad(15)) || 3;
    camera.position.copy(sphere.center).add(new THREE.Vector3(0.55, 0.5, 0.8).normalize().multiplyScalar(dist));
    camera.lookAt(sphere.center);

    renderer.setClearColor(0x000000, 0);
    renderer.render(scene, camera);
    const url = renderer.domElement.toDataURL("image/png");
    disposables.forEach((d) => d.dispose());
    cache.set(key, url);
    return url;
  } catch {
    cache.set(key, ""); // no WebGL: the palette falls back to a plain icon
    return null;
  }
}
