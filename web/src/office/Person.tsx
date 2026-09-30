"use client";

/**
 * An agent's rigged, animated character (Kenney "Mini Characters", CC0 — or
 * the RobotExpressive robot). Each agent gets its own skeleton clone; the
 * position, facing and animation are driven every frame from its Motion.
 */

import { useGLTF } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";
import type { Motion } from "./brain";
import { S } from "./models";

import { characterFor, HUMANS, type CharacterKey } from "./characters";

export { characterFor };

interface Rig {
  url: string;
  scale: number | "fit";
  height?: number; // for "fit"
  clips: { idle: string; walk: string; sit: string; wave: string; yes: string; no: string; thumbs: string };
  sitLift: number;
  tint: boolean;
}

const KENNEY: Omit<Rig, "url"> = {
  scale: S * 0.85,
  clips: { idle: "idle", walk: "walk", sit: "sit", wave: "interact-right", yes: "emote-yes", no: "emote-no", thumbs: "emote-yes" },
  sitLift: 0.3,
  tint: false,
};
const RIGS: Record<CharacterKey, Rig> = {
  ...Object.fromEntries(HUMANS.map((h) => [h, { ...KENNEY, url: `/models/people/character-${h}.glb` }])),
  robot: {
    url: "/models/robot.glb",
    scale: "fit",
    height: 1.25,
    clips: { idle: "Idle", walk: "Walking", sit: "Sitting", wave: "Wave", yes: "Yes", no: "No", thumbs: "ThumbsUp" },
    sitLift: 0.3,
    tint: true,
  },
} as Record<CharacterKey, Rig>;


export function preloadCharacters(keys: CharacterKey[]): void {
  for (const k of new Set(keys)) useGLTF.preload(RIGS[k].url);
}

const FADE = 0.22;
const ONE_SHOT = new Set(["yes", "no", "thumbs"]);

export function Person({
  motion,
  character,
  outfit,
  accent,
  selected,
  dim,
  onClick,
  children,
}: {
  motion: Motion;
  character: CharacterKey;
  outfit: string;
  accent: string;
  selected: boolean;
  dim: boolean;
  onClick?: () => void;
  children?: ReactNode;
}) {
  const rig = RIGS[character];
  const gltf = useGLTF(rig.url);
  const group = useRef<THREE.Group>(null);

  const { scene, scale, lift } = useMemo(() => {
    const s = cloneSkinned(gltf.scene) as THREE.Group;
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const h = box.max.y - box.min.y || 1;
    const sc = rig.scale === "fit" ? (rig.height ?? 1.3) / h : rig.scale;
    s.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true;
      mesh.frustumCulled = false;
      const src = mesh.material as THREE.MeshStandardMaterial;
      const mat = src.clone();
      if (rig.tint) {
        if (src.name === "Main") mat.color = new THREE.Color(outfit);
        else if (src.name === "Grey") mat.color = new THREE.Color(accent).lerp(new THREE.Color("#d9dce3"), 0.55);
      }
      mesh.material = mat;
    });
    return { scene: s, scale: sc, lift: -box.min.y * sc };
  }, [gltf.scene, rig, outfit, accent]);

  const mixer = useMemo(() => new THREE.AnimationMixer(scene), [scene]);
  const actions = useMemo(() => {
    const out: Record<string, THREE.AnimationAction> = {};
    for (const clip of gltf.animations) out[clip.name] = mixer.clipAction(clip);
    for (const k of ONE_SHOT) {
      const a = out[rig.clips[k as keyof Rig["clips"]]];
      if (a) {
        a.setLoop(THREE.LoopOnce, 1);
        a.clampWhenFinished = true;
      }
    }
    return out;
  }, [gltf.animations, mixer, rig]);

  const current = useRef("");
  const finished = useRef("");
  useEffect(() => {
    const onFinished = (e: { action: THREE.AnimationAction }) => {
      finished.current = e.action.getClip().name;
    };
    mixer.addEventListener("finished", onFinished);
    return () => {
      mixer.removeEventListener("finished", onFinished);
      mixer.stopAllAction();
    };
  }, [mixer]);

  useEffect(() => {
    scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.transparent = dim;
      mat.opacity = dim ? 0.4 : 1;
    });
  }, [scene, dim]);

  const play = (name: string, restart = false) => {
    const next = actions[name];
    if (!next) return;
    if (current.current === name && !restart) return;
    const prev = current.current ? actions[current.current] : null;
    next.reset().setEffectiveWeight(1).fadeIn(FADE).play();
    if (prev && prev !== next) prev.fadeOut(FADE);
    current.current = name;
  };

  const lifted = useRef(0);
  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    const c = rig.clips;
    let want: string;
    if (motion.moving) {
      want = c.walk;
      finished.current = "";
    } else if (motion.emote) {
      const clip = c[motion.emote === "wave" ? "wave" : motion.emote];
      if (finished.current === clip) {
        if (motion.loopEmote) {
          finished.current = "";
          play(clip, true);
        } else {
          motion.emote = null;
        }
      }
      want = motion.emote ? clip : motion.pose === "sit" ? c.sit : c.idle;
      if (motion.emote === "wave" && !ONE_SHOT.has("wave")) {
        // interact/wave clips loop by default; nothing else to do
      }
    } else {
      want = motion.pose === "sit" ? c.sit : c.idle;
    }
    play(want);
    const sitting = !motion.moving && current.current === c.sit;
    lifted.current += ((sitting ? rig.sitLift : 0) - lifted.current) * Math.min(1, dt * 8);
    g.position.set(motion.pos[0], lifted.current, motion.pos[1]);
    g.rotation.y = motion.heading;
    mixer.update(Math.min(dt, 0.1));
  });

  return (
    <group
      ref={group}
      onClick={
        onClick &&
        ((e) => {
          e.stopPropagation();
          onClick();
        })
      }
      onPointerOver={onClick && (() => (document.body.style.cursor = "pointer"))}
      onPointerOut={onClick && (() => (document.body.style.cursor = ""))}
    >
      <primitive object={scene} scale={scale} position={[0, lift, 0]} />
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03 - lifted.current, 0]}>
          <ringGeometry args={[0.45, 0.56, 40]} />
          <meshBasicMaterial color={accent} transparent opacity={0.95} />
        </mesh>
      )}
      {children}
    </group>
  );
}
