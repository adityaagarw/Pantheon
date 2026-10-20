/** How things look in the Studio: an agent's colour, and what each state looks like. */
import type { Agent, RuntimeStatus } from "@/lib/types";

/** An agent's hue, the same everywhere it appears: from its chosen accent, else its name. */
export function agentHue(agent: Pick<Agent, "name" | "avatar"> | undefined): number {
  const accent = agent?.avatar?.accent;
  if (accent && /^#[0-9a-f]{6}$/i.test(accent)) return hexHue(accent);
  let h = 0;
  for (const ch of agent?.name ?? "?") h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 360;
}

export const hsl = (hue: number, s = 82, l = 68, a = 1) => `hsla(${hue}, ${s}%, ${l}%, ${a})`;

function hexHue(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  if (d === 0) return 250;
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return Math.round((h * 60 + 360) % 360);
}

export interface StateLook {
  label: string;
  color: string;
  /** Moves: a working agent breathes; one waiting for you glows. */
  motion: "breathe" | "glow" | "none";
  /** Calls for the user's attention. */
  loud: boolean;
}

export function stateLook(status: RuntimeStatus | undefined, agentStatus?: Agent["status"]): StateLook {
  if (agentStatus === "paused") return { label: "Paused", color: "var(--st-ink-3)", motion: "none", loud: false };
  if (agentStatus === "standby") return { label: "Standby", color: "var(--st-ink-3)", motion: "none", loud: false };
  switch (status) {
    case "working":
      return { label: "Working", color: "var(--st-violet)", motion: "breathe", loud: false };
    case "awaiting_approval":
      return { label: "Needs you", color: "var(--st-amber)", motion: "glow", loud: true };
    case "error":
      return { label: "Failed", color: "var(--st-red)", motion: "glow", loud: true };
    case "retrying":
      return { label: "Retrying", color: "var(--st-amber)", motion: "none", loud: false };
    case "cooling_down":
      return { label: "Cooling down", color: "var(--st-cyan)", motion: "none", loud: false };
    case "queued":
      return { label: "Queued", color: "var(--st-cyan)", motion: "none", loud: false };
    default:
      return { label: "Idle", color: "var(--st-ink-3)", motion: "none", loud: false };
  }
}

/** Initials for an agent's mark. */
export const initials = (name: string) =>
  name.split(/[\s_-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";
