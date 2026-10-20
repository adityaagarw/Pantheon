"use client";

/**
 * Where a new organization works (engine services/workspaces.py): a new folder, a folder on
 * this computer, or a cloned repository; with git, and a worktree and branch for each agent
 * who writes files, merged by their lead.
 */

import { inDesktopApp } from "@/lib/engine";
import { cx, Input, Toggle } from "./ui";
import { pickFolder } from "@/lib/folders";

export interface WorkspaceSpec {
  source: "new" | "folder" | "clone";
  path?: string;
  url?: string;
  git: boolean;
  worktrees: boolean;
}

export const DEFAULT_WORKSPACE: WorkspaceSpec = { source: "new", git: true, worktrees: true };

/** Whether the spec has what its source needs. */
export const workspaceReady = (s: WorkspaceSpec) =>
  s.source === "new" || (s.source === "folder" ? !!s.path?.trim() : !!s.url?.trim());

const SOURCES: { id: WorkspaceSpec["source"]; label: string; hint: string; icon: string }[] = [
  { id: "new", label: "A new folder", hint: "Made for this organization", icon: "✦" },
  { id: "folder", label: "A folder on this computer", hint: "Your project, where it is", icon: "▣" },
  { id: "clone", label: "Clone a repository", hint: "From GitHub or any git URL", icon: "⑂" },
];

export function WorkspaceSetup({ value, onChange }: { value: WorkspaceSpec; onChange: (v: WorkspaceSpec) => void }) {
  const set = (patch: Partial<WorkspaceSpec>) => onChange({ ...value, ...patch });
  const git = value.git || value.source === "clone";
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {SOURCES.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => set({ source: s.id })}
            className={cx(
              "flex items-start gap-2.5 rounded-lg border p-3 text-left transition-colors cursor-pointer",
              value.source === s.id ? "border-accent bg-accent/10" : "border-line hover:border-line-2",
            )}
          >
            <span className="mt-0.5 text-base text-accent">{s.icon}</span>
            <span>
              <span className="block text-sm font-medium">{s.label}</span>
              <span className="block text-xs text-ink-3">{s.hint}</span>
            </span>
          </button>
        ))}
      </div>

      {value.source === "folder" && (
        <div className="flex gap-2">
          <Input value={value.path ?? ""} onChange={(e) => set({ path: e.target.value })} placeholder={navigator.userAgent.includes("Windows") ? String.raw`C:\Users\you\projects\my-app` : "/Users/you/projects/my-app"} className="flex-1" />
          {inDesktopApp() && (
            <button
              type="button"
              className="shrink-0 rounded-md border border-line-2 px-3 text-sm text-ink-2 hover:text-ink cursor-pointer"
              onClick={() => void pickFolder("The organization's workspace").then((p) => p && set({ path: p }))}
            >
              Choose…
            </button>
          )}
        </div>
      )}
      {value.source === "clone" && (
        <Input value={value.url ?? ""} onChange={(e) => set({ url: e.target.value })} placeholder="https://github.com/you/repo.git" />
      )}

      <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg border border-line bg-panel-2/40 px-3 py-2.5">
        <Toggle
          checked={git}
          onChange={(v) => value.source !== "clone" && set({ git: v })}
          label={<span title={value.source === "clone" ? "A clone is a git repository" : undefined}>Use git</span>}
        />
        <span className={cx(!git && "pointer-events-none opacity-40")}>
          <Toggle checked={git && value.worktrees} onChange={(v) => set({ worktrees: v })} label="A worktree and branch for each agent" />
        </span>
      </div>
      <p className="text-xs leading-relaxed text-ink-3">
        {git && value.worktrees
          ? "Everyone who writes files works on their own branch in their own worktree, so nobody steps on anyone else's work. Leads work on the main branch and merge their team's branches when they're ready."
          : git
            ? "Everyone works in the same folder, with git keeping the history."
            : "Everyone works in the same folder."}
        {value.source === "folder" && git && " A folder that isn't a git repository yet becomes one, with what's in it as the first commit."}
      </p>
    </div>
  );
}
