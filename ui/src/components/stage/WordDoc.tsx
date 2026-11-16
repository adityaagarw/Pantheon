"use client";

import "@eigenpal/docx-editor-react/styles.css";
import { DocxEditor, type DocxEditorRef } from "@eigenpal/docx-editor-react";
import type { EditorView } from "prosemirror-view";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, cx } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { sendQuote } from "@/lib/quote";
import { useOrg } from "@/store/org";

/** A Word document shared with agents, edited in pages like Word. It saves itself as you
 * type; what an agent changes arrives live, and if you were typing you choose whose stays. */
export default function WordDoc({
  docId,
  title,
  agentName,
  onView,
}: {
  docId: string;
  title?: string;
  /** Who is told what the user selected. */
  agentName?: string;
  onView?: (text: string) => void;
}) {
  const [file, setFile] = useState<{ data: ArrayBuffer; key: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<"saved" | "saving" | "unsaved">("saved");
  const [theirs, setTheirs] = useState<{ version: number; by: string } | null>(null);
  const [pointing, setPointing] = useState<{ a: number; b: number; text: string } | null>(null);
  const editor = useRef<DocxEditorRef>(null);
  const view = useRef<EditorView | null>(null);
  const seen = useRef(0); // the version our copy is based on
  const dirty = useRef(false);
  const readyAt = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const ev = useOrg((s) => s.docs[docId]);

  const load = useCallback(async () => {
    try {
      const f = await api.wordFile(docId);
      seen.current = f.version;
      dirty.current = false;
      readyAt.current = Date.now() + 1500; // the editor reports changes while it lays out
      setFile((old) => ({ data: f.data, key: (old?.key ?? 0) + 1 }));
      setState("saved");
      setTheirs(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [docId]);
  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (base = seen.current) => {
      clearTimeout(timer.current);
      if (!dirty.current || !editor.current) return;
      setState("saving");
      try {
        const out = await editor.current.save();
        if (!out) return;
        dirty.current = false;
        const r = await api.saveWord(docId, out, base);
        seen.current = r.version;
        setState(dirty.current ? "unsaved" : "saved");
        setTheirs(null);
      } catch (e) {
        dirty.current = true;
        setState("unsaved");
        if (e instanceof ApiError && e.status === 409) {
          setTheirs({ version: (ev?.version ?? seen.current) as number, by: ev?.updatedBy ?? "someone" });
        } else {
          setError((e as Error).message);
        }
      }
    },
    [docId, ev],
  );

  const changed = () => {
    if (Date.now() < readyAt.current) return;
    dirty.current = true;
    setState("unsaved");
    clearTimeout(timer.current);
    if (!theirs) timer.current = setTimeout(() => void save(), 1200);
  };
  useEffect(() => () => clearTimeout(timer.current), []);

  // Agents' changes arrive live: with nothing unsaved we reload; otherwise you choose.
  useEffect(() => {
    if (!ev || ev.version <= seen.current || ev.updatedBy === "user") return;
    if (dirty.current) setTheirs({ version: ev.version, by: ev.updatedBy });
    else void load();
  }, [ev, load]);

  /** The selection, as word_read numbers paragraphs (top-level ones; tables aside). */
  const selected = () => {
    const v = view.current;
    if (!v || !onView) return;
    const { from, to, empty } = v.state.selection;
    if (empty) {
      if (pointing) {
        setPointing(null);
        onView("reading it");
      }
      return;
    }
    let a = 0;
    let b = 0;
    let n = 0;
    v.state.doc.forEach((node, offset) => {
      if (node.type.name !== "paragraph") return;
      n += 1;
      if (offset <= from) a = n;
      if (offset < to) b = n;
    });
    const text = v.state.doc.textBetween(from, to, "\n").trim();
    if (!text) return;
    setPointing({ a, b, text });
    const where = a ? (a === b ? `paragraph ${a}` : `paragraphs ${a}-${b}`) : "a passage";
    const shown = text.length > 1500 ? `${text.slice(0, 1500)}…` : text;
    onView(`they've selected ${where} (as word_read numbers them), so "this" or "here" means this passage:\n"""\n${shown}\n"""\n`);
  };

  const takeTheirs = () => void load();
  const keepMine = () => {
    if (!theirs) return;
    const base = theirs.version;
    setTheirs(null);
    void save(base);
  };

  if (error && !file) return <div className="p-6 text-sm text-bad">{error}</div>;
  if (!file) return <div className="p-6 text-sm text-ink-3">Loading document…</div>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-line bg-panel px-3 py-1 text-[11px]">
        {pointing && onView && (
          <span
            className="flex items-center gap-1.5 rounded bg-accent/15 px-1.5 py-0.5 text-ink"
            title={`${agentName ?? "Your agent"} is told about this selection, so "this" or "here" means it`}
          >
            ◉ {pointing.a ? (pointing.a === pointing.b ? `Paragraph ${pointing.a}` : `Paragraphs ${pointing.a}–${pointing.b}`) : "Selection"} shared with{" "}
            {agentName ?? "the agent"}
            <button
              className="text-ink-2 underline hover:text-ink cursor-pointer"
              title="Also put it in the message box"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => sendQuote({ source: title ?? "the document", text: pointing.text })}
            >
              quote
            </button>
          </span>
        )}
        <span className={cx("ml-auto", state === "saved" ? "text-ink-3" : "text-warn")}>
          {state === "saving" ? "Saving…" : state === "unsaved" ? "Unsaved" : "Saved"}
        </span>
      </div>
      {theirs && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-warn/10 px-3 py-1.5 text-xs">
          <span className="text-warn">{theirs.by} changed this document while you were editing.</span>
          <Button size="sm" variant="ghost" onClick={takeTheirs}>
            Take theirs
          </Button>
          <Button size="sm" variant="ghost" onClick={keepMine}>
            Keep mine
          </Button>
        </div>
      )}
      {error && <div className="border-b border-line bg-bad/10 px-3 py-1.5 text-xs text-bad">{error}</div>}
      <div className="min-h-0 flex-1" onMouseUp={() => setTimeout(selected)} onKeyUp={() => setTimeout(selected)}>
        <DocxEditor
          key={file.key}
          ref={editor}
          documentBuffer={file.data}
          mode="editing"
          author="You"
          showFileOpen={false}
          showHelpMenu={false}
          onChange={changed}
          onEditorViewReady={(v) => {
            view.current = v;
          }}
          onError={(e) => setError(e.message)}
          style={{ height: "100%" }}
        />
      </div>
    </div>
  );
}
