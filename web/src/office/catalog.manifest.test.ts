/**
 * The server places furniture for world-building agents, so it needs the catalog's keys and
 * sizes: server/app/world/catalog.json. This keeps that file in sync with the catalog.
 *
 *   UPDATE_MANIFEST=1 npx vitest run src/office/catalog.manifest.test.ts   # regenerate it
 */
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { CATALOG } from "./catalog";

const FILE = resolve(__dirname, "../../../server/app/world/catalog.json");

function manifest() {
  const out: Record<string, { label: string; category: string; size: [number, number]; blocking: boolean; seats: number; workstation: boolean }> = {};
  for (const [key, e] of Object.entries(CATALOG).sort(([a], [b]) => a.localeCompare(b))) {
    if (e.category === "Custom") continue; // runtime assets: the server knows those itself
    out[key] = {
      label: e.label,
      category: e.category,
      size: [Math.round(e.size[0] * 100) / 100, Math.round(e.size[1] * 100) / 100],
      blocking: e.blocking !== false,
      seats: e.seats.length,
      workstation: !!e.workstation,
    };
  }
  return out;
}

describe("catalog manifest for the server", () => {
  it("matches server/app/world/catalog.json", () => {
    const text = JSON.stringify(manifest(), null, 1) + "\n";
    if (process.env.UPDATE_MANIFEST) writeFileSync(FILE, text);
    expect(readFileSync(FILE, "utf8"), "run: UPDATE_MANIFEST=1 npx vitest run src/office/catalog.manifest.test.ts").toBe(text);
  });
});
