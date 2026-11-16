"use client";

import { useEffect, useState } from "react";
import { quoteTargetName, selectionQuote, sendQuote, type Quote } from "@/lib/quote";
import { openPane } from "@/studio/CommandBar";

/**
 * A small button by any selection in a view that opted in (files, messages, documents):
 * it quotes the selected lines into the open message box, so the agent knows what you mean.
 */
export function QuoteButton() {
  const [at, setAt] = useState<{ x: number; y: number; quote: Quote; to: string | null | undefined } | null>(null);

  useEffect(() => {
    const show = () => {
      // After the browser has settled the selection.
      setTimeout(() => {
        const quote = selectionQuote();
        const sel = window.getSelection();
        if (!quote || !sel?.rangeCount) return setAt(null);
        const rects = sel.getRangeAt(0).getClientRects();
        const r = rects[rects.length - 1] ?? sel.getRangeAt(0).getBoundingClientRect();
        const x = Math.min(Math.max(8, r.right - 60), window.innerWidth - 200);
        const y = r.bottom + 40 > window.innerHeight ? Math.max(8, r.top - 36) : r.bottom + 6;
        setAt({ x, y, quote, to: quoteTargetName() });
      });
    };
    const hide = () => {
      if (window.getSelection()?.isCollapsed) setAt(null);
    };
    const gone = () => setAt(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey || e.key === "Shift") show();
      if (e.key === "Escape") setAt(null);
    };
    document.addEventListener("mouseup", show);
    document.addEventListener("keyup", onKey);
    document.addEventListener("selectionchange", hide);
    window.addEventListener("scroll", gone, true);
    return () => {
      document.removeEventListener("mouseup", show);
      document.removeEventListener("keyup", onKey);
      document.removeEventListener("selectionchange", hide);
      window.removeEventListener("scroll", gone, true);
    };
  }, []);

  if (!at) return null;
  const label = at.to ? `Quote to ${at.to}` : "Quote in chat";
  return (
    <button
      // Keep the selection while clicking.
      onMouseDown={(e) => e.preventDefault()}
      onMouseUp={(e) => e.stopPropagation()}
      onClick={() => {
        if (!sendQuote(at.quote)) openPane("console");
        window.getSelection()?.removeAllRanges();
        setAt(null);
      }}
      title="Put the selection in the message box, so the agent knows which part you mean"
      className="fixed z-[100] flex items-center gap-1.5 rounded-lg border border-line-2 bg-panel px-2.5 py-1 text-xs text-ink shadow-xl hover:bg-panel-2 cursor-pointer"
      style={{ left: at.x, top: at.y }}
    >
      <span aria-hidden>❝</span>
      {label}
      {at.quote.lines && (
        <span className="text-ink-3">
          {at.quote.lines[0] === at.quote.lines[1] ? `L${at.quote.lines[0]}` : `L${at.quote.lines[0]}–${at.quote.lines[1]}`}
        </span>
      )}
    </button>
  );
}
