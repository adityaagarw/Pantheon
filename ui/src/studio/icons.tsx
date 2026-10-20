/** The Studio's line icons (24px grid, drawn with the current colour). */
import type { SVGProps } from "react";

const P: Record<string, string> = {
  canvas: "M4 6.5a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0M15 6.5a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0M9.5 17.5a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0M9 6.5h6M7.6 8.6l3.2 6.6M16.4 8.6l-3.2 6.6",
  chat: "M4 5h16v11H9l-5 4z",
  stage: "M3 5h18v12H3zM8 21h8M12 17v4",
  board: "M4 4h5v16H4zM10 4h5v10h-5zM16 4h4v7h-4z",
  comms: "M4 12h4l2-6 4 12 2-6h4",
  sessions: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4.5V12l3 2",
  memory: "M7 4h10l3 4v12H4V8zM9 12h6M9 16h4",
  files: "M5 3h9l5 5v13H5zM14 3v5h5",
  team: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14a6 6 0 0 1 3.5 6",
  settings: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM12 2v3m0 14v3M2 12h3m14 0h3M4.9 4.9l2.1 2.1m10 10 2.1 2.1m0-14.2-2.1 2.1m-10 10-2.1 2.1",
  office: "M3 21V9l9-6 9 6v12M9 21v-7h6v7",
  shadow: "M12 4C7 4 3 12 3 12s4 8 9 8 9-8 9-8-4-8-9-8zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  inspector: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16.5 16.5 21 21",
  home: "M3 10.5 12 3l9 7.5V21H3z",
  zeus: "M13 2 4 14h7l-1 8 9-12h-7z",
  argus: "M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  kronos: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4.5V12l3 2",
  bench: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  packs: "M12 2 3 7v10l9 5 9-5V7zM3 7l9 5 9-5M12 12v10",
  plus: "M12 5v14M5 12h14",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16.5 16.5 21 21",
  pause: "M8 5v14M16 5v14",
  play: "M7 4v16l13-8z",
  inbox: "M3 13h5l1.5 3h5l1.5-3h5M5 5h14l2 8v6H3v-6z",
  collapse: "M15 18l-6-6 6-6",
  expand: "M9 18l6-6-6-6",
  maximize: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  restore: "M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5",
  float: "M4 8h11v11H4zM9 4h11v11",
  close: "M6 6l12 12M18 6 6 18",
  live: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  timeline: "M3 12h18M7 8v8M12 5v14M17 9v6",
  send: "M4 12 20 4l-6 16-3-7z",
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  steer: "M12 2v4M12 18v4M2 12h4M18 12h4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 16, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden {...rest}>
      <path d={P[name]} />
    </svg>
  );
}
