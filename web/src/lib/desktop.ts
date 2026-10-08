import { servedDirect } from "./ws";

/** The noVNC live view of the sandbox desktop; `control` lets the user use it. */
export function desktopViewUrl(control: boolean): string {
  const { protocol, hostname } = window.location;
  const direct = servedDirect();
  const base = direct ? `${protocol}//${hostname}:${process.env.NEXT_PUBLIC_PANTHEON_DESKTOP_PORT ?? "6901"}` : "/desktop";
  return `${base}/vnc.html?autoconnect=1&resize=scale&reconnect=true&view_only=${control ? 0 : 1}${direct ? "" : "&path=desktop/websockify"}`;
}
