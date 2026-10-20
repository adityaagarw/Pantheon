"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card, Input, Toggle, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { inDesktopApp } from "@/lib/engine";
import { pickFolder } from "@/lib/folders";
import type { ComputerStatus, HostStatus, SandboxStatus } from "@/lib/types";

function usePolled<T>(load: () => Promise<T>, ms: number): [T | null, (v: T) => void] {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    const run = () =>
      load()
        .then((v) => alive && setValue(v))
        .catch(() => {});
    void run();
    const t = setInterval(run, ms);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [load, ms]);
  return [value, setValue];
}

/** This computer's status (the source Kronos may change, its folders), refreshed now and then. */
export function useHostStatus(): HostStatus | null {
  return usePolled(api.hostStatus, 10000)[0];
}

/** The folders Kronos may use: add one (with the system's folder picker) or take one away. */
export function HostFolders({ allowed, compact = false }: { allowed: string[]; compact?: boolean }) {
  const [changed, setChanged] = useState<string[] | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const list = changed ?? allowed;
  const apply = async (p: Promise<HostStatus>) => {
    setError(null);
    try {
      setChanged((await p).info?.allowed ?? []);
      setTyped("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const add = async () => {
    const dir = await pickFolder("A folder Kronos may use");
    if (dir) await apply(api.hostAddFolder(dir));
  };
  return (
    <div className="space-y-2">
      {list.length ? (
        <ul className="space-y-1 text-xs text-ink-2">
          {list.map((a) => (
            <li key={a} className="flex items-start gap-2">
              <span className="min-w-0 flex-1 break-all font-mono">{a}</span>
              <button className="shrink-0 cursor-pointer text-ink-3 hover:text-bad" onClick={() => void apply(api.hostRemoveFolder(a))}>
                remove
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-xs text-ink-3">None yet.</div>
      )}
      {inDesktopApp() ? (
        <Button size="sm" onClick={() => void add()}>
          Add a folder…
        </Button>
      ) : (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (typed.trim()) void apply(api.hostAddFolder(typed.trim()));
          }}
        >
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="A folder's full path on this computer" className="text-xs" />
          <Button size="sm" type="submit" disabled={!typed.trim()}>
            Add
          </Button>
        </form>
      )}
      {!compact && <div className="text-[11px] leading-relaxed text-ink-3">Kronos reads, writes and runs commands only in these folders (and in Pantheon&apos;s own source, when it runs from one).</div>}
      {error && <div className="text-xs text-bad">{error}</div>}
    </div>
  );
}

/** Settings → This computer: which computer agents use, and the folders Kronos may use. */
export function HostStatusCard() {
  const [computer, setComputer] = usePolled(api.computer, 4000);
  const status = useHostStatus();
  const [error, setError] = useState<string | null>(null);
  const choose = async (backend: string) => {
    setError(null);
    try {
      setComputer(await api.setComputer(backend));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const options = computer ? [{ id: "off", label: "Off", description: "Agents can't use a computer." }, ...computer.backends] : [];
  return (
    <div className="space-y-5">
      <StartAtLogin />
      <Card className="space-y-4 p-5 text-sm">
        <div className="flex items-center gap-2">
          <span className="font-semibold">Computer use</span>
          {computer?.paused && <Badge tone="warn">paused: {computer.paused}</Badge>}
        </div>
        <div className="text-ink-2">The computer agents see and use. Each agent gets the tools that work with it: computer_* (look and click) and desktop_* (work in apps).</div>
        <div className="grid gap-2">
          {options.map((b) => (
            <label
              key={b.id}
              className={cx(
                "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5",
                computer?.backend === b.id ? "border-accent bg-accent/10" : "border-line hover:bg-panel-2",
              )}
            >
              <input type="radio" name="computer" className="mt-1" checked={computer?.backend === b.id} onChange={() => void choose(b.id)} />
              <span>
                <span className="font-medium text-ink">{b.label}</span>
                <span className="block text-xs text-ink-2">{b.description}</span>
              </span>
            </label>
          ))}
        </div>
        {computer && computer.backend !== "off" && (
          <div className="flex items-center gap-3">
            {computer.paused ? (
              <Button size="sm" onClick={() => void api.computerResume().then(setComputer)}>
                Let agents use it again
              </Button>
            ) : (
              <Button size="sm" variant="danger" onClick={() => void api.computerStop().then(setComputer)}>
                Stop now
              </Button>
            )}
            <span className="text-xs text-ink-3">{computer.inUseBy ? `In use by ${computer.inUseBy}.` : "Nobody is using it."}</span>
          </div>
        )}
        {computer?.backend === "driver" && /Mac OS X|Macintosh/.test(navigator.userAgent) && <MacPermissions />}
        {computer?.backend === "cua" && <SandboxSetup />}
        {error && <div className="text-xs text-bad">{error}</div>}
      </Card>

      <WebBrowsing showSandbox={computer?.backend !== "cua"} />

      <Card className="space-y-3 p-5 text-sm">
        <div className="flex items-center gap-2">
          <span className="font-semibold">Folders Kronos may use</span>
          {status?.info?.host && <Badge>{status.info.host}</Badge>}
        </div>
        <div className="text-ink-2">
          Kronos, Pantheon&apos;s engineer, works on this computer: it changes Pantheon&apos;s own code
          {status?.info?.repo ? (
            <>
              {" "}
              (in <span className="font-mono text-xs">{status.info.repo}</span>)
            </>
          ) : (
            " (when Pantheon runs from its source)"
          )}{" "}
          and works with your files in the folders below.
        </div>
        {status?.info && <HostFolders allowed={status.info.allowed ?? []} />}
      </Card>
    </div>
  );
}

const WHERE: { id: "auto" | "builtin" | "sandbox"; label: string; description: string }[] = [
  {
    id: "auto",
    label: "Automatic",
    description: "The built-in browser, fast and unseen. A site that turns it away (a bot check, “unusual traffic”) opens in the sandbox desktop's browser instead.",
  },
  { id: "builtin", label: "Built-in browser", description: "Always the built-in headless browser. Some sites block it." },
  {
    id: "sandbox",
    label: "Sandbox desktop",
    description: "Always a real, visible Chromium on the sandbox desktop: you watch it on the Stage, can step in for a human check, and sign in to sites once for your agents.",
  },
];

/** Settings → This computer: where agents browse the web (app/browser/sandboxed.py). */
function WebBrowsing({ showSandbox }: { showSandbox: boolean }) {
  const [st, setSt] = useState<{ where: string; sandbox: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.browserWhere().then(setSt).catch(() => {});
  }, []);
  const choose = async (where: string) => {
    setError(null);
    try {
      setSt(await api.setBrowserWhere(where));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  if (!st) return null;
  return (
    <Card className="space-y-4 p-5 text-sm">
      <div className="font-semibold">Web browsing</div>
      <div className="text-ink-2">Where agents&apos; browser_* tools open pages.</div>
      <div className="grid gap-2">
        {WHERE.map((w) => (
          <label
            key={w.id}
            className={cx("flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5", st.where === w.id ? "border-accent bg-accent/10" : "border-line hover:bg-panel-2")}
          >
            <input type="radio" name="browser-where" className="mt-1" checked={st.where === w.id} onChange={() => void choose(w.id)} />
            <span>
              <span className="font-medium text-ink">{w.label}</span>
              <span className="block text-xs text-ink-2">{w.description}</span>
            </span>
          </label>
        ))}
      </div>
      {st.where !== "builtin" && !st.sandbox && (
        <div className="text-xs text-warn">The sandbox desktop isn&apos;t set up yet{st.where === "auto" ? ", so agents stay in the built-in browser" : ""}. Set it up below.</div>
      )}
      {st.where !== "builtin" && showSandbox && <SandboxSetup />}
      {error && <div className="text-xs text-bad">{error}</div>}
    </Card>
  );
}

/** The sandbox desktop: Docker → download → create → run, one step at a time. */
export function SandboxSetup() {
  const [st, setSt] = useState<SandboxStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const s = await api.sandbox();
        if (!alive) return;
        setSt(s);
        timer = setTimeout(tick, s.setup.running || s.docker !== "running" || !s.healthy ? 2000 : 10000);
      } catch {
        timer = setTimeout(tick, 4000);
      }
    };
    void tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  const act = async (fn: () => Promise<unknown>) => {
    setNote(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "docker" in r) setSt(r as SandboxStatus);
      if (r && typeof r === "object" && "message" in r) setNote(String((r as { message: string }).message));
    } catch (e) {
      setNote((e as Error).message);
    }
  };
  if (!st) return <div className="text-xs text-ink-3">Checking Docker…</div>;
  const box = "space-y-2 rounded-lg border border-line p-3 text-xs text-ink-2";
  let body;
  if (st.docker === "missing") {
    body = (
      <>
        <div>The sandbox runs in Docker, which isn&apos;t installed on this computer.</div>
        <a className="text-accent hover:underline" href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noreferrer">
          Get Docker Desktop
        </a>
        <span className="text-ink-3"> (free for personal use), then come back here.</span>
      </>
    );
  } else if (st.docker === "stopped") {
    body = (
      <>
        <div>Docker is installed but not running.</div>
        <Button size="sm" onClick={() => void act(api.dockerStart)}>
          Start Docker
        </Button>
      </>
    );
  } else if (st.setup.running) {
    body = (
      <>
        <div>{st.setup.step}…</div>
        <div className="h-1.5 overflow-hidden rounded-full bg-panel-2">
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.round(st.setup.progress * 100)}%` }} />
        </div>
      </>
    );
  } else if (st.container === "none") {
    body = (
      <>
        <div>
          Pantheon sets up a Linux desktop for agents ({st.image ? "already downloaded" : "a download of about 1.8 GB"}). It runs on this computer only, behind its own password.
        </div>
        <Button size="sm" variant="primary" onClick={() => void act(api.sandboxSetup)}>
          Set up the sandbox
        </Button>
      </>
    );
  } else {
    body = (
      <>
        <div className="flex items-center gap-2">
          <Badge tone={st.healthy ? "ok" : "warn"}>{st.healthy ? "running" : st.container === "running" ? "starting" : "stopped"}</Badge>
          <span className="text-ink-3">Starts by itself when an agent needs it.</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {st.container === "running" ? (
            <Button size="sm" variant="ghost" onClick={() => void act(api.sandboxStop)}>
              Stop the sandbox
            </Button>
          ) : (
            <Button size="sm" onClick={() => void act(api.sandboxStart)}>
              Start the sandbox
            </Button>
          )}
          {st.view && st.healthy && (
            <a className="inline-flex items-center rounded-md border border-line px-2.5 py-1 text-xs hover:bg-panel-2" href={api.sandboxDesktopUrl()} target="_blank" rel="noreferrer">
              Open the desktop
            </a>
          )}
        </div>
        {st.view && <div className="text-ink-3">You can also see and use the desktop on any org&apos;s Stage → Computer.</div>}
      </>
    );
  }
  return (
    <div className={box}>
      <div className="font-medium text-ink">Sandbox desktop</div>
      {body}
      {st.setup.error && <div className="text-bad">{st.setup.error}</div>}
      {note && <div>{note}</div>}
    </div>
  );
}

/** macOS asks once for Accessibility and Screen Recording, for cua-driver. */
function MacPermissions() {
  const [note, setNote] = useState<string | null>(null);
  return (
    <div className="space-y-1 rounded-md border border-line p-3 text-xs text-ink-2">
      <div>macOS needs your permission once: Accessibility and Screen Recording, for “CuaDriver”.</div>
      <Button size="sm" onClick={() => void api.computerPermissions().then((r) => setNote(r.ok ? "Done. If macOS asked you to restart something, do that." : r.message))}>
        Grant permissions…
      </Button>
      {note && <div className="whitespace-pre-wrap">{note}</div>}
    </div>
  );
}

/** Start Pantheon when the user signs in to the computer (the desktop app only). */
function StartAtLogin() {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => {
    if (!inDesktopApp()) return;
    void import("@tauri-apps/plugin-autostart")
      .then((m) => m.isEnabled())
      .then(setOn)
      .catch(() => setOn(null));
  }, []);
  if (on === null) return null;
  const change = async (value: boolean) => {
    const m = await import("@tauri-apps/plugin-autostart");
    await (value ? m.enable() : m.disable());
    setOn(await m.isEnabled());
  };
  return (
    <Card className="flex items-center gap-3 p-5 text-sm">
      <div className="min-w-0 flex-1">
        <div className="font-semibold">Start Pantheon when I sign in</div>
        <div className="text-xs text-ink-2">So your agents keep working, and the server keeps serving, after a restart.</div>
      </div>
      <Toggle checked={on} onChange={(v) => void change(v)} />
    </Card>
  );
}

/** "Ada is using your computer · Stop", across the app, while an agent drives this computer. */
export function ComputerBanner() {
  const [computer, setComputer] = usePolled<ComputerStatus>(api.computer, 3000);
  if (!computer || !["native", "driver"].includes(computer.backend) || !computer.active || computer.paused) return null;
  const takesOver = computer.backend === "native";
  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-accent/40 bg-accent/15 px-4 py-1.5 text-sm">
      <span className="size-2 animate-pulse rounded-full bg-accent" />
      <span>
        <span className="font-medium">{computer.active.agent}</span> {takesOver ? "is using your computer" : "is working in your apps"}
      </span>
      {takesOver && <span className="hidden text-xs text-ink-3 sm:inline">move the mouse to the top-left corner to take it back</span>}
      <Button size="sm" variant="danger" className="ml-auto" onClick={() => void api.computerStop().then(setComputer)}>
        Stop
      </Button>
    </div>
  );
}
