/**
 * Where the engine (the Python sidecar) is.
 *
 * In the desktop app the page is served by Tauri from its own origin, so the engine is
 * reached at its address on this machine; the shell passes the port in
 * `window.__PANTHEON__`. In a plain browser on the UI dev server (port 1420), requests
 * stay relative and Vite proxies them to the engine.
 */

declare global {
  interface Window {
    __PANTHEON__?: { enginePort?: number };
    __TAURI_INTERNALS__?: unknown;
  }
}

export const DEFAULT_ENGINE_PORT = 8720;

export function inDesktopApp(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** The engine's HTTP origin ("" = same origin, through the dev server's proxy). */
export function engineHttp(): string {
  if (typeof window === "undefined" || !inDesktopApp()) return "";
  return `http://127.0.0.1:${window.__PANTHEON__?.enginePort ?? DEFAULT_ENGINE_PORT}`;
}

/** The engine's WebSocket origin. */
export function engineWs(): string {
  const http = engineHttp();
  if (http) return http.replace(/^http/, "ws");
  const { protocol, host } = window.location;
  return `${protocol === "https:" ? "wss" : "ws"}://${host}`;
}

/** A full URL for an engine API path such as "/orgs/x" (adds /api/v1). */
export function apiUrl(path: string): string {
  return `${engineHttp()}/api/v1${path}`;
}
