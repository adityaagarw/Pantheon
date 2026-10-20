"use client";

/** Runtime 3D assets, and which packs add more. */

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AssetInfo, PackInfo } from "@/lib/types";
import { useAssets } from "@/office/assets";
import { Badge, Button, Spinner } from "./ui";

export function AssetsPanel() {
  const [assets, setAssets] = useState<AssetInfo[] | null>(null);
  const [packs, setPacks] = useState<PackInfo[] | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const load = () => {
    api.assets().then(setAssets).catch(() => setAssets([]));
    api.packs().then((l) => setPacks(l.packs)).catch(() => setPacks([]));
  };
  useEffect(load, []);
  if (!assets || !packs) return <Spinner />;
  const withAssets = packs.filter((p) => p.enabled && p.assets.length > 0);
  const own = assets.filter((a) => a.key.startsWith("asset:"));

  return (
    <div className="space-y-5 p-4 text-sm">
      <section>
        <div className="mb-2 flex items-center justify-between">
          <div className="text-xs font-medium uppercase tracking-wider text-ink-3">Packs</div>
          <Button size="sm" variant="ghost" onClick={load}>
            Refresh
          </Button>
        </div>
        <div className="text-ink-3">
          {withAssets.length
            ? `Assets also come from ${withAssets.map((p) => p.name).join(", ")}. `
            : ""}
          Add, switch off or remove packs (assets, libraries, templates, plugins) on the{" "}
          <Link href="/packs" className="text-accent hover:underline">
            Packs
          </Link>{" "}
          page.
        </div>
      </section>

      <section>
        <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-3">Assets</div>
        {assets.length === 0 ? (
          <div className="text-ink-3">Ask Zeus to build a prop from shapes, or to find a free 3D model online and import it.</div>
        ) : (
          <div className="divide-y divide-line rounded-lg border border-line">
            {assets.map((a) => (
              <div key={a.key} className="flex items-start gap-3 p-2.5">
                <span className="mt-0.5 text-lg">{a.kind === "procedural" ? "◆" : "▣"}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium">{a.label}</span>
                    <Badge>{a.category}</Badge>
                    {a.carryable && <Badge tone="info">carryable</Badge>}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-ink-3" title={a.sourceUrl || a.source}>
                    {a.size.map((v) => v.toFixed(2)).join(" × ")} m · {a.source}
                    {a.license && ` · ${a.license}`}
                  </div>
                </div>
                {own.includes(a) &&
                  (confirm === a.id ? (
                    <div className="flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
                        Keep
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={async () => {
                          await api.deleteAsset(a.id);
                          setConfirm(null);
                          load();
                          void useAssets.getState().load(true);
                        }}
                      >
                        Delete
                      </Button>
                    </div>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => setConfirm(a.id)}>
                      ✕
                    </Button>
                  ))}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
