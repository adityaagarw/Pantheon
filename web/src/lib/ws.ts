/**
 * Event stream client with gap-free reconnect.
 *
 * The server guarantees every persisted event with seq > `after` exactly once,
 * in order. We remember the last seq and reconnect with it (exponential
 * backoff); ephemeral events (seq === null) are delivered live only.
 */

import type { PantheonEvent } from "./types";

export type StreamState = "connecting" | "live" | "reconnecting" | "closed";

const WEB_PORT = process.env.NEXT_PUBLIC_PANTHEON_WEB_PORT ?? "3710";
const BACKEND_PORT = process.env.NEXT_PUBLIC_PANTHEON_BACKEND_PORT ?? "8710";

/**
 * True when the page is served by Next itself (its published port, or 3000 in
 * dev) rather than the HTTPS proxy. Next's rewrites don't proxy WebSockets, so
 * then the browser talks to the backend's port directly; behind the proxy
 * everything is same-origin.
 */
export function servedDirect(): boolean {
  const { port } = window.location;
  return port === WEB_PORT || port === "3000";
}

/** Where the backend's WebSockets live. */
export function backendWsBase(): string {
  const { protocol, hostname, host } = window.location;
  const scheme = protocol === "https:" ? "wss" : "ws";
  return servedDirect() ? `${scheme}://${hostname}:${BACKEND_PORT}` : `${scheme}://${host}`;
}

/** The backend's HTTP origin (Stage pages and live views are served from it directly). */
export function backendHttpBase(): string {
  const { protocol, hostname, host } = window.location;
  return servedDirect() ? `${protocol}//${hostname}:${BACKEND_PORT}` : `${protocol}//${host}`;
}

export function wsUrl(): string {
  return process.env.NEXT_PUBLIC_PANTHEON_WS_URL ?? `${backendWsBase()}/api/v1/ws`;
}

export class EventStream {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pinger: ReturnType<typeof setInterval> | null = null;
  private closed = false;

  constructor(
    private orgId: string | null,
    private onEvent: (e: PantheonEvent) => void,
    private onState: (s: StreamState) => void = () => {},
    startSeq = 0,
  ) {
    this.lastSeq = startSeq;
    this.connect();
  }

  get seq(): number {
    return this.lastSeq;
  }

  private connect(): void {
    if (this.closed) return;
    this.onState(this.attempt === 0 ? "connecting" : "reconnecting");
    const params = new URLSearchParams();
    if (this.orgId) params.set("org", this.orgId);
    if (this.lastSeq > 0) params.set("after", String(this.lastSeq));
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${wsUrl()}?${params}`);
    } catch {
      this.schedule();
      return;
    }
    this.ws = ws;
    ws.onmessage = (m) => {
      if (typeof m.data !== "string" || m.data === "pong") return;
      let ev: PantheonEvent & { type: string; seq: number | null };
      try {
        ev = JSON.parse(m.data);
      } catch {
        return;
      }
      if (ev.type === "hello") {
        this.lastSeq = Math.max(this.lastSeq, ev.seq ?? 0);
        this.attempt = 0;
        this.onState("live");
        return;
      }
      if (ev.type === "resync") {
        ws.close();
        return;
      }
      if (typeof ev.seq === "number") {
        if (ev.seq <= this.lastSeq) return;
        this.lastSeq = ev.seq;
      }
      this.onEvent(ev);
    };
    ws.onclose = () => {
      if (this.pinger) clearInterval(this.pinger);
      this.ws = null;
      this.schedule();
    };
    ws.onerror = () => ws.close();
    ws.onopen = () => {
      this.pinger = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send("ping"), 25000);
    };
  }

  private schedule(): void {
    if (this.closed) return;
    this.onState("reconnecting");
    const delay = Math.min(15000, 500 * 2 ** this.attempt) + Math.random() * 300;
    this.attempt += 1;
    this.timer = setTimeout(() => this.connect(), delay);
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.pinger) clearInterval(this.pinger);
    this.ws?.close();
    this.onState("closed");
  }
}
