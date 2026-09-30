"use client";

/** Runtime 3D assets and installed plugins. */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AssetInfo, PluginInfo } from "@/lib/types";
import { useAssets } from "@/office/assets";
import { Badge, Button, Spinner } from "./ui";

export function AssetsPanel() {
  const [assets, setAssets] = useState<AssetInfo[] | null>(null);
  const [plugins, setPlugins] = useState<{ dir: string; plugins: PluginInfo[] } | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const load = () => {
    api.assets().then(setAssets).catch(() => setAssets([]));
    api.plugins().then(setPlugins).catch(() => setPlugins({ dir: "", plugins: [] }));
  };
  useEffect(load, []);
  if (!assets || !plugins) return <Spinner />;
  const own = assets.filter((a) => a.key.startsWith("asset:"));

  return (
    <div className="space-y-5 p-4 text-sm">
      <section>
        <div className="mb-2 flex items-center justify-between">
          <div className="text-xs font-medium uppercase tracking-wider text-ink-3">Plugins</div>
          <Button size="sm" variant="ghost" onClick={load}>
            Refresh
          </Button>
        </div>
        {plugins.plugins.length === 0 ? (
          <div className="text-ink-3">
            No plugins installed. Drop a plugin folder into <code className="rounded bg-panel-2 px-1">{plugins.dir || "plugins/"}</code> and restart the backend.
          </div>
        ) : (
          <div className="space-y-2">
            {plugins.plugins.map((p) => (
              <div key={p.id} className="rounded-lg border border-line p-3">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.name}</span>
                  <span className="text-xs text-ink-3">v{p.version}</span>
                  {p.error ? <Badge tone="bad">failed</Badge> : <Badge tone="ok">loaded</Badge>}
                </div>
                {p.description && <div className="mt-1 text-xs text-ink-2">{p.description}</div>}
                {p.error ? (
                  <div className="mt-1 font-mono text-xs text-bad">{p.error}</div>
                ) : (
                  <div className="mt-1.5 flex flex-wrap gap-1 text-[11px] text-ink-3">
                    {p.templates.length > 0 && <Badge>{p.templates.length} templates</Badge>}
                    {p.assets.length > 0 && <Badge>{p.assets.length} assets</Badge>}
                    {p.tools.map((t) => (
                      <Badge key={t} tone="accent">
                        {t}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
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
