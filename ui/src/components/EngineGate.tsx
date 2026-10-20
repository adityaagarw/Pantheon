import { useEffect, useState, type ReactNode } from "react";
import { apiUrl, inDesktopApp } from "@/lib/engine";

/**
 * Shows the app once the engine answers. The desktop app opens its window while the
 * engine is still starting (a first start also creates the database), so this waits
 * for it instead of letting every panel fail its first request.
 */
export function EngineGate({ children }: { children: ReactNode }) {
  const [up, setUp] = useState(false);
  const [waited, setWaited] = useState(0);

  useEffect(() => {
    let stop = false;
    const started = Date.now();
    (async () => {
      while (!stop) {
        try {
          const r = await fetch(apiUrl("/runtime/limits"), { cache: "no-store" });
          if (r.ok) {
            setUp(true);
            return;
          }
        } catch {
          // not listening yet
        }
        setWaited(Math.round((Date.now() - started) / 1000));
        await new Promise((r) => setTimeout(r, 400));
      }
    })();
    return () => {
      stop = true;
    };
  }, []);

  if (up) return <>{children}</>;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 bg-bg text-ink">
      <div className="h-10 w-10 animate-spin rounded-full border-2 border-line border-t-accent" />
      <div className="text-sm text-ink-2">Starting Pantheon…</div>
      {waited > 20 && (
        <div className="max-w-sm text-center text-xs text-ink-3">
          {inDesktopApp()
            ? "This is taking longer than usual. The engine's log is in the app's data folder, under logs/engine.log."
            : "Waiting for the engine on port 8720. Start it with: cd engine && uv run python -m app.serve"}
        </div>
      )}
    </div>
  );
}
