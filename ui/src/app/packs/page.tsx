/**
 * Packs: everything Pantheon can use beyond its core, as packs to add, switch off and
 * remove. Stage libraries (and any npm library, fetched on demand), 3D assets,
 * templates and plugins. The originals ship as packs too.
 */
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { api } from "@/lib/api";
import type { LibraryInfo, PackInfo, PackKind, PackListing } from "@/lib/types";
import { Icon } from "@/studio/icons";
import { agentHue, hsl } from "@/studio/look";

type Filter = "all" | "library" | "office" | "templates" | "plugin" | "yours";
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "library", label: "Libraries" },
  { id: "office", label: "Office" },
  { id: "templates", label: "Templates" },
  { id: "plugin", label: "Plugins" },
  { id: "yours", label: "Added by you" },
];
const KIND_LABEL: Record<PackKind, string> = {
  library: "Library",
  furniture: "Furniture",
  characters: "Characters",
  themes: "Themes",
  assets: "3D assets",
  templates: "Templates",
  plugin: "Plugin",
};
const OFFICE_KINDS: PackKind[] = ["furniture", "characters", "themes", "assets"];

/** Whether a pack shows under a filter. */
const shows = (p: PackInfo, f: Filter) =>
  f === "all" ? true : f === "yours" ? !p.bundled : f === "office" ? p.kinds.some((k) => OFFICE_KINDS.includes(k)) : p.kinds.includes(f);

const glass = "rounded-2xl border border-[var(--st-edge)] bg-[var(--st-glass)] backdrop-blur-xl";
const chip = "rounded-full border px-2.5 py-0.5 text-[11.5px]";
const btn =
  "inline-flex h-9 items-center gap-2 rounded-xl border border-[var(--st-edge)] bg-[var(--st-glass-2)] px-3.5 text-[13px] text-[var(--st-ink-2)] transition-colors hover:border-[var(--st-edge-2)] hover:text-[var(--st-ink)] disabled:opacity-50";

