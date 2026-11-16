/**
 * Quote what the user selected (lines of a file, part of a message) into a chat box, so they
 * can point an agent at it. Views opt in with data-quote="<source>" (plus data-quote-code for
 * code, and data-line="<n>" on each line); message boxes register to receive the quote.
 */

export interface Quote {
  /** Where it's from: a file path, "Ada's message"... */
  source: string;
  text: string;
  code?: boolean;
  lines?: [number, number];
}

interface Target {
  insert: (text: string) => void;
  /** Who reads it, for the button ("Quote to Ada"). */
  name?: string;
  /** The box itself: one on a hidden tab or pane doesn't count. */
  element: () => HTMLElement | null;
  focusedAt: number;
}

const MAX = 6000;
const targets = new Map<symbol, Target>();
let pending: string | null = null;

/** A message box: the most recently focused visible one (or the newest) receives quotes. */
export function registerQuoteTarget(
  insert: (text: string) => void,
  element: () => HTMLElement | null,
  name?: string,
): { focused: () => void; dispose: () => void } {
  const id = Symbol();
  targets.set(id, { insert, element, name, focusedAt: Date.now() });
  // A quote made while no box was open goes to the first one that shows.
  setTimeout(() => {
    const el = element();
    if (pending && el?.getClientRects().length) {
      insert(pending);
      pending = null;
    }
  });
  return {
    focused: () => {
      const t = targets.get(id);
      if (t) t.focusedAt = Date.now();
    },
    dispose: () => void targets.delete(id),
  };
}

function current(): Target | undefined {
  let best: Target | undefined;
  for (const t of targets.values()) {
    const el = t.element();
    if (!el || !el.getClientRects().length) continue;
    if (!best || t.focusedAt >= best.focusedAt) best = t;
  }
  return best;
}

export function quoteTargetName(): string | null | undefined {
  const t = current();
  return t ? (t.name ?? null) : undefined;
}

/** The quote as message text, ready for the user's question after it. */
export function formatQuote(q: Quote): string {
  let text = q.text.replace(/\s+$/, "");
  if (text.length > MAX) text = `${text.slice(0, MAX)}\n…(cut short)`;
  const where = q.lines ? (q.lines[0] === q.lines[1] ? `, line ${q.lines[0]}` : `, lines ${q.lines[0]}–${q.lines[1]}`) : "";
  const head = q.code ? `\`${q.source}\`${where}:` : `From ${q.source}${where}:`;
  if (q.code) {
    const ticks = "`".repeat(Math.max(3, ...Array.from(text.matchAll(/`+/g), (m) => m[0].length + 1)));
    return `${head}\n${ticks}\n${text}\n${ticks}\n\n`;
  }
  return `${head}\n${text.split("\n").map((l) => `> ${l}`).join("\n")}\n\n`;
}

/** Puts the quote in the current message box; false if none is open (it waits for the next). */
export function sendQuote(q: Quote): boolean {
  const text = formatQuote(q);
  const t = current();
  if (t) {
    t.insert(text);
    return true;
  }
  pending = text;
  return false;
}

/** What the current selection quotes, if it lies in a view that opted in. */
export function selectionQuote(): Quote | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  const el = (n: Node) => (n instanceof Element ? n : n.parentElement);
  const box = el(range.commonAncestorContainer)?.closest<HTMLElement>("[data-quote]");
  if (!box) return null;
  const code = box.hasAttribute("data-quote-code");
  const row = (n: Node) => el(n)?.closest<HTMLElement>("[data-line]");
  const first = row(range.startContainer);
  let last = row(range.endContainer);
  if (first && last) {
    // A selection that runs to the start of the next line (a triple-click) ends on this one.
    if (last !== first) {
      const tail = document.createRange();
      tail.setStart(last, 0);
      tail.setEnd(range.endContainer, range.endOffset);
      if (!tail.toString()) last = (last.previousElementSibling as HTMLElement | null) ?? last;
    }
    const a = Number(first.dataset.line);
    const b = Number(last.dataset.line);
    // Whole lines, without the line-number gutter.
    const lines: string[] = [];
    for (let r: Element | null = first; r; r = r.nextElementSibling) {
      lines.push((r.querySelector("[data-text]")?.textContent ?? "").replace(/\u200b/g, ""));
      if (r === last) break;
    }
    return { source: box.dataset.quote!, text: lines.join("\n"), code, lines: [a, b] };
  }
  const text = sel.toString().trim();
  return text ? { source: box.dataset.quote!, text, code } : null;
}
