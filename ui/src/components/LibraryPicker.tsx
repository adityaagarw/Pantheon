"use client";

/**
 * Which Stage libraries an organization's (or one agent's) pages may import. Fewer
 * libraries keep agents focused: they read the docs only for what they have. Core
 * libraries (three.js, KaTeX) are always on, and a library brings what it needs.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LibraryInfo } from "@/lib/types";
import { cx } from "./ui";

interface Props {
  /** null: every library (an org) or the organization's choice (an agent). */
  value: string[] | null | undefined;
  onChange: (value: string[] | null) => void;
  /** An agent can fall back to its organization's choice, shown here. */
  inherit?: { label: string; value: string[] | null | undefined };
}

export function LibraryPicker({ value, onChange, inherit }: Props) {
  const [libs, setLibs] = useState<LibraryInfo[] | null>(null);
  useEffect(() => {
    api.packs().then((l) => setLibs(l.libraries)).catch(() => setLibs([]));
  }, []);
  if (!libs) return <div className="text-xs text-ink-3">Loading libraries…</div>;

  const extra = libs.filter((l) => !l.core);
  const core = libs.filter((l) => l.core);
  const chosen = value ?? null;
  const shown = chosen ?? (inherit ? inherit.value ?? null : null);
  const on = (id: string) => (shown === null ? true : shown.includes(id));
  const toggle = (id: string) => {
    const base = shown ?? extra.map((l) => l.id);
    onChange(base.includes(id) ? base.filter((x) => x !== id) : [...base, id]);
  };
  const needs = (l: LibraryInfo) => l.requires.filter((r) => !core.some((c) => c.id === r));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5 text-xs">
        <button
          type="button"
          onClick={() => onChange(null)}
          className={cx("rounded-full border px-3 py-1", chosen === null ? "border-accent bg-accent/15 text-ink" : "border-line text-ink-2 hover:text-ink")}
        >
          {inherit ? inherit.label : "Every library (and new ones)"}
        </button>
        <button
          type="button"
          onClick={() => onChange(shown ?? extra.map((l) => l.id))}
          className={cx("rounded-full border px-3 py-1", chosen !== null ? "border-accent bg-accent/15 text-ink" : "border-line text-ink-2 hover:text-ink")}
        >
          Only the ones I pick
        </button>
      </div>
      <div className={cx("flex flex-wrap gap-1.5", chosen === null && "opacity-60")}>
        {core.map((l) => (
          <span key={l.id} title={`${l.summary} (always on: the Stage needs it)`} className="rounded-md border border-line bg-panel-2 px-2 py-1 text-xs text-ink-3">
            🔒 {l.name}
          </span>
        ))}
        {extra.map((l) => (
          <button
            key={l.id}
            type="button"
            disabled={chosen === null}
            onClick={() => toggle(l.id)}
            title={`${l.summary}${needs(l).length ? ` (brings ${needs(l).join(", ")})` : ""}\nimport from ${l.imports.filter((i) => !i.startsWith("three/")).join(", ")}`}
            className={cx(
              "rounded-md border px-2 py-1 text-xs transition-colors",
              on(l.id) ? "border-accent/60 bg-accent/15 text-ink" : "border-line text-ink-3 hover:text-ink-2",
            )}
          >
            {on(l.id) ? "✓ " : ""}
            {l.name}
          </button>
        ))}
      </div>
      <div className="text-xs text-ink-3">
        Any npm library can be added on the{" "}
        <Link href="/packs" className="text-accent hover:underline">
          Packs
        </Link>{" "}
        page, or by asking Zeus.
      </div>
    </div>
  );
}