export default function PacksPage() {
  const [listing, setListing] = useState<PackListing | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [docs, setDocs] = useState<LibraryInfo | null>(null);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  const load = () => api.packs().then(setListing).catch((e) => setNote({ tone: "bad", text: (e as Error).message }));
  useEffect(() => {
    void load();
  }, []);

  const shown = useMemo(() => {
    if (!listing) return [];
    return listing.packs.filter((p) => shows(p, filter));
  }, [listing, filter]);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setNote(null);
    try {
      await fn();
      if (ok) setNote({ tone: "ok", text: ok });
    } catch (e) {
      setNote({ tone: "bad", text: (e as Error).message });
    }
    await load();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-4 pb-16 pt-6 md:px-8 md:pt-10">
        <header className="mb-7 flex flex-wrap items-end gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <span className="grid size-10 place-items-center rounded-xl text-white" style={{ background: "linear-gradient(140deg, #9d8cff, #4cc9f0)" }}>
                <Icon name="packs" size={20} />
              </span>
              <h1 className="st-display text-[30px] leading-none">Packs</h1>
            </div>
            <p className="mt-3 max-w-[640px] text-[14px] leading-relaxed text-[var(--st-ink-2)]">
              Everything Pantheon uses beyond its core comes in packs: libraries for the Stage, 3D assets, templates and plugins. Add your own, switch any off, remove the
              ones you added. The originals ship as packs too.
            </p>
          </div>
          <button className={btn} onClick={() => act(() => api.reloadPacks(), "Reloaded the packs folder.")} title="Pick up packs changed by hand">
            <Icon name="sessions" size={14} /> Reload
          </button>
        </header>

        <AddBar onAdded={(text) => act(async () => {}, text)} onError={(text) => setNote({ tone: "bad", text })} dir={listing?.dir ?? ""} />

        <AnimatePresence>
          {note && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mt-3 flex items-start gap-3 rounded-xl border px-4 py-2.5 text-[13px]"
              style={{
                borderColor: note.tone === "ok" ? "rgba(61,220,151,0.35)" : "rgba(255,93,108,0.4)",
                background: note.tone === "ok" ? "rgba(61,220,151,0.08)" : "rgba(255,93,108,0.08)",
                color: note.tone === "ok" ? "var(--st-green)" : "var(--st-red)",
              }}
            >
              <span className="min-w-0 flex-1 whitespace-pre-wrap">{note.text}</span>
              <button onClick={() => setNote(null)} className="opacity-70 hover:opacity-100">
                <Icon name="close" size={13} />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="mb-4 mt-7 flex flex-wrap items-center gap-1.5">
          {FILTERS.map((f) => {
            const n = listing ? listing.packs.filter((p) => shows(p, f.id)).length : 0;
            return (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className="relative rounded-full px-3.5 py-1.5 text-[13px] transition-colors"
                style={{ color: filter === f.id ? "var(--st-ink)" : "var(--st-ink-3)" }}
              >
                {filter === f.id && (
                  <motion.span layoutId="pack-filter" className="absolute inset-0 rounded-full" style={{ background: "rgba(157,140,255,0.16)", boxShadow: "inset 0 0 0 1px rgba(157,140,255,0.35)" }} />
                )}
                <span className="relative">
                  {f.label} <span className="st-mono text-[11px] opacity-60">{n}</span>
                </span>
              </button>
            );
          })}
        </div>

        {!listing ? (
          <div className="py-16 text-center text-[var(--st-ink-3)]">Loading packs…</div>
        ) : shown.length === 0 ? (
          <div className={`${glass} px-6 py-14 text-center text-[var(--st-ink-3)]`}>Nothing here yet.</div>
        ) : (
          <motion.div layout className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(330px,1fr))]">
            <AnimatePresence initial={false}>
              {shown.map((p) => (
                <PackCard
                  key={p.id}
                  pack={p}
                  onToggle={(on) => act(() => api.setPackEnabled(p.id, on), `${p.name} is ${on ? "on" : "off"}.`)}
                  onRemove={() => act(() => api.removePack(p.id), `Removed ${p.name}.`)}
                  onDocs={(lib) => setDocs(lib)}
                />
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </div>
      <DocsDrawer lib={docs} onClose={() => setDocs(null)} />
    </div>
  );
}

function AddBar({ onAdded, onError, dir }: { onAdded: (text: string) => void; onError: (text: string) => void; dir: string }) {
  const [spec, setSpec] = useState("");
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const run = async (what: string, fn: () => Promise<string>) => {
    setBusy(what);
    try {
      onAdded(await fn());
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const addNpm = () =>
    spec.trim() &&
    run("npm", async () => {
      const p = await api.addNpmLibrary(spec.trim());
      setSpec("");
      const names = p.libraries[0]?.imports.filter((i) => !i.startsWith("three/")).join(", ");
      return `Added ${p.name} ${p.version}: pages import it as "${names}".` + (p.warnings.length ? `\n${p.warnings.map((w) => `• ${w}`).join("\n")}` : "");
    });

  return (
    <div className={`${glass} relative overflow-hidden p-1.5`}>
      <div className="pointer-events-none absolute -left-20 -top-24 size-64 rounded-full opacity-40 blur-3xl" style={{ background: "radial-gradient(circle, rgba(157,140,255,0.5), transparent 70%)" }} />
      <div className="relative flex flex-wrap items-center gap-1.5">
        <div className="flex min-w-0 flex-1 basis-full items-center gap-2 rounded-xl bg-black/20 px-3 sm:basis-[320px]">
          <span className="st-mono text-[12px] text-[var(--st-ink-3)]">npm</span>
          <input
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addNpm()}
            placeholder="Any npm library for the Stage: chart.js, matter-js@0.20, three-globe…"
            className="h-10 min-w-0 flex-1 bg-transparent text-[14px] text-[var(--st-ink)] outline-none placeholder:text-[var(--st-ink-3)]"
          />
          <button className={btn} disabled={!spec.trim() || busy !== null} onClick={addNpm}>
            {busy === "npm" ? <Spinner /> : <Icon name="plus" size={14} />} Add library
          </button>
        </div>
        <button className={btn} disabled={busy !== null} onClick={() => file.current?.click()} title="A pack as a .zip (pack.json inside)">
          {busy === "zip" ? <Spinner /> : <Icon name="files" size={14} />} Upload a pack
        </button>
        <input
          ref={file}
          type="file"
          accept=".zip"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void run("zip", async () => `Added ${(await api.uploadPack(f)).name}.`);
          }}
        />
        <div className="flex min-w-0 max-w-full items-center gap-1.5 rounded-xl bg-black/20 pl-3">
          <input
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && folder.trim() && run("folder", async () => `Added ${(await api.addPackFolder(folder.trim())).name}.`)}
            placeholder="…or a pack folder on this computer"
            className="h-10 w-[230px] min-w-0 max-w-full bg-transparent text-[13px] text-[var(--st-ink)] outline-none placeholder:text-[var(--st-ink-3)]"
          />
          <button className={btn} disabled={!folder.trim() || busy !== null} onClick={() => run("folder", async () => `Added ${(await api.addPackFolder(folder.trim())).name}.`)}>
            {busy === "folder" ? <Spinner /> : <Icon name="plus" size={14} />} Add
          </button>
        </div>
      </div>
      {dir && (
        <div className="relative px-3 pb-1 pt-2 text-[11.5px] text-[var(--st-ink-3)]">
          Your packs live in <span className="st-mono break-all text-[var(--st-ink-2)]">{dir}</span>
        </div>
      )}
    </div>
  );
}

function Spinner() {
  return <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />;
}

function PackCard({ pack: p, onToggle, onRemove, onDocs }: { pack: PackInfo; onToggle: (on: boolean) => void; onRemove: () => void; onDocs: (lib: LibraryInfo) => void }) {
  const [confirm, setConfirm] = useState(false);
  const hue = agentHue({ name: p.id, avatar: {} });
  const off = !p.enabled;
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ type: "spring", stiffness: 420, damping: 34 }}
      className={`${glass} group relative flex flex-col overflow-hidden p-4`}
      style={{ opacity: off ? 0.62 : 1 }}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px" style={{ background: `linear-gradient(90deg, transparent, ${hsl(hue, 80, 65, 0.7)}, transparent)` }} />
      <div className="flex items-start gap-3">
        <span
          className="grid size-10 shrink-0 place-items-center rounded-xl text-[15px] font-semibold text-white"
          style={{ background: `linear-gradient(140deg, ${hsl(hue, 70, 58)}, ${hsl((hue + 50) % 360, 65, 38)})`, filter: off ? "grayscale(0.8)" : undefined }}
        >
          {p.name.replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "?"}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[15px] font-medium">{p.name}</span>
            <span className="st-mono shrink-0 text-[11px] text-[var(--st-ink-3)]">{p.version}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {p.kinds.map((k) => (
              <span key={k} className={chip} style={{ borderColor: "var(--st-edge-2)", color: "var(--st-ink-2)" }}>
                {KIND_LABEL[k]}
              </span>
            ))}
            {p.core ? (
              <span className={chip} style={{ borderColor: "rgba(76,201,240,0.4)", color: "var(--st-cyan)" }}>
                {p.libraries.some((l) => l.core) ? "Part of the Stage" : "Required"}
              </span>
            ) : p.bundled ? (
              <span className={chip} style={{ borderColor: "var(--st-edge)", color: "var(--st-ink-3)" }}>
                Built in
              </span>
            ) : (
              <span className={chip} style={{ borderColor: "rgba(157,140,255,0.45)", color: "var(--st-violet)" }}>
                Yours
              </span>
            )}
          </div>
        </div>
        <Switch on={p.enabled} locked={p.core} onChange={onToggle} />
      </div>

      {p.description && <p className="mt-3 line-clamp-3 text-[13px] leading-relaxed text-[var(--st-ink-2)]">{p.description}</p>}
      {p.error && <p className="st-mono mt-2 rounded-lg bg-[rgba(255,93,108,0.08)] px-2.5 py-1.5 text-[11.5px] text-[var(--st-red)]">{p.error}</p>}

      <div className="mt-3 flex flex-col gap-2">
        {p.libraries.map((lib) => (
          <div key={lib.id} className="flex flex-wrap items-center gap-1.5">
            {lib.imports
              .filter((i) => !i.startsWith("three/") || lib.id === "three")
              .slice(0, 5)
              .map((i) => (
                <code key={i} className="st-mono rounded-md bg-black/25 px-1.5 py-0.5 text-[11.5px] text-[var(--st-ink)]">
                  {i}
                </code>
              ))}
            {lib.requires.length > 0 && <span className="text-[11.5px] text-[var(--st-ink-3)]">uses {lib.requires.join(", ")}</span>}
            {lib.hasDocs && (
              <button onClick={() => onDocs(lib)} className="ml-auto text-[12px] text-[var(--st-violet)] hover:underline">
                Docs agents read →
              </button>
            )}
          </div>
        ))}
        {(p.assets.length > 0 || p.templates.length > 0 || p.tools.length > 0 || p.office.items + p.office.characters + p.office.themes > 0) && (
          <div className="flex flex-wrap gap-1.5 text-[12px] text-[var(--st-ink-3)]">
            {[
              p.office.items && `${p.office.items} furniture`,
              p.office.characters && `${p.office.characters} characters`,
              p.office.themes && `${p.office.themes} office themes`,
            ]
              .filter(Boolean)
              .join(" · ")}
            {p.assets.length > 0 && <span>{p.office.items + p.office.characters ? "· " : ""}{p.assets.length} 3D assets</span>}
            {p.templates.length > 0 && <span>· {p.templates.length} templates</span>}
            {p.tools.length > 0 && <span>· tools: {p.tools.join(", ")}</span>}
          </div>
        )}
      </div>

      {!p.bundled && (
        <div className="mt-auto flex items-center justify-end gap-2 pt-3">
          {confirm ? (
            <>
              <span className="text-[12px] text-[var(--st-ink-3)]">Delete its folder?</span>
              <button className="text-[12px] text-[var(--st-ink-3)] hover:text-[var(--st-ink)]" onClick={() => setConfirm(false)}>
                Keep
              </button>
              <button className="rounded-lg bg-[rgba(255,93,108,0.15)] px-2.5 py-1 text-[12px] text-[var(--st-red)]" onClick={onRemove}>
                Remove
              </button>
            </>
          ) : (
            <button className="text-[12px] text-[var(--st-ink-3)] opacity-0 transition-opacity hover:text-[var(--st-red)] group-hover:opacity-100" onClick={() => setConfirm(true)}>
              Remove
            </button>
          )}
        </div>
      )}
    </motion.div>
  );
}

