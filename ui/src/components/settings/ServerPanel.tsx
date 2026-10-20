import { useEffect, useState } from "react";
import { Badge, Button, Card, ErrorNote, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { ServerStatus } from "@/lib/types";

/** Settings → Server: Pantheon in a browser, on this computer or others on the network. */
export function ServerPanel() {
  const [st, setSt] = useState<ServerStatus | null>(null);
  const [port, setPort] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    api
      .server()
      .then((s) => (setSt(s), setPort(String(s.port))))
      .catch((e) => setError((e as Error).message));
  }, []);
  const apply = async (b: { enabled?: boolean; port?: number; newAccessCode?: boolean }) => {
    setError(null);
    try {
      const s = await api.setServer(b);
      setSt(s);
      setPort(String(s.port));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  if (!st) return error ? <ErrorNote error={error} /> : null;
  const portChanged = port !== String(st.port);
  return (
    <Card className="space-y-4 p-5 text-sm">
      <div className="flex items-center gap-3">
        <span className="font-semibold">Open Pantheon in a browser</span>
        {st.enabled && <Badge tone={st.running ? "ok" : "bad"}>{st.running ? `on port ${st.port}` : "not running"}</Badge>}
        <div className="ml-auto">
          <Button variant={st.enabled ? "danger" : "primary"} onClick={() => void apply({ enabled: !st.enabled })}>
            {st.enabled ? "Stop server" : "Start server"}
          </Button>
        </div>
      </div>
      <div className="text-ink-2">
        Pantheon also listens on a port of your choosing, for a browser on this computer or on other devices on your network. Other devices need the access code.
      </div>
      <Field label="Port">
        <div className="flex gap-2">
          <Input className="w-32" inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} />
          {portChanged && (
            <Button size="sm" onClick={() => void apply({ port: Number(port) })}>
              Use port {port}
            </Button>
          )}
        </div>
      </Field>
      {st.error && <div className="text-xs text-bad">{st.error}</div>}
      {st.enabled && st.running && (
        <>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">Addresses</div>
            <ul className="space-y-0.5 font-mono text-xs">
              {st.urls.map((u) => (
                <li key={u}>
                  <a className="text-accent hover:underline" href={u} target="_blank" rel="noreferrer">
                    {u}
                  </a>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-3">Access code</div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-panel-2 px-2.5 py-1 font-mono">{shown ? st.accessCode : "••••-••••-••••-••••"}</span>
              <Button size="sm" variant="ghost" onClick={() => setShown(!shown)}>
                {shown ? "Hide" : "Show"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void apply({ newAccessCode: true })}>
                New code
              </Button>
            </div>
            <div className="mt-1 text-[11px] text-ink-3">A new code signs out every other device. Anyone with the code can use Pantheon as you, so share it only with yourself.</div>
          </div>
          <div className="text-[11px] leading-relaxed text-ink-3">
            The connection is plain HTTP: use it on networks you trust. Talking by voice from another device needs HTTPS (put Pantheon behind a reverse proxy).
          </div>
        </>
      )}
      <ErrorNote error={error} />
      <Headless />
    </Card>
  );
}

const COMMANDS: Record<string, string> = {
  windows: "pantheon-serve",
  linux: "pantheon-serve",
  mac: '"/Applications/Pantheon Studio.app/Contents/Resources/runtime/pantheon-serve"',
};

/** Pantheon as a server without this window: the command to run, for a computer that
 * stays on (it shares your data, so close the app first). */
function Headless() {
  const ua = navigator.userAgent;
  const os = /Windows/.test(ua) ? "windows" : /Mac OS X|Macintosh/.test(ua) ? "mac" : "linux";
  const command = COMMANDS[os];
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-2 border-t border-line pt-4">
      <div className="font-medium">Without the app</div>
      <div className="text-xs text-ink-2">
        To keep Pantheon serving on a computer that stays on, close this app and run this in a terminal. It listens on port 8730 (add{" "}
        <span className="font-mono">--port 9000</span> for another) and prints the addresses and the access code.
      </div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-md bg-panel-2 px-2.5 py-1.5 font-mono text-xs">{command}</code>
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            void navigator.clipboard.writeText(command).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            })
          }
        >
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}
