/**
 * The org's workspace and worktrees (engine services/workspaces.py): the main checkout and
 * each agent's branch, what they changed, their files as a tree, and merging a branch
 * once it's ready.
 */
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";
import { api } from "@/lib/api";
import type { Worktree, WorktreeDiff, WorktreeListing } from "@/lib/types";
import { useOrg } from "@/store/org";
import { Icon } from "./icons";
import { agentHue, hsl } from "./look";

const ink3 = "text-[var(--st-ink-3)]";

export function WorktreesPane() {
  const { orgId } = useParams<{ orgId: string }>();
  const [listing, setListing] = useState<WorktreeListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>(".");
  const [tab, setTab] = useState<"files" | "changes">("files");

  const load = useCallback(() => {
    api.worktrees(orgId!).then(
      (l) => {
        setListing(l);
        setError(null);
      },
      (e: Error) => setError(e.message),
    );
  }, [orgId]);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  if (error) return <div className="p-6 text-sm text-[var(--st-red)]">{error}</div>;
  if (!listing) return <div className={`p-6 text-sm ${ink3}`}>Loading the workspace…</div>;
  const current = listing.worktrees.find((w) => w.path === selected) ?? listing.worktrees[0];

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[280px] shrink-0 flex-col border-r border-[var(--st-edge)]">
        <div className="border-b border-[var(--st-edge)] px-4 py-3">
          <div className="text-[13px] font-medium">Workspace</div>
          <div className={`st-mono mt-0.5 truncate text-[11px] ${ink3}`} title={listing.root}>
            {listing.root}
          </div>
          {listing.git.enabled ? (
            <div className={`mt-1.5 text-[11.5px] ${ink3}`}>
              git · base <span className="st-mono text-[var(--st-ink-2)]">{listing.git.base}</span>
              {listing.git.worktrees ? " · a branch per agent" : ""}
            </div>
          ) : (
            <TurnOnGit orgId={orgId!} onDone={load} />
          )}
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
          {listing.worktrees.map((w) => (
            <TreeCard key={w.path} w={w} base={listing.git.base ?? "main"} active={w.path === current?.path} onClick={() => setSelected(w.path)} />
          ))}
        </div>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        {current && (
          <>
            <div className="flex items-center gap-1 border-b border-[var(--st-edge)] px-3 py-2">
              {(["files", "changes"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className="relative rounded-lg px-3 py-1 text-[12.5px]"
                  style={{ color: tab === t ? "var(--st-ink)" : "var(--st-ink-3)" }}
                >
                  {tab === t && <motion.span layoutId="wt-tab" className="absolute inset-0 rounded-lg bg-white/[0.07]" />}
                  <span className="relative">{t === "files" ? "Files" : `Changes${current.changes.length || current.ahead ? ` · ${current.ahead + current.changes.length}` : ""}`}</span>
                </button>
              ))}
              <span className={`st-mono ml-auto truncate text-[11.5px] ${ink3}`}>{current.branch ?? "(no git)"}</span>
            </div>
            <div className="min-h-0 flex-1">
              {tab === "files" ? (
                <FilesView key={current.path} orgId={orgId!} root={current.path} />
              ) : (
                <ChangesView key={current.path} orgId={orgId!} w={current} enabled={listing.git.enabled} onMerged={load} />
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function TurnOnGit({ orgId, onDone }: { orgId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="mt-2">
      <button
        disabled={busy}
        onClick={() => {
          setBusy(true);
          api
            .setUpWorkspace(orgId, { source: "current", git: true, worktrees: true })
            .then((o) => {
              useOrg.getState().setOrg(o);
              onDone();
            }, (e: Error) => setError(e.message))
            .finally(() => setBusy(false));
        }}
        className="rounded-lg border border-[var(--st-edge-2)] px-2.5 py-1 text-[12px] text-[var(--st-ink-2)] hover:text-[var(--st-ink)] disabled:opacity-50"
        title="Make this folder a git repository and give each agent who writes files its own branch"
      >
        {busy ? "Setting up…" : "Turn on git and worktrees"}
      </button>
      {error && <div className="mt-1 text-[11.5px] text-[var(--st-red)]">{error}</div>}
    </div>
  );
}

function TreeCard({ w, base, active, onClick }: { w: Worktree; base: string; active: boolean; onClick: () => void }) {
  const agent = useOrg((s) => (w.agentId ? s.agents[w.agentId] : undefined));
  const hue = agent ? agentHue(agent) : 260;
  const name = w.main ? `Main · ${w.branch ?? base}` : (w.agentName ?? w.path);
  return (
    <button
      onClick={onClick}
      className="relative w-full rounded-xl px-3 py-2.5 text-left transition-colors"
      style={{ background: active ? "rgba(157,140,255,0.12)" : undefined, boxShadow: active ? "inset 0 0 0 1px rgba(157,140,255,0.3)" : undefined }}
    >
      <div className="flex items-center gap-2">
        <span
          className="grid size-6 shrink-0 place-items-center rounded-md text-[11px] font-semibold text-white"
          style={{ background: w.main ? "linear-gradient(140deg,#9d8cff,#4cc9f0)" : hsl(hue, 65, 50) }}
        >
          {w.main ? <Icon name="files" size={13} /> : name.charAt(0)}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px]">{name}</span>
        {w.changes.length > 0 && (
          <span className="st-mono rounded-full bg-[rgba(255,181,71,0.15)] px-1.5 text-[10.5px] text-[var(--st-amber)]" title="Uncommitted changes">
            {w.changes.length}
          </span>
        )}
      </div>
      <div className={`mt-1 flex items-center gap-2 pl-8 text-[11px] ${ink3}`}>
        {!w.main && w.branch && (
          <span className="st-mono" title={`Commits ahead of / behind ${base}`}>
            ↑{w.ahead} ↓{w.behind}
          </span>
        )}
        <span className="truncate">{w.last ? w.last.subject : w.branch ? "no commits" : ""}</span>
      </div>
    </button>
  );
}

interface Entry {
  name: string;
  type: "dir" | "file";
  size: number | null;
  path: string;
}

function FilesView({ orgId, root }: { orgId: string; root: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const base = root === "." ? "" : root;
  return (
    <div className="flex h-full min-h-0">
      <div className="w-[300px] shrink-0 overflow-y-auto border-r border-[var(--st-edge)] py-2">
        <Dir orgId={orgId} path={base} depth={0} hideWorktrees={root === "."} selected={open} onOpen={setOpen} />
      </div>
      <div className="min-w-0 flex-1">{open ? <FileView orgId={orgId} path={open} /> : <div className={`p-6 text-sm ${ink3}`}>Pick a file.</div>}</div>
    </div>
  );
}

function Dir({ orgId, path, depth, hideWorktrees, selected, onOpen }: { orgId: string; path: string; depth: number; hideWorktrees: boolean; selected: string | null; onOpen: (p: string) => void }) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => {
    api.files(orgId, path).then((r) => setEntries((r.entries ?? []) as Entry[]), () => setEntries([]));
  }, [orgId, path]);
  if (!entries) return <div className={`py-1 text-[12px] ${ink3}`} style={{ paddingLeft: 14 + depth * 14 }}>…</div>;
  const shown = entries.filter((e) => !(hideWorktrees && depth === 0 && e.name === ".pantheon"));
  if (!shown.length && depth === 0) return <div className={`px-4 py-2 text-[12px] ${ink3}`}>Empty.</div>;
  return (
    <>
      {shown.map((e) => (
        <div key={e.path}>
          <button
            onClick={() => (e.type === "dir" ? setExpanded((x) => ({ ...x, [e.path]: !x[e.path] })) : onOpen(e.path))}
            className="flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[12.5px] hover:bg-white/[0.04]"
            style={{ paddingLeft: 12 + depth * 14, background: selected === e.path ? "rgba(157,140,255,0.12)" : undefined }}
          >
            <span className={`w-3 text-[10px] ${ink3}`}>{e.type === "dir" ? (expanded[e.path] ? "▾" : "▸") : ""}</span>
            <span className={e.type === "dir" ? "text-[var(--st-ink)]" : "text-[var(--st-ink-2)]"}>{e.name}</span>
          </button>
          {e.type === "dir" && expanded[e.path] && <Dir orgId={orgId} path={e.path} depth={depth + 1} hideWorktrees={hideWorktrees} selected={selected} onOpen={onOpen} />}
        </div>
      ))}
    </>
  );
}

const IMAGE = /\.(png|jpe?g|gif|webp|svg)$/i;
const MAX_TEXT = 400_000;

function FileView({ orgId, path }: { orgId: string; path: string }) {
  const [text, setText] = useState<string | null>(null);
  const url = api.rawFileUrl(orgId, path);
  useEffect(() => {
    if (IMAGE.test(path)) return;
    setText(null);
    fetch(url)
      .then((r) => r.blob())
      .then(async (b) => setText(b.size > MAX_TEXT ? `(${Math.round(b.size / 1024)} KB: too large to show here)` : await b.text()))
      .catch((e: Error) => setText(`(couldn't read it: ${e.message})`));
  }, [url, path]);
  return (
    <div className="flex h-full flex-col">
      <div className={`st-mono border-b border-[var(--st-edge)] px-4 py-2 text-[11.5px] ${ink3}`}>{path}</div>
      <div className="min-h-0 flex-1 overflow-auto">
        {IMAGE.test(path) ? (
          <img src={url} alt={path} className="max-w-full p-4" />
        ) : (
          <pre className="st-mono whitespace-pre p-4 text-[12px] leading-[1.55] text-[var(--st-ink-2)]">{text ?? "…"}</pre>
        )}
      </div>
    </div>
  );
}

function ChangesView({ orgId, w, enabled, onMerged }: { orgId: string; w: Worktree; enabled: boolean; onMerged: () => void }) {
  const [diff, setDiff] = useState<WorktreeDiff | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const key = `${w.path}:${w.last?.hash}:${w.changes.length}`;
  useEffect(() => {
    if (!enabled) return;
    api.worktreeDiff(orgId, w.path).then(setDiff, () => setDiff(null));
  }, [orgId, w.path, enabled, key]);
  if (!enabled) return <div className={`p-6 text-sm ${ink3}`}>This organization doesn&apos;t use git, so there are no branches or changes to show.</div>;
  if (!diff) return <div className={`p-6 text-sm ${ink3}`}>…</div>;

  const merge = () => {
    setBusy(true);
    setResult(null);
    api
      .mergeWorktree(orgId, w.path)
      .then(
        (r) => setResult(r.merged ? { ok: true, text: `Merged ${r.branch} into ${diff.base}.` } : { ok: false, text: `It conflicts with ${diff.base} in ${r.conflicts.join(", ")}. Nothing changed; ask ${w.agentName ?? "them"} to bring in ${diff.base} (git_update) and resolve it.` }),
        (e: Error) => setResult({ ok: false, text: e.message }),
      )
      .finally(() => {
        setBusy(false);
        onMerged();
      });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--st-edge)] px-4 py-3">
        <div className="min-w-0 flex-1 text-[13px]">
          {w.main ? (
            <>The main checkout, on {diff.base}.</>
          ) : (
            <>
              <span className="st-mono">{diff.branch}</span> is {w.ahead} commit{w.ahead === 1 ? "" : "s"} ahead of <span className="st-mono">{diff.base}</span>
              {w.behind ? `, ${w.behind} behind` : ""}.
            </>
          )}
        </div>
        {!w.main && w.ahead > 0 && (
          <button
            onClick={merge}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 text-[12.5px] text-white disabled:opacity-50"
            style={{ background: "linear-gradient(140deg,#9d8cff,#6c5cff)" }}
            title={`Merge ${diff.branch} into ${diff.base}`}
          >
            {busy ? "Merging…" : `Merge into ${diff.base}`}
          </button>
        )}
      </div>
      <AnimatePresence>
        {result && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="mx-4 mt-3 rounded-lg px-3 py-2 text-[12.5px]"
            style={{ background: result.ok ? "rgba(61,220,151,0.1)" : "rgba(255,93,108,0.1)", color: result.ok ? "var(--st-green)" : "var(--st-red)" }}>
            {result.text}
          </motion.div>
        )}
      </AnimatePresence>
      {diff.stat && <Section title="Committed on this branch"><pre className="st-mono px-4 text-[12px] text-[var(--st-ink-2)]">{diff.stat}</pre><Patch text={diff.committed} /></Section>}
      {(diff.uncommitted || diff.untracked.length > 0) && (
        <Section title="Not committed yet">
          {diff.untracked.length > 0 && <div className={`px-4 pb-2 text-[12px] ${ink3}`}>New files: {diff.untracked.join(", ")}</div>}
          <Patch text={diff.uncommitted} />
        </Section>
      )}
      {!diff.stat && !diff.uncommitted && !diff.untracked.length && <div className={`p-6 text-sm ${ink3}`}>No changes.</div>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="pt-3">
      <div className={`px-4 pb-2 text-[10.5px] uppercase tracking-[0.12em] ${ink3}`}>{title}</div>
      {children}
    </div>
  );
}

function Patch({ text }: { text: string }) {
  const lines = useMemo(() => text.split("\n"), [text]);
  if (!text.trim()) return null;
  return (
    <pre className="st-mono overflow-x-auto pb-3 text-[12px] leading-[1.5]">
      {lines.map((l, i) => {
        const color = l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff --git") ? "var(--st-ink)" : l.startsWith("+") ? "var(--st-green)" : l.startsWith("-") ? "var(--st-red)" : l.startsWith("@@") ? "var(--st-cyan)" : "var(--st-ink-3)";
        const bg = l.startsWith("+") && !l.startsWith("+++") ? "rgba(61,220,151,0.06)" : l.startsWith("-") && !l.startsWith("---") ? "rgba(255,93,108,0.06)" : l.startsWith("diff --git") ? "rgba(255,255,255,0.04)" : undefined;
        return (
          <div key={i} className="px-4" style={{ color, background: bg, fontWeight: l.startsWith("diff --git") ? 600 : undefined }}>
            {l || " "}
          </div>
        );
      })}
    </pre>
  );
}