function Switch({ on, locked, onChange }: { on: boolean; locked: boolean; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      disabled={locked}
      onClick={() => onChange(!on)}
      title={locked ? "Pantheon needs it: replace it with a pack of the same id instead" : on ? "Switch off" : "Switch on"}
      className="relative mt-1 h-[22px] w-[40px] shrink-0 rounded-full transition-colors disabled:cursor-not-allowed"
      style={{ background: on ? "var(--st-violet-2)" : "rgba(255,255,255,0.12)", opacity: locked ? 0.5 : 1 }}
    >
      <motion.span
        className="absolute top-[3px] size-4 rounded-full bg-white shadow"
        animate={{ left: on ? 21 : 3 }}
        transition={{ type: "spring", stiffness: 600, damping: 32 }}
      />
    </button>
  );
}

function DocsDrawer({ lib, onClose }: { lib: LibraryInfo | null; onClose: () => void }) {
  const [docs, setDocs] = useState<string>("");
  useEffect(() => {
    if (!lib) return;
    setDocs("");
    api.libraryDocs(lib.id).then((d) => setDocs(d.docs)).catch((e) => setDocs(`(couldn't load: ${(e as Error).message})`));
  }, [lib]);
  return (
    <AnimatePresence>
      {lib && (
        <>
          <motion.div className="fixed inset-0 z-40 bg-black/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 380, damping: 40 }}
            className="fixed inset-y-3 right-3 z-50 flex w-[min(620px,92vw)] flex-col rounded-2xl border border-[var(--st-edge-2)] bg-[var(--st-glass-hi)] shadow-2xl backdrop-blur-2xl"
          >
            <div className="flex items-center gap-3 border-b border-[var(--st-edge)] px-5 py-4">
              <div className="min-w-0 flex-1">
                <div className="text-[16px] font-medium">{lib.name}</div>
                <div className="text-[12px] text-[var(--st-ink-3)]">What an agent with this library reads (stage_docs)</div>
              </div>
              <button onClick={onClose} className="text-[var(--st-ink-3)] hover:text-[var(--st-ink)]">
                <Icon name="close" size={16} />
              </button>
            </div>
            <div className="studio-page min-h-0 flex-1 overflow-y-auto px-5 py-4 text-[13.5px]">
              {/* A README is Markdown; the shipped libraries' docs are a terse code-like reference. */}
              {docs ? <Markdown>{/^#|\n#/.test(docs) ? docs : "```\n" + docs + "\n```"}</Markdown> : <div className="text-[var(--st-ink-3)]">Loading…</div>}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
