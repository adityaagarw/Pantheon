// Ready-made art for Stage pages: Lucide icons and Open Peeps people, as SVG.
//
//   import { icon, iconNames, peep, peepParts } from "stage/art";
//   el.innerHTML = await icon("door-open", { size: 64, color: "#58c4dd", stroke: 2 });
//   el.innerHTML = await peep({ pose: "standing/WalkingBW", face: "Smile", hair: "ShortVolumed",
//                               stroke: "#ece9e4", fill: "#1a2130", height: 360 });
//
// Both return SVG markup. Pass { inner: true } to get only the shapes, for placing inside
// your own <svg> (icons are a 24x24 drawing; peeps a 850x1200 one, head at the top middle).
// The data is fetched on first use (icons ~450 KB, peeps ~1.9 MB).

let iconsP, peepsP;
const icons = () => (iconsP ??= import("./icons/icons-data.js").then((m) => m.default));
const peeps = () => (peepsP ??= import("./peeps/peeps-data.js").then((m) => m.default));

/** Every icon name (about 2,000: "rocket", "door-open", "brain", "mountain", "users"…). */
export async function iconNames(filter) {
  const all = Object.keys(await icons());
  return filter ? all.filter((n) => n.includes(filter)) : all;
}

/** A Lucide icon as SVG. Unknown names throw with close matches. */
export async function icon(name, { size = 48, color = "currentColor", stroke = 2, inner = false } = {}) {
  const all = await icons();
  const body = all[name];
  if (body === undefined) {
    const near = Object.keys(all).filter((n) => n.includes(name.split("-")[0])).slice(0, 12);
    throw new Error(`no icon "${name}"${near.length ? `; try: ${near.join(", ")}` : ""}`);
  }
  if (inner) return `<g fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${body}</g>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
}

/** The pieces a peep can be made of: { standing, sitting, bust, hair, face, facialHair, accessory }. */
export async function peepParts() {
  const d = await peeps();
  return Object.fromEntries(Object.entries(d).map(([k, v]) => [k, Object.keys(v)]));
}

/**
 * An Open Peeps person. pose: "standing/<name>", "sitting/<name>" or "bust/<name>" (or just a
 * name, searched in that order); face, hair, facialHair, accessory: piece names (optional).
 * stroke: line colour; fill: the colour inside the shapes (clothes, skin).
 */
export async function peep({ pose = "standing/WalkingBW", face = "Smile", hair = "Short", facialHair,
  accessory, stroke = "#ece9e4", fill = "#1a2130", height = 300, inner = false } = {}) {
  const d = await peeps();
  const pick = (group, name) => {
    if (!name) return "";
    const s = d[group]?.[name];
    if (s === undefined) throw new Error(`no ${group} "${name}"; try: ${Object.keys(d[group] ?? {}).join(", ")}`);
    return s;
  };
  let body = "";
  if (pose) {
    const [g, n] = pose.includes("/") ? pose.split("/") : [["standing", "sitting", "bust"].find((x) => d[x][pose]), pose];
    if (!g) throw new Error(`no pose "${pose}"; try standing/…: ${Object.keys(d.standing).join(", ")}`);
    body = pick(g, n);
  }
  const head = `<g transform="translate(225 0)"><g>${pick("hair", hair)}</g>` +
    `<g transform="translate(159 186)">${pick("face", face)}</g>` +
    `<g transform="translate(123 338)">${pick("facialHair", facialHair)}</g>` +
    `<g transform="translate(47 241)">${pick("accessory", accessory)}</g></g>`;
  const shapes = `<g>${body}${head}</g>`.replaceAll("#S#", stroke).replaceAll("#F#", fill);
  if (inner) return shapes;
  const w = Math.round((height * 850) / 1200);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${height}" viewBox="0 0 850 1200">${shapes}</svg>`;
}
