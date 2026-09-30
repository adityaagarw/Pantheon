"use client";

/** Tools Zeus (or you) created without code. Grant them to agents on the Team tab. */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CustomTool } from "@/lib/types";
import { Badge, Button, Spinner } from "./ui";

const KIND_LABEL = { action: "action in the space", http: "web API call", prompt: "prompt skill" } as const;

export function CustomToolsPanel() {
  const [tools, setTools] = useState<CustomTool[] | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const load = () => api.customTools().then(setTools).catch(() => setTools([]));
  useEffect(() => {
    void load();
  }, []);
  if (!tools) return <Spinner />;
  if (!tools.length)
    return (
      <div className="p-4 text-sm text-ink-3">
        No custom tools yet. Ask Zeus for one, e.g. &ldquo;give the baristas a way to serve coffee&rdquo; or &ldquo;make a tool that looks up the weather&rdquo;.
      </div>
    );
  return (
    <div className="divide-y divide-line">
      {tools.map((t) => (
        <div key={t.name} className="p-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono font-medium">{t.name}</span>
            <Badge tone="accent">{KIND_LABEL[t.kind]}</Badge>
            <Badge tone={t.approval === "auto" ? "ok" : "warn"}>approval {t.approval}</Badge>
            <span className="ml-auto">
              {confirm === t.name ? (
                <span className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
                    Keep
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => api.deleteCustomTool(t.name).then(() => (setConfirm(null), void load()))}>
                    Delete
                  </Button>
                </span>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setConfirm(t.name)}>
                  ✕
                </Button>
              )}
            </span>
          </div>
          <div className="mt-1 text-ink-2">{t.description}</div>
          <pre className="mt-2 overflow-x-auto rounded bg-bg p-2 text-[11px] text-ink-3">{JSON.stringify(t.config, null, 1)}</pre>
          <div className="mt-1 text-[11px] text-ink-3">by {t.createdBy}</div>
        </div>
      ))}
    </div>
  );
}
