"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { api } from "@/lib/api";
import type { HostStatus } from "@/lib/types";

/** Pantheon Host's status, refreshed every few seconds. */
export function useHostStatus(): HostStatus | null {
  const [status, setStatus] = useState<HostStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .hostStatus()
        .then((s) => alive && setStatus(s))
        .catch(() => {});
    void load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  return status;
}

function Cmd({ children }: { children: string }) {
  return <code className="block overflow-x-auto whitespace-pre rounded-md bg-black/30 px-2.5 py-1.5 font-mono text-[12px] text-ink">{children}</code>;
}

const CAPABILITY: Record<string, string> = {
  kronos: "Kronos: files in allowed folders and Pantheon's code",
  recorder: "Shadow: records this computer when you pick “My computer”",
  folders: "Asks you on screen for folders Kronos may use",
};

/** Kronos's folders: add one (Pantheon Host asks you on your screen) or take one away. */
export function HostFolders({ allowed, compact = false }: { allowed: string[]; compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const run = async (p: Promise<{ message: string }>) => {
    setNote(null);
    try {
      setNote((await p).message);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2">
      {allowed.length ? (
        <ul className="space-y-1 text-xs text-ink-2">
          {allowed.map((a) => (
            <li key={a} className="group flex items-start gap-2">
              <span className="min-w-0 flex-1 break-all">{a}</span>
              <button className="shrink-0 text-ink-3 hover:text-bad cursor-pointer" onClick={() => void run(api.hostRemoveFolder(a))}>
                remove
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-xs text-ink-3">None yet. Kronos can only read Pantheon&apos;s own code.</div>
      )}
      <Button size="sm" disabled={busy} onClick={() => (setBusy(true), void run(api.hostPickFolder()))}>
        {busy ? "Choose the folder on your screen…" : "Add a folder…"}
      </Button>
      {!compact && (
        <div className="text-[11px] leading-relaxed text-ink-3">A folder picker opens on your computer; only you can choose, so nothing in Pantheon can widen Kronos&apos;s reach.</div>
      )}
      {note && <div className="text-xs text-ink-2">{note}</div>}
    </div>
  );
}

/** "This computer": whether Pantheon Host is running, and what it lets Pantheon do. */
export function HostStatusCard() {
  const status = useHostStatus();
  const info = status?.info;
  return (
    <Card className="space-y-4 p-5 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-semibold">Pantheon Host</span>
        {status && <Badge tone={status.connected ? "ok" : "warn"}>{status.connected ? `running on ${info?.host ?? "this computer"}` : "not running"}</Badge>}
      </div>
      <div className="text-ink-2">
        A small helper on your computer that does what the containers can&apos;t: Kronos&apos;s work with your files and Pantheon&apos;s code, and recording your
        computer for Shadow. It decides what it may touch, so nothing inside Pantheon can widen its reach.
      </div>
      {status && !status.connected && (
        <div className="space-y-2">
          <div className="text-ink-2">Start it from your Pantheon folder (needs only Python), and have it start whenever you log in:</div>
          <Cmd>{"python host/main.py install\npython host/main.py"}</Cmd>
        </div>
      )}
      {status?.connected && info && (
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">What it does</div>
            <ul className="space-y-0.5 text-xs text-ink-2">
              {(info.capabilities ?? []).map((c) => (
                <li key={c}>• {CAPABILITY[c] ?? c}</li>
              ))}
            </ul>
            {info.recording && (
              <div className="mt-2 flex items-center gap-2 text-xs text-bad">
                <span className="size-2 animate-pulse rounded-full bg-bad" /> Recording “{info.recording.title}”
              </div>
            )}
          </div>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">Folders Kronos may use</div>
            <HostFolders allowed={info.allowed ?? []} />
          </div>
        </div>
      )}
    </Card>
  );
}
