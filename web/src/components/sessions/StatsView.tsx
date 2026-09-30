"use client";

import { useEffect, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { Card, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { duration, tokens, usd } from "@/lib/format";
import type { Stats } from "@/lib/types";
import { useOrg } from "@/store/org";

export function StatsView({ orgId }: { orgId: string }) {
  const [days, setDays] = useState(14);
  const [stats, setStats] = useState<Stats | null>(null);
  const agents = useOrg((s) => s.agents);
  useEffect(() => {
    api.stats(orgId, days).then(setStats).catch(() => {});
  }, [orgId, days]);
  if (!stats) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  const totalIn = stats.agents.reduce((a, b) => a + b.inputTokens, 0);
  const totalOut = stats.agents.reduce((a, b) => a + b.outputTokens, 0);
  const cost = stats.agents.reduce((a, b) => a + b.costUsd, 0);
  const calls = stats.agents.reduce((a, b) => a + b.calls, 0);
  const errors = stats.agents.reduce((a, b) => a + b.errors, 0);
  const maxDay = Math.max(1, ...stats.days.map((d) => d.inputTokens + d.outputTokens));
  const maxHeat = Math.max(1, ...stats.heatmap.map((h) => h.calls));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-5 px-6 py-6">
        <div className="flex items-center justify-between">
          <div className="text-sm text-ink-2">Usage over the last</div>
          <Select className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 7, 14, 30, 90].map((d) => (
              <option key={d} value={d}>
                {d} day{d > 1 ? "s" : ""}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Model calls" value={calls.toLocaleString()} />
          <Stat label="Input tokens" value={tokens(totalIn)} />
          <Stat label="Output tokens" value={tokens(totalOut)} />
          <Stat label="Cost" value={usd(cost)} />
          <Stat label="Failed calls" value={String(errors)} tone={errors ? "text-bad" : undefined} />
        </div>

        <Card className="p-4">
          <div className="mb-3 text-sm font-semibold">Tokens per day</div>
          {stats.days.length === 0 ? (
            <div className="text-sm text-ink-3">No activity in this period.</div>
          ) : (
            <div className="flex h-40 items-end gap-1.5">
              {stats.days.map((d) => (
                <div key={d.day} className="group flex flex-1 flex-col items-center gap-1" title={`${d.day}: ${tokens(d.inputTokens)} in, ${tokens(d.outputTokens)} out, ${usd(d.costUsd)}`}>
                  <div className="flex w-full flex-col justify-end" style={{ height: 130 }}>
                    <div className="w-full rounded-t bg-accent/80" style={{ height: `${(d.outputTokens / maxDay) * 100}%` }} />
                    <div className="w-full bg-accent/35" style={{ height: `${(d.inputTokens / maxDay) * 100}%` }} />
                  </div>
                  <div className="text-[10px] text-ink-3">{d.day.slice(5)}</div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="p-4">
            <div className="mb-3 text-sm font-semibold">By agent</div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-3">
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 text-right font-medium">Calls</th>
                  <th className="pb-2 text-right font-medium">Tokens</th>
                  <th className="pb-2 text-right font-medium">Cost</th>
                  <th className="pb-2 text-right font-medium">Avg latency</th>
                </tr>
              </thead>
              <tbody>
                {stats.agents
                  .sort((a, b) => b.inputTokens + b.outputTokens - (a.inputTokens + a.outputTokens))
                  .map((a) => (
                    <tr key={a.agentId} className="border-t border-line">
                      <td className="py-1.5">
                        <span className="flex items-center gap-2">
                          <AgentAvatar agent={agents[a.agentId]} size={18} /> {a.name}
                        </span>
                      </td>
                      <td className="text-right">{a.calls}</td>
                      <td className="text-right">{tokens(a.inputTokens + a.outputTokens)}</td>
                      <td className="text-right">{usd(a.costUsd)}</td>
                      <td className="text-right">{duration(Math.round(a.avgLatencyMs))}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </Card>
          <Card className="p-4">
            <div className="mb-3 text-sm font-semibold">Tools</div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-ink-3">
                  <th className="pb-2 font-medium">Tool</th>
                  <th className="pb-2 text-right font-medium">Calls</th>
                  <th className="pb-2 text-right font-medium">Failures</th>
                  <th className="pb-2 text-right font-medium">Avg time</th>
                </tr>
              </thead>
              <tbody>
                {stats.tools
                  .sort((a, b) => b.calls - a.calls)
                  .map((t) => (
                    <tr key={t.name} className="border-t border-line">
                      <td className="py-1.5 font-mono text-xs">{t.name}</td>
                      <td className="text-right">{t.calls}</td>
                      <td className={`text-right ${t.failures ? "text-bad" : ""}`}>{t.failures}</td>
                      <td className="text-right">{duration(Math.round(t.avgMs))}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
          <Card className="p-4">
            <div className="mb-3 text-sm font-semibold">Models</div>
            {stats.models.map((m) => (
              <div key={m.model} className="flex justify-between border-t border-line py-1.5 text-sm first:border-0">
                <span className="font-mono text-xs">{m.model}</span>
                <span className="text-ink-2">
                  {m.calls} calls · {tokens(m.tokens)} · {usd(m.costUsd)}
                </span>
              </div>
            ))}
          </Card>
          <Card className="p-4">
            <div className="mb-3 text-sm font-semibold">Activity heatmap (UTC)</div>
            <div className="grid gap-[3px]" style={{ gridTemplateColumns: "28px repeat(24, 1fr)" }}>
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day, dow) => (
                <div key={day} className="contents">
                  <div className="text-[10px] text-ink-3">{day}</div>
                  {Array.from({ length: 24 }, (_, h) => {
                    const c = stats.heatmap.find((x) => x.dow === dow && x.hour === h)?.calls ?? 0;
                    return <div key={h} className="aspect-square rounded-[2px]" style={{ background: c ? `rgba(139,124,255,${0.15 + 0.85 * (c / maxHeat)})` : "#171a22" }} title={`${day} ${h}:00 — ${c} calls`} />;
                  })}
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div className="text-xs text-ink-3">Sessions by status: {Object.entries(stats.turns).map(([k, v]) => `${k} ${v}`).join(" · ") || "none"}</div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-ink-3">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${tone ?? ""}`}>{value}</div>
    </Card>
  );
}
