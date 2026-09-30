"use client";

// Developer page: renders every Kenney model re-centered, with a red arrow on
// +z, to verify orientation and footprints. Not linked from the UI.
import { Html, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import dynamic from "next/dynamic";
import { Suspense } from "react";
import meta from "@/office/modelMeta.json";
import { footprint, useModelParts } from "@/office/models";

function Model({ model, x, z }: { model: string; x: number; z: number }) {
  const parts = useModelParts(model);
  const fp = footprint(model);
  return (
    <group position={[x, 0, z]}>
      {parts.map((p, i) => (
        <mesh key={i} geometry={p.geometry} material={p.material} matrixAutoUpdate={false} matrix={p.matrix} />
      ))}
      <mesh position={[0, 0.01, fp.d / 2 + 0.25]} rotation={[Math.PI / 2, 0, 0]}>
        <coneGeometry args={[0.12, 0.3, 8]} />
        <meshBasicMaterial color="red" />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.005, 0]}>
        <planeGeometry args={[fp.w, fp.d]} />
        <meshBasicMaterial color="#9ad" transparent opacity={0.35} />
      </mesh>
      <Html position={[0, -0.05, -fp.d / 2 - 0.3]} center style={{ fontSize: 10, color: "#111", whiteSpace: "nowrap" }}>
        {model.split("/")[1]}
      </Html>
    </group>
  );
}

function Catalog() {
  const filter = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("q") ?? "" : "";
  const keys = Object.keys(meta).filter((k) => k.startsWith("furniture/") && k.includes(filter));
  const cols = 8;
  return (
    <Canvas camera={{ position: [Math.min(keys.length, cols) * 1.5, 4 + keys.length * 0.4, 6 + Math.ceil(keys.length / cols) * 3], fov: 40 }} style={{ height: "100vh", background: "#eee" }}>
      <ambientLight intensity={1.2} />
      <directionalLight position={[5, 10, 8]} intensity={1.5} />
      <Suspense fallback={null}>
        {keys.map((k, i) => (
          <Model key={k} model={k} x={(i % cols) * 3} z={Math.floor(i / cols) * 3} />
        ))}
      </Suspense>
      <OrbitControls target={[(Math.min(keys.length, cols) - 1) * 1.5, 0, (Math.ceil(keys.length / cols) - 1) * 1.5]} />
    </Canvas>
  );
}

export default dynamic(() => Promise.resolve(Catalog), { ssr: false });
