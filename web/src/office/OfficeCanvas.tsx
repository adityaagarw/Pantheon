"use client";

import { Canvas } from "@react-three/fiber";
import { Suspense } from "react";
import * as THREE from "three";
import type { Agent, Task, WhiteboardInfo } from "@/lib/types";
import type { OrgState } from "@/store/reduce";
import type { OfficeDesign } from "./design";
import { OfficeScene } from "./Scene";

export default function OfficeCanvas(props: {
  design: OfficeDesign;
  editing: boolean;
  agents: Agent[];
  state: Pick<OrgState, "live" | "meetings" | "beams" | "agents" | "objects">;
  tasks: Task[];
  selectedId: string | null;
  focusId: string | null;
  onSelect: (id: string | null) => void;
  onBoardClick: () => void;
  whiteboards?: WhiteboardInfo[];
  onWhiteboardClick?: (boardId: string) => void;
}) {
  return (
    <Canvas
      shadows={{ type: THREE.PCFShadowMap }}
      dpr={[1, 1.75]}
      camera={{ fov: 40, near: 0.1, far: 400, position: [20, 25, 40] }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
      onPointerMissed={() => !props.editing && props.onSelect(null)}
    >
      <Suspense fallback={null}>
        <OfficeScene {...props} />
      </Suspense>
    </Canvas>
  );
}
