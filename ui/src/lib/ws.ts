/**
 * Event stream client with gap-free reconnect.
 *
 * The server guarantees every persisted event with seq > `after` exactly once,
 * in order. We remember the last seq and reconnect with it (exponential
 * backoff); ephemeral events (seq === null) are delivered live only.
 */

import { engineHttp, engineWs } from "./engine";
import type { PantheonEvent } from "./types";

export type StreamState = "connecting" | "live" | "reconnecting" | "closed";

/** Where the engine's WebSockets live. */
export function backendWsBase(): string {
  return engineWs();
}

/** The engine's HTTP origin (Stage pages and live views are served from it directly). */
export function backendHttpBase(): string {
  return engineHttp() || window.location.origin;
}

export function wsUrl(): string {
  return `${engineWs()}/api/v1/ws`;
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
