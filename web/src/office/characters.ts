/** Character choices for agents (no 3D imports — safe for any page). */

export const HUMANS = [
  "female-a", "female-b", "female-c", "female-d", "female-e", "female-f",
  "male-a", "male-b", "male-c", "male-d", "male-e", "male-f",
] as const;
export const CHARACTERS = [...HUMANS, "robot"] as const;
export type CharacterKey = (typeof CHARACTERS)[number];

export function characterFor(agent: { id: string; avatar?: { character?: string } }): CharacterKey {
  const chosen = agent.avatar?.character as CharacterKey | undefined;
  if (chosen && (CHARACTERS as readonly string[]).includes(chosen)) return chosen;
  let h = 0;
  for (const c of agent.id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return HUMANS[h % HUMANS.length];
}

export const characterThumb = (k: CharacterKey) =>
  k === "robot" ? "/thumbs/people/robot.png" : `/thumbs/people/character-${k}.png`;
