"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { Button, cx } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { MarkdownData, StageDoc } from "@/lib/types";
import { useOrg } from "@/store/org";

type Mode = "edit" | "split" | "preview";
const MODE_KEY = "stage.md.mode";

/** A Markdown document shared with agents: write on the left, read on the right. What an
 * agent changes arrives live; if you were typing at the time, you choose whose text stays. */
export default function MarkdownDoc({ docId, onView }: { docId: string; onView?: (text: string) => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<"saved" | "saving" | "unsaved">("saved");
  const [theirs, setTheirs] = useState<{ text: string; version: number; by: string } | null>(null);
  const [mode, setMode] = useState<Mode>(() => {
    try {
      return (localStorage.getItem(MODE_KEY) as Mode) || "split";
    } catch {
      return "split";
    }
  });
  const area = useRef<HTMLTextAreaElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  const textRef = useRef("");
  const seen = useRef(0); // the version our text is based on
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const again = useRef<() => Promise<void>>(undefined);
  const ev = useOrg((s) => s.docs[docId]);

  const choose = (m: Mode) => {
    setMode(m);
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      // a convenience only
    }
  };

  useEffect(() => {
    api
      .stageDoc(docId)
      .then((d) => {
        const t = (d.data as MarkdownData).text ?? "";
        textRef.current = t;
        setText(t);
        seen.current = d.version;
        if (!t) choose("edit");
      })
      .catch((e) => setError((e as Error).message));
  }, [docId]);

  const save = useCallback(
    async (base = seen.current) => {
      clearTimeout(timer.current);
      if (!dirty.current) return;
      setState("saving");
      const sent = textRef.current;
      try {
        const r = await api.saveStageText(docId, sent, base);
        seen.current = r.version;
        if (textRef.current === sent) {
          dirty.current = false;
          setState("saved");
        } else {
          setState("unsaved");
          timer.current = setTimeout(() => void again.current?.(), 700); // typed while it saved
        }
        setTheirs(null);
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          try {
            const doc = (JSON.parse(e.message) as { doc: StageDoc }).doc;
            setTheirs({ text: (doc.data as MarkdownData).text ?? "", version: doc.version, by: doc.updatedBy });
          } catch {
            setError("Someone changed this document meanwhile; reload it to see their changes.");
          }
          setState("unsaved");
        } else {
          setError((e as Error).message);
          setState("unsaved");
        }
      }
    },
    [docId],
  );
  useEffect(() => {
    again.current = () => save();
  }, [save]);

  // Changes from agents arrive live. With nothing unsaved we take them; otherwise you choose.
  useEffect(() => {
    if (!ev?.data || ev.version <= seen.current) return;
    const incoming = (ev.data as MarkdownData).text ?? "";
    if (incoming === textRef.current) {
      seen.current = ev.version;
      return;
    }
    if (dirty.current) {
      setTheirs({ text: incoming, version: ev.version, by: ev.updatedBy });
      return;
    }
    seen.current = ev.version;
    textRef.current = incoming;
    setText(incoming);
  }, [ev]);

  useEffect(() => () => clearTimeout(timer.current), []);

  // Side by side needs room: a narrow pane shows one side at a time.
  const loaded = text !== null;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setNarrow(e.contentRect.width < 720));
    ro.observe(el);
    return () => ro.disconnect();
  }, [loaded]);

  const type = (t: string) => {
    textRef.current = t;
    setText(t);
    dirty.current = true;
    setState("unsaved");
    clearTimeout(timer.current);
    if (!theirs) timer.current = setTimeout(() => void save(), 700);
  };

  // Tell the agent where in the document the user is: the section around the cursor.
  const where = () => {
    const el = area.current;
    if (!el || !onView) return;
    const before = el.value.slice(0, el.selectionStart);
    const line = before.split("\n").length;
    const heading = [...before.matchAll(/^#{1,6}\s+(.+)$/gm)].pop()?.[1];
    onView(`editing line ${line}${heading ? `, in the section "${heading.trim()}"` : ""}`);
  };

  /** Wrap the selection (or insert at the cursor) and keep it selected. */
  const wrap = (before: string, after = before, placeholder = "text") => {
    const el = area.current;
    if (!el) return;
    const { selectionStart: s, selectionEnd: e, value } = el;
    const inner = value.slice(s, e) || placeholder;
    type(value.slice(0, s) + before + inner + after + value.slice(e));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(s + before.length, s + before.length + inner.length);
    });
  };

  /** Prefix every selected line (headings, lists, quotes). */
  const prefix = (p: string | ((i: number) => string)) => {
    const el = area.current;
    if (!el) return;
    const { selectionStart: s, selectionEnd: e, value } = el;
    const start = value.lastIndexOf("\n", s - 1) + 1;
    const endNl = value.indexOf("\n", e);
    const end = endNl === -1 ? value.length : endNl;
    const lines = value.slice(start, end).split("\n");
    const out = lines.map((l, i) => (typeof p === "function" ? p(i) : p) + l.replace(/^(#{1,6}\s|[-*]\s(\[[ x]\]\s)?|\d+\.\s|>\s)/, "")).join("\n");
    type(value.slice(0, start) + out + value.slice(end));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start, start + out.length);
    });
  };

  const insert = (snippet: string) => {
    const el = area.current;
    if (!el) return;
    const { selectionStart: s, value } = el;
    const pre = s > 0 && value[s - 1] !== "\n" ? "\n\n" : "";
    type(value.slice(0, s) + pre + snippet + value.slice(s));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(s + pre.length + snippet.length, s + pre.length + snippet.length);
    });
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "b") {
      e.preventDefault();
      wrap("**");
    } else if (mod && e.key.toLowerCase() === "i") {
      e.preventDefault();
      wrap("*");
    } else if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      e.stopPropagation(); // not the command bar
      wrap("[", "](https://)", "link");
    } else if (mod && e.key.toLowerCase() === "s") {
      e.preventDefault();
      void save();
    } else if (e.key === "Tab") {
      e.preventDefault();
      const el = e.currentTarget;
      const { selectionStart: s, selectionEnd: t } = el;
      type(`${el.value.slice(0, s)}  ${el.value.slice(t)}`);
      requestAnimationFrame(() => el.setSelectionRange(s + 2, s + 2));
    } else if (e.key === "Enter" && !e.shiftKey) {
      // Lists carry on to the next line; an empty item ends the list.
      const el = e.currentTarget;
      const { selectionStart: s, value } = el;
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      const line = value.slice(lineStart, s);
      const m = line.match(/^(\s*)([-*]\s\[[ x]\]\s|[-*]\s|(\d+)\.\s)/);
      if (!m) return;
      e.preventDefault();
      if (line.trim() === m[2].trim()) {
        type(value.slice(0, lineStart) + value.slice(s));
        requestAnimationFrame(() => el.setSelectionRange(lineStart, lineStart));
        return;
      }
      const next = m[3] ? `${Number(m[3]) + 1}. ` : m[2].replace("[x]", "[ ]");
      const add = `\n${m[1]}${next}`;
      type(value.slice(0, s) + add + value.slice(s));
      requestAnimationFrame(() => el.setSelectionRange(s + add.length, s + add.length));
    }
  };

  const takeTheirs = () => {
    if (!theirs) return;
    clearTimeout(timer.current);
    seen.current = theirs.version;
    textRef.current = theirs.text;
    dirty.current = false;
    setText(theirs.text);
    setTheirs(null);
    setState("saved");
  };
  const keepMine = () => {
    if (!theirs) return;
    const base = theirs.version;
    setTheirs(null);
    void save(base);
  };

  if (error && text === null) return <div className="p-6 text-sm text-bad">{error}</div>;
  if (text === null) return <div className="p-6 text-sm text-ink-3">Loading document…</div>;

  const shown: Mode = narrow && mode === "split" ? "edit" : mode;
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const tool = "rounded px-1.5 py-0.5 text-[12px] text-ink-2 hover:bg-panel-2 hover:text-ink cursor-pointer";
  return (
    <div ref={box} className="flex h-full min-h-0 flex-col bg-bg">
      <div className="no-scrollbar flex items-center gap-0.5 overflow-x-auto whitespace-nowrap border-b border-line bg-panel px-2 py-1">
        {shown !== "preview" && (
          <>
            <button className={cx(tool, "font-bold")} title="Bold (Ctrl+B)" onClick={() => wrap("**")}>B</button>
            <button className={cx(tool, "italic")} title="Italic (Ctrl+I)" onClick={() => wrap("*")}>I</button>
            <button className={cx(tool, "line-through")} title="Strikethrough" onClick={() => wrap("~~")}>S</button>
            <span className="mx-1 h-4 w-px bg-line-2" />
            <button className={tool} title="Heading 1" onClick={() => prefix("# ")}>H1</button>
            <button className={tool} title="Heading 2" onClick={() => prefix("## ")}>H2</button>
            <button className={tool} title="Heading 3" onClick={() => prefix("### ")}>H3</button>
            <span className="mx-1 h-4 w-px bg-line-2" />
            <button className={tool} title="Bulleted list" onClick={() => prefix("- ")}>• List</button>
            <button className={tool} title="Numbered list" onClick={() => prefix((i) => `${i + 1}. `)}>1. List</button>
            <button className={tool} title="Checklist" onClick={() => prefix("- [ ] ")}>☐ Tasks</button>
            <button className={tool} title="Quote" onClick={() => prefix("> ")}>❝</button>
            <span className="mx-1 h-4 w-px bg-line-2" />
            <button className={cx(tool, "font-mono")} title="Code" onClick={() => wrap("`", "`", "code")}>{"<>"}</button>
            <button className={tool} title="Code block" onClick={() => insert("```\n\n```\n")}>{"{ }"}</button>
            <button className={tool} title="Link (Ctrl+K)" onClick={() => wrap("[", "](https://)", "link")}>🔗</button>
            <button className={tool} title="Table" onClick={() => insert("| Column | Column |\n| --- | --- |\n| | |\n")}>▦</button>
            <button className={tool} title="Divider" onClick={() => insert("---\n")}>―</button>
          </>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-2 pl-2">
          <span className={cx("text-[11px]", state === "saved" ? "text-ink-3" : "text-warn")}>
            {state === "saving" ? "Saving…" : state === "unsaved" ? "Unsaved" : "Saved"} · {words} words
          </span>
          <div className="flex rounded-md border border-line p-0.5">
            {(narrow ? (["edit", "preview"] as Mode[]) : (["edit", "split", "preview"] as Mode[])).map((m) => (
              <button
                key={m}
                onClick={() => choose(m)}
                className={cx("rounded px-2 py-0.5 text-[11.5px] capitalize cursor-pointer", shown === m ? "bg-panel-2 text-ink" : "text-ink-3 hover:text-ink-2")}
              >
                {m === "preview" ? "Read" : m}
              </button>
            ))}
          </div>
        </div>
      </div>
      {theirs && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-warn/10 px-3 py-1.5 text-xs">
          <span className="text-warn">{theirs.by} changed this document while you were typing.</span>
          <Button size="sm" variant="ghost" onClick={takeTheirs}>
            Take theirs
          </Button>
          <Button size="sm" variant="ghost" onClick={keepMine}>
            Keep mine
          </Button>
        </div>
      )}
      {error && <div className="border-b border-line bg-bad/10 px-3 py-1.5 text-xs text-bad">{error}</div>}
      <div className="flex min-h-0 flex-1">
        {shown !== "preview" && (
          <textarea
            ref={area}
            value={text}
            spellCheck
            onChange={(e) => type(e.target.value)}
            onKeyDown={onKey}
            onKeyUp={where}
            onClick={where}
            onBlur={() => void save()}
            placeholder={"# Title\n\nWrite in Markdown. Your agents can read and edit this too."}
            className={cx(
              "min-h-0 flex-1 resize-none bg-bg px-5 py-4 font-mono text-[13px] leading-relaxed text-ink outline-none",
              shown === "split" && "border-r border-line",
            )}
          />
        )}
        {shown !== "edit" && (
          <div className="doc-panel min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {text.trim() ? <Markdown className="prose-doc">{text}</Markdown> : <div className="text-sm text-ink-3">Nothing written yet.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
