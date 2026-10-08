/** Character choices for agents (no 3D imports — safe for any page). */

export const HUMANS = [
  "female-a", "female-b", "female-c", "female-d", "female-e", "female-f",
  "male-a", "male-b", "male-c", "male-d", "male-e", "male-f",
] as const;
// Kenney "Blocky Characters" (CC0)
export const BLOCKY = "abcdefghijklmnopqr".split("").map((l) => `blocky-${l}`);
// Kenney "Cube Pets" (CC0): for anyone who'd rather be helped by a fox
export const ANIMALS = [
  "beaver", "bee", "bunny", "cat", "caterpillar", "chick", "cow", "crab", "deer", "dog", "elephant", "fish",
  "fox", "giraffe", "hog", "koala", "lion", "monkey", "panda", "parrot", "penguin", "pig", "polar", "tiger",
].map((a) => `pet-${a}`);
// Kenney "Mini Dungeon" (CC0)
export const FANTASY = ["dungeon-human", "dungeon-orc"];

export const CHARACTERS: string[] = [...HUMANS, ...BLOCKY, ...ANIMALS, ...FANTASY, "robot"];
export type CharacterKey = string;

export const CHARACTER_GROUPS: { label: string; keys: string[] }[] = [
  { label: "People", keys: [...HUMANS] },
  { label: "Blocky", keys: BLOCKY },
  { label: "Animals", keys: ANIMALS },
  { label: "Fantasy", keys: FANTASY },
  { label: "Robot", keys: ["robot"] },
];

export function characterFor(agent: { id: string; avatar?: { character?: string } }): CharacterKey {
  const chosen = agent.avatar?.character;
  if (chosen && CHARACTERS.includes(chosen)) return chosen;
  let h = 0;
  for (const c of agent.id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return HUMANS[h % HUMANS.length];
}

export function characterThumb(k: CharacterKey): string {
  if (k === "robot") return "/thumbs/people/robot.png";
  if (k.startsWith("pet-")) return `/thumbs/pets/animal-${k.slice(4)}.png`;
  if (k.startsWith("blocky-")) return `/thumbs/blocky/character-${k.slice(7)}.png`;
  if (k.startsWith("dungeon-")) return `/thumbs/dungeon/character-${k.slice(8)}.png`;
  return `/thumbs/people/character-${k}.png`;
}

/** A readable name for a character key: "pet-polar" -> "Polar". */
export function characterLabel(k: CharacterKey): string {
  const s = k.replace(/^(pet|blocky|dungeon)-/, "").replace(/-/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
