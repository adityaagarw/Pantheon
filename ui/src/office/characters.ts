/**
 * Characters for agents, from packs (registry.ts). The original 57 (Kenney's people,
 * blocky folk, animals and fantasy characters, and a robot) are the office-classic pack.
 * No 3D imports here: safe for any page.
 */

export type CharacterKey = string;

/** How a character is animated: its clips, size and how it sits. */
export interface RigDef {
  /** Model units to meters, or `fit` to a height in meters instead. */
  scale?: number;
  fit?: number;
  sitLift: number;
  /** The model's "Main" and "Grey" materials take the agent's outfit and accent colours. */
  tint?: boolean;
  clips: { idle: string; walk: string; sit: string; wave: string; yes: string; no: string; thumbs: string };
}

export interface CharacterDef {
  key: CharacterKey;
  label: string;
  group: string;
  model: string; // URL
  thumb?: string; // URL
  rig: string; // a rig id
  /** One of the characters agents get when they haven't picked one. */
  default?: boolean;
  /** Overrides the rig's size: fit to this height in meters. */
  fit?: number;
}

let CHARACTERS: CharacterDef[] = [];
let BY_KEY: Record<string, CharacterDef> = {};
let RIGS: Record<string, RigDef> = {};

export function registerCharacters(list: CharacterDef[], rigs: Record<string, RigDef>): void {
  BY_KEY = {};
  for (const c of list) BY_KEY[c.key] = c; // a later pack's key wins
  CHARACTERS = Object.values(BY_KEY);
  RIGS = rigs;
}

export const character = (k: CharacterKey): CharacterDef | undefined => BY_KEY[k];
export const rigOf = (c: CharacterDef): RigDef | undefined => RIGS[c.rig];

/** The characters by group, in the order the packs list them. */
export function characterGroups(): { label: string; keys: string[] }[] {
  const groups = new Map<string, string[]>();
  for (const c of CHARACTERS) groups.set(c.group, [...(groups.get(c.group) ?? []), c.key]);
  return [...groups].map(([label, keys]) => ({ label, keys }));
}

/** An agent's character: its own pick, else one of the defaults chosen by its id. */
export function characterFor(agent: { id: string; avatar?: { character?: string } }): CharacterKey {
  const chosen = agent.avatar?.character;
  if (chosen && BY_KEY[chosen]) return chosen;
  const pool = CHARACTERS.filter((c) => c.default);
  const from = pool.length ? pool : CHARACTERS;
  if (!from.length) return chosen ?? "";
  let h = 0;
  for (const ch of agent.id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return from[h % from.length].key;
}

export function characterThumb(k: CharacterKey): string {
  return BY_KEY[k]?.thumb ?? "";
}

/** A readable name for a character key: "pet-polar" -> "Polar". */
export function characterLabel(k: CharacterKey): string {
  if (BY_KEY[k]) return BY_KEY[k].label;
  const s = k.replace(/^(pet|blocky|dungeon)-/, "").replace(/-/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
