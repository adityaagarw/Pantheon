"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { Button, cx } from "@/components/ui";
import { api } from "@/lib/api";
import type { PdfData, PdfHighlight } from "@/lib/types";
import { useOrg } from "@/store/org";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** A PDF read together: the page in view, agents' highlights, and the user's own. */
export default function PdfViewer({ docId, onView }: { docId: string; onView?: (text: string) => void }) {
  const [data, setData] = useState<PdfData | null>(null);
  const [pages, setPages] = useState(0);
  const [page, setPage] = useState(1);
  const [width, setWidth] = useState(800);
  const [selection, setSelection] = useState("");
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const ev = useOrg((s) => s.docs[docId]);
  const seen = useRef(0);

  useEffect(() => {
    api
      .stageDoc(docId)
      .then((d) => {
        const pd = d.data as PdfData;
        setData(pd);
        setPage(pd.page || 1);
        seen.current = d.version;
      })
      .catch((e) => setError((e as Error).message));
  }, [docId]);

  // An agent pointed somewhere: follow it.
  useEffect(() => {
    if (!ev || ev.version <= seen.current) return;
    seen.current = ev.version;
    if (ev.data) setData(ev.data as PdfData);
    if (ev.focus?.page && ev.updatedBy !== "user") setPage(ev.focus.page);
  }, [ev]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.min(el.clientWidth - 32, 1100)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    onView?.(`on page ${page} of ${pages || "?"}${selection ? `, with "${selection.slice(0, 200)}" selected` : ""}`);
  }, [page, pages, selection, onView]);

  // Highlights on this page, as one string so the text renderer only changes when they do.
  const markKey = (data?.highlights ?? [])
    .filter((h) => h.page === page)
    .map((h) => norm(h.text))
    .join("\u0000");
  const renderText = useCallback(
    ({ str }: { str: string }) => {
      const marks = markKey ? markKey.split("\u0000") : [];
      const s = norm(str);
      const hit = s.length > 2 && marks.some((m) => m.includes(s) || s.includes(m));
      return hit ? `<mark class="pantheon-hl">${escapeHtml(str)}</mark>` : escapeHtml(str);
    },
    [markKey],
  );

  const go = (p: number) => setPage(Math.max(1, Math.min(pages || p, p)));
  const highlight = async () => {
    if (!selection) return;
    try {
      const d = await api.pdfView(docId, { page, highlight: selection });
      setData(d.data as PdfData);
      window.getSelection()?.removeAllRanges();
      setSelection("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error) return <div className="p-6 text-sm text-bad">{error}</div>;
  if (!data) return <div className="p-6 text-sm text-ink-3">Loading PDF…</div>;
  const highlights = data.highlights ?? [];
  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-1.5 text-sm">
          <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => go(page - 1)}>
            ‹
          </Button>
          <span className="text-ink-2">
            Page{" "}
            <input
              className="w-12 rounded border border-line bg-panel-2 px-1 text-center"
              value={page}
              onChange={(e) => Number(e.target.value) && go(Number(e.target.value))}
            />{" "}
            of {pages || "…"}
          </span>
          <Button size="sm" variant="ghost" disabled={!!pages && page >= pages} onClick={() => go(page + 1)}>
            ›
          </Button>
          {selection && (
            <Button size="sm" variant="secondary" onClick={() => void highlight()}>
              Highlight selection
            </Button>
          )}
        </div>
        <div
          ref={box}
          className="min-h-0 flex-1 overflow-auto bg-[#525659] p-4"
          onMouseUp={() => setSelection((window.getSelection()?.toString() ?? "").trim())}
        >
          <Document
            file={api.stageDocExportUrl(docId)}
            onLoadSuccess={(pdf) => setPages(pdf.numPages)}
            onLoadError={(e) => setError(`Couldn't open the PDF: ${e.message}`)}
            loading={<div className="p-6 text-sm text-white/70">Loading PDF…</div>}
          >
            <Page pageNumber={page} width={width} customTextRenderer={renderText} className="mx-auto w-fit shadow-2xl" />
          </Document>
        </div>
      </div>
      {highlights.length > 0 && (
        <aside className="hidden w-64 shrink-0 overflow-y-auto border-l border-line bg-panel p-3 lg:block">
          <div className="mb-2 flex items-center text-xs font-medium uppercase tracking-wider text-ink-3">
            Highlights
            <button className="ml-auto normal-case tracking-normal text-ink-3 hover:text-ink cursor-pointer" onClick={() => void api.pdfView(docId, { clear: true }).then((d) => setData(d.data as PdfData))}>
              clear
            </button>
          </div>
          {highlights.map((h: PdfHighlight) => (
            <button
              key={h.id}
              onClick={() => go(h.page)}
              className={cx("mb-2 block w-full rounded-md border p-2 text-left text-xs cursor-pointer", h.page === page ? "border-warn/60 bg-warn/10" : "border-line hover:bg-panel-2")}
            >
              <div className="mb-1 text-[11px] text-ink-3">
                p. {h.page} · {h.by}
              </div>
              <div className="line-clamp-3 text-ink">“{h.text}”</div>
              {h.note && <div className="mt-1 text-ink-2">{h.note}</div>}
            </button>
          ))}
        </aside>
      )}
      <style>{`.pantheon-hl{background:rgba(255,213,79,.55);color:transparent;border-radius:2px}`}</style>
    </div>
  );
}
