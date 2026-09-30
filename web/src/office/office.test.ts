import { describe, expect, it } from "vitest";
import { CATALOG, itemBounds, toWorld } from "./catalog";
import { generateDesign, overlap, resolveDesign } from "./design";
import { buildGrid, CELL, findPath } from "./pathfinding";

const agents = (n: number, teams = ["A", "B", "C"]) =>
  Array.from({ length: n }, (_, i) => ({
    id: `agt_${i}`,
    team: teams[i % teams.length],
    createdAt: `2026-01-01T00:00:${String(i).padStart(2, "0")}Z`,
  }));

describe("generated office", () => {
  it("gives every agent a real workstation", () => {
    const people = agents(13);
    const office = resolveDesign(generateDesign(people), people);
    expect(office.desks.map((d) => d.agentId).sort()).toEqual(people.map((a) => a.id).sort());
    expect(office.autoDesks).toBe(0);
    expect(office.meetingRooms.length).toBeGreaterThanOrEqual(1);
    expect(office.meetingRooms[0].seats.length).toBeGreaterThanOrEqual(6);
    expect(office.loungeSpots.length).toBeGreaterThan(0);
  });

  it("has no overlapping blocking furniture", () => {
    const d = generateDesign(agents(10));
    const blocking = d.items.filter((i) => CATALOG[i.kind]?.blocking && !i.y);
    for (let a = 0; a < blocking.length; a++)
      for (let b = a + 1; b < blocking.length; b++) {
        const A = itemBounds(blocking[a]);
        const B = itemBounds(blocking[b]);
        const shrink = (r: typeof A) => ({ x: r.x + 0.06, z: r.z + 0.06, w: r.w - 0.12, d: r.d - 0.12 });
        expect(overlap(shrink(A), shrink(B)), `${blocking[a].kind} vs ${blocking[b].kind}`).toBe(false);
      }
  });

  it("adds desks automatically when a saved design is too small", () => {
    const d = generateDesign(agents(2));
    const office = resolveDesign(d, agents(5));
    expect(office.desks).toHaveLength(5);
    expect(office.autoDesks).toBe(1);
  });

  it("honours explicit desk assignments", () => {
    const people = agents(4);
    const d = generateDesign(people);
    const last = d.items.filter((i) => i.kind === "workstation").at(-1)!;
    last.agentId = "agt_0";
    const office = resolveDesign(d, people);
    expect(office.desks.find((x) => x.agentId === "agt_0")!.itemId).toBe(last.id);
  });
});

describe("pathfinding", () => {
  it("routes every agent from their desk into the boardroom", () => {
    const people = agents(12);
    const office = resolveDesign(generateDesign(people), people);
    const g = buildGrid(office);
    const seat = office.meetingRooms[0].seats[0].pos;
    for (const d of office.desks) {
      const path = findPath(g, d.seat, seat);
      expect(path.length).toBeGreaterThan(1);
      for (const p of path.slice(1, -1)) {
        expect(g.blocked[Math.floor(p[1] / CELL) * g.w + Math.floor(p[0] / CELL)]).toBe(0);
      }
    }
  });

  it("rotates local points like three.js", () => {
    const [x, z] = toWorld({ x: 0, z: 0, rot: Math.PI / 2 }, 0, 1);
    expect(x).toBeCloseTo(1);
    expect(z).toBeCloseTo(0);
  });
});

describe("physical presence", () => {
  it("sends an agent to a spot inside the room it chose", async () => {
    const { locationIntent, newMotion } = await import("./brain");
    const people = agents(4);
    const office = resolveDesign(generateDesign(people), people);
    const lounge = office.design.rooms.find((r) => r.name === "Lounge")!;
    const motions = new Map(people.map((p) => [p.id, newMotion([1, 1])]));
    const intent = locationIntent("agt_0", { kind: "room", room: "Lounge" }, office, motions)!;
    const [x, z] = intent.target;
    expect(x >= lounge.x && x <= lounge.x + lounge.w && z >= lounge.z && z <= lounge.z + lounge.d).toBe(true);
    const near = locationIntent("agt_0", { kind: "agent", agentId: "agt_1" }, office, motions)!;
    expect(Math.hypot(near.target[0] - 1, near.target[1] - 1)).toBeCloseTo(0.95, 1);
  });

  it("places named extras at a free spot in their room", async () => {
    const { withExtras } = await import("./design");
    const people = agents(3);
    const base = generateDesign(people);
    const d = withExtras(base, [{ id: "ex_1", kind: "pottedPlant", room: "Kitchen" }]);
    const it = d.items.find((i) => i.id === "ex_1")!;
    const kitchen = d.rooms.find((r) => r.name === "Kitchen")!;
    expect(it.x > kitchen.x && it.x < kitchen.x + kitchen.w && it.z > kitchen.z && it.z < kitchen.z + kitchen.d).toBe(true);
    expect(withExtras(d, [{ id: "ex_1", kind: "pottedPlant", room: "Kitchen" }]).items.length).toBe(d.items.length);
  });

  it("scenes without desks give people a spot in their team's room", () => {
    const people = [{ id: "a", team: "Market", createdAt: "2026-01-01T00:00:00Z" }];
    const office = resolveDesign(
      { version: 1, width: 20, depth: 20, floor: "asphalt", autoDesks: false, items: [], rooms: [{ id: "r", name: "Market", type: "custom", x: 2, z: 2, w: 8, d: 8, walls: "none", door: "n" }] },
      people,
    );
    expect(office.autoDesks).toBe(0);
    expect(office.design.items.length).toBe(0);
    const [x, z] = office.desks[0].seat;
    expect(x > 2 && x < 10 && z > 2 && z < 10).toBe(true);
  });
});
