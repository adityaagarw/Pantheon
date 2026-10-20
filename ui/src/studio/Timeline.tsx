/**
 * The Timeline: the org's history as a density strip, and a scrubber over it.
 *
 * Drag on it to go back in time; the Office shows the org as it was (replay.ts). Play
 * runs the history forward at 1×–300×; Live returns to now. Each bar is a slice of time,
 * as tall as the work in it and in the colour of the agent most active then. Red marks
 * a failure, amber a wait for you.
 */
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import { useOrg } from "@/store/org";
import { Icon } from "./icons";
import { agentHue, hsl } from "./look";
import { useReplay } from "./replay";
import { useStudio } from "./store";

const SPANS = [
  { label: "15m", ms: 15 * 60_000 },
  { label: "1h", ms: 60 * 60_000 },
  { label: "6h", ms: 6 * 60 * 60_000 },
  { label: "24h", ms: 24 * 60 * 60_000 },
  { label: "All", ms: 0 },
];
const SPEEDS = [1, 10, 60, 300];
const BARS = 220;
// What counts as work on the strip (not every token streamed).
const WORK = new Set([
  "message.created", "turn.started", "turn.completed", "turn.failed", "tool.started",
  "task.created", "task.updated", "approval.requested", "stage.updated",
]);

const clock = (t: number) =>
  new Date(t).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function Timeline() {
  const { orgId } = useParams<{ orgId: string }>();
  const agents = useOrg((s) => s.agents);
  const feedLen = useOrg((s) => s.feed.length);
  const events = useReplay((r) => r.events);
  const load = useReplay((r) => r.load);
  const show = useReplay((r) => r.show);
  const { at, setAt, speed, setSpeed, playing, setPlaying } = useStudio();
  const [span, setSpan] = useState(SPANS[1]!);
  const [open, setOpen] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const [hover, setHover] = useState<{ x: number; t: number; n: number; who: string; hue: number } | null>(null);
  const track = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  // The history: loaded for this org, and again as new work arrives (debounced).
  useEffect(() => {
    if (!orgId) return;
    const t = setTimeout(() => load(orgId), at === null ? 1500 : 0);
    return () => clearTimeout(t);
  }, [orgId, load, feedLen, at === null]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(t);
  }, []);

  // Leaving the org returns to live.
  useEffect(() => () => setAt(null), [orgId, setAt]);

  const start = span.ms ? now - span.ms : (events[0]?.ts ?? now - 60 * 60_000);
  const width = Math.max(1, now - start);

  const bars = useMemo(() => {
    const out = Array.from({ length: BARS }, () => ({ n: 0, by: {} as Record<string, number>, bad: false, wait: false }));
    for (const e of events) {
      if (e.ts < start || !WORK.has(e.type)) continue;
      const i = Math.min(BARS - 1, Math.floor(((e.ts - start) / width) * BARS));
      const b = out[i]!;
      b.n++;
      if (e.agentId) b.by[e.agentId] = (b.by[e.agentId] ?? 0) + 1;
      if (e.type === "turn.failed") b.bad = true;
      if (e.type === "approval.requested") b.wait = true;
    }
    const max = Math.max(1, ...out.map((b) => b.n));
    return out.map((b) => {
      const top = Object.entries(b.by).sort((x, y) => y[1] - x[1])[0]?.[0];
      return { ...b, h: b.n ? 0.18 + 0.82 * Math.sqrt(b.n / max) : 0, top };
    });
  }, [events, start, width]);

  // Playing: the cursor moves forward at the chosen speed and returns to live at now.
  useEffect(() => {
    if (!playing || at === null) return;
    let last = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const cur = useStudio.getState().at;
      if (cur === null) return;
      const next = cur + (t - last) * useStudio.getState().speed;
      last = t;
      if (next >= Date.now()) {
        setAt(null);
        return;
      }
      setAt(next);
      show(next);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, at === null, setAt, show]); // eslint-disable-line react-hooks/exhaustive-deps

  // Measured when a drag starts: replay mode adds controls that resize the strip, which
  // would otherwise move the pointer past its end (back to live) in the middle of the drag.
  const dragRect = useRef<DOMRect | null>(null);
  const timeAt = (clientX: number) => {
    const r = dragRect.current ?? track.current!.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    return start + f * width;
  };
  const scrub = (clientX: number) => {
    const t = timeAt(clientX);
    if (t >= now - 1000) {
      setAt(null);
      return;
    }
    setPlaying(false);
    setAt(t);
    show(t);
  };

  const replaying = at !== null;
  const cursor = replaying ? Math.min(1, Math.max(0, (at - start) / width)) : 1;

  return (
    <div className="shrink-0 px-3 pb-2.5 pt-1">
      <div
        className="glass relative flex items-center gap-3 rounded-xl px-3 transition-[height,border-color] duration-300"
        style={{ height: open ? 58 : 30, borderColor: replaying ? "rgba(76,201,240,0.45)" : undefined }}
      >
        <button
          onClick={() => setOpen(!open)}
          className="flex shrink-0 items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-[var(--st-ink-3)] hover:text-[var(--st-ink-2)]"
          title={open ? "Collapse the timeline" : "Expand the timeline"}
        >
          <Icon name="timeline" size={13} /> Timeline
        </button>

        {open && (
          <>
            <button
              title={playing ? "Pause" : replaying ? "Play from here" : "Replay this span"}
              onClick={() => {
                if (!replaying) {
                  // From the first work in view, not from an empty stretch before it.
                  const first = events.find((e) => e.ts >= start && WORK.has(e.type));
                  const from = Math.max(start, (first?.ts ?? start) - 2000);
                  setAt(from);
                  show(from);
                }
                setPlaying(!playing);
              }}
              className="grid size-8 shrink-0 place-items-center rounded-lg border border-[var(--st-edge)] text-[var(--st-ink-2)] hover:text-[var(--st-ink)]"
            >
              <Icon name={playing ? "pause" : "play"} size={13} />
            </button>

            <div
              ref={track}
              className="relative h-10 min-w-0 flex-1 cursor-ew-resize select-none"
              onPointerDown={(e) => {
                dragRect.current = track.current!.getBoundingClientRect();
                setDragging(true);
                (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
                scrub(e.clientX);
              }}
              onPointerMove={(e) => {
                if (dragging) scrub(e.clientX);
                const r = track.current!.getBoundingClientRect();
                const f = (e.clientX - r.left) / r.width;
                const b = bars[Math.min(BARS - 1, Math.max(0, Math.floor(f * BARS)))];
                const who = b?.top ? agents[b.top] : undefined;
                setHover({ x: f, t: timeAt(e.clientX), n: b?.n ?? 0, who: who?.name ?? "", hue: agentHue(who) });
              }}
              onPointerUp={() => {
                dragRect.current = null;
                setDragging(false);
              }}
              onPointerLeave={() => setHover(null)}
            >
              <div className="absolute inset-x-0 bottom-1 flex h-[30px] items-end gap-px">
                {bars.map((b, i) => (
                  <div
                    key={i}
                    className="flex-1 rounded-[1.5px] transition-[height] duration-500"
                    style={{
                      height: `${b.h * 100}%`,
                      background: b.bad
                        ? "var(--st-red)"
                        : b.wait
                          ? "var(--st-amber)"
                          : b.top
                            ? hsl(agentHue(agents[b.top]), 80, 64, 0.85)
                            : "rgba(148,160,220,0.25)",
                      opacity: replaying && i / BARS > cursor ? 0.28 : 1,
                    }}
                  />
                ))}
              </div>
              <div className="absolute inset-x-0 bottom-0.5 h-px bg-[var(--st-edge)]" />
              {/* the cursor */}
              <motion.div
                className="pointer-events-none absolute top-0 bottom-0 w-[2px] rounded-full"
                animate={{ left: `${cursor * 100}%` }}
                transition={dragging || playing ? { duration: 0 } : { type: "spring", stiffness: 400, damping: 40 }}
                style={{
                  background: replaying ? "var(--st-cyan)" : "var(--st-green)",
                  boxShadow: `0 0 12px ${replaying ? "var(--st-cyan)" : "var(--st-green)"}`,
                }}
              />
              {hover && (
                <div
                  className="glass pointer-events-none absolute bottom-full z-20 mb-2 -translate-x-1/2 whitespace-nowrap rounded-lg px-3 py-1.5 text-[11.5px]"
                  style={{ left: `${Math.min(Math.max(hover.x * 100, 8), 92)}%` }}
                >
                  <span className="st-mono text-[var(--st-ink-2)]">{clock(hover.t)}</span>
                  {hover.n > 0 && (
                    <span className="text-[var(--st-ink-3)]">
                      {" · "}{hover.n} event{hover.n === 1 ? "" : "s"}
                      {hover.who && <> · mostly <span style={{ color: hsl(hover.hue, 75, 80) }}>{hover.who}</span></>}
                    </span>
                  )}
                </div>
              )}
            </div>

            <div className="flex shrink-0 items-center gap-1">
              {SPANS.map((s) => (
                <button
                  key={s.label}
                  onClick={() => setSpan(s)}
                  className="st-mono rounded-md px-1.5 py-0.5 text-[11px]"
                  style={{ color: s === span ? "var(--st-ink)" : "var(--st-ink-3)", background: s === span ? "rgba(255,255,255,0.06)" : undefined }}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </>
        )}

        <AnimatePresence mode="wait">
          {replaying ? (
            <motion.div key="replay" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
              className="flex shrink-0 items-center gap-2">
              {open && (
                <div className="flex items-center gap-0.5 rounded-lg border border-[var(--st-edge)] p-0.5">
                  {SPEEDS.map((v) => (
                    <button key={v} onClick={() => setSpeed(v)} className="st-mono rounded-md px-1.5 py-0.5 text-[10.5px]"
                      style={{ color: v === speed ? "var(--st-ink)" : "var(--st-ink-3)", background: v === speed ? "rgba(76,201,240,0.16)" : undefined }}>
                      {v}×
                    </button>
                  ))}
                </div>
              )}
              <span className="st-mono text-[11px] text-[var(--st-cyan)]">{clock(at)}</span>
              <button onClick={() => setAt(null)}
                className="flex items-center gap-1.5 rounded-lg bg-[rgba(61,220,151,0.12)] px-2.5 py-1 text-[11px] text-[var(--st-green)] hover:bg-[rgba(61,220,151,0.2)]">
                <span className="size-1.5 rounded-full bg-[var(--st-green)]" /> Back to live
              </button>
            </motion.div>
          ) : (
            <motion.span key="live" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="inline-flex shrink-0 items-center gap-1.5 text-[11px] text-[var(--st-green)]">
              <span className="size-1.5 animate-pulse rounded-full bg-[var(--st-green)]" /> Live
            </motion.span>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
