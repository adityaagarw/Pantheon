"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, ErrorNote, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Agent, AttachmentInfo, StageDocEvent, StageDocKind } from "@/lib/types";
import { useAttention } from "./useAttention";

function Loading() {
  return <div className="p-6 text-sm text-ink-3">Loading…</div>;
}
const SheetEditor = dynamic(() => import("./SheetEditor"), { ssr: false, loading: Loading });
const PdfViewer = dynamic(() => import("./PdfViewer"), { ssr: false, loading: Loading });
const NotebookView = dynamic(() => import("./NotebookView"), { ssr: false, loading: Loading });
const MarkdownDoc = dynamic(() => import("./MarkdownDoc"), { ssr: false, loading: Loading });

export const DOC_ICON: Record<StageDocKind, string> = { sheet: "▦", pdf: "📄", notebook: "📓", markdown: "📝" };
const KIND_LABEL: Record<StageDocKind, string> = { sheet: "Spreadsheet", pdf: "PDF", notebook: "Notebook", markdown: "Markdown" };

/** Which kind of document a file opens as, if any. */
export function docKindFor(name: string): StageDocKind | null {
  const n = name.toLowerCase();
  if (n.endsWith(".pdf")) return "pdf";
  if (n.endsWith(".xlsx") || n.endsWith(".xlsm") || n.endsWith(".csv") || n.endsWith(".tsv")) return "sheet";
  if (n.endsWith(".ipynb")) return "notebook";
  if (n.endsWith(".md") || n.endsWith(".markdown")) return "markdown";
  return null;
}

/** The agent the user is working with on this document: who made it, else who last changed it. */
export function docAgent(doc: StageDocEvent | undefined, agents: Record<string, Agent>): string | null {
  if (!doc) return null;
  if (agents[doc.createdBy]) return doc.createdBy;
  return Object.values(agents).find((a) => a.name === doc.updatedBy)?.id ?? null;
}

export function DocView({
  doc,
  agents,
  chatAgentId,
  onDeleted,
}: {
  doc: StageDocEvent;
  agents: Record<string, Agent>;
  /** The agent the user is chatting with on the Stage, if any: they're told what's on screen. */
  chatAgentId?: string | null;
  onDeleted: () => void;
}) {
  const [where, setWhere] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(doc.title);
  const [error, setError] = useState<string | null>(null);
  const agentId = chatAgentId ?? docAgent(doc, agents);
  useAttention(agentId, agentId ? { kind: doc.kind, docId: doc.id, title: doc.title, text: where } : null, [agentId, doc.id, doc.title, where]);
  const onView = useCallback((t: string) => setWhere(t), []);
  const exportLabel = doc.kind === "sheet" ? ".xlsx" : doc.kind === "notebook" ? ".ipynb" : doc.kind === "markdown" ? ".md" : "PDF";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2 text-sm">
        <span className="w-5 text-center">{DOC_ICON[doc.kind]}</span>
        {renaming ? (
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => {
              setRenaming(false);
              if (title.trim() && title !== doc.title) void api.renameStageDoc(doc.id, title).catch((e) => setError((e as Error).message));
            }}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
            className="rounded border border-line bg-panel-2 px-2 py-0.5 font-semibold"
          />
        ) : (
          <button className="font-semibold hover:underline cursor-text" onClick={() => (setTitle(doc.title), setRenaming(true))} title="Rename">
            {doc.title}
          </button>
        )}
        <Badge>{KIND_LABEL[doc.kind]}</Badge>
        <span className="text-xs text-ink-3">
          {doc.updatedBy !== "user" ? `${doc.updatedBy} · ` : ""}
          {timeAgo(doc.updatedAt)} · shared with your agents
        </span>
        <div className="ml-auto flex items-center gap-1">
          <a href={api.stageDocExportUrl(doc.id)} download className="rounded px-2 py-1 text-xs text-ink-2 hover:bg-panel-2 hover:text-ink">
            {exportLabel} ↓
          </a>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (window.confirm(`Delete “${doc.title}”?`)) void api.deleteStageDoc(doc.id).then(onDeleted, (e) => setError((e as Error).message));
            }}
          >
            Delete
          </Button>
        </div>
      </div>
      <ErrorNote error={error} />
      <div className={cx("min-h-0 flex-1", doc.kind === "sheet" && "bg-white")}>
        {doc.kind === "sheet" ? (
          <SheetEditor key={doc.id} docId={doc.id} onSelection={onView} />
        ) : doc.kind === "pdf" ? (
          <PdfViewer key={doc.id} docId={doc.id} onView={onView} />
        ) : doc.kind === "markdown" ? (
          <MarkdownDoc key={doc.id} docId={doc.id} onView={onView} />
        ) : (
          <NotebookView key={doc.id} docId={doc.id} onView={onView} />
        )}
      </div>
    </div>
  );
}

/** "+" on the Documents section: a new sheet or notebook, or open one from Files. */
export function NewDocMenu({ orgId, count, onCreated }: { orgId: string; count: number; onCreated: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<AttachmentInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    api
      .attachments(orgId)
      .then((all) => setFiles(all.filter((a) => docKindFor(a.name) && a.status !== "failed")))
      .catch(() => setFiles([]));
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open, orgId]);
  const make = async (b: { kind: StageDocKind; title?: string; attachmentId?: string }) => {
    setError(null);
    try {
      const d = await api.createStageDoc(orgId, b);
      setOpen(false);
      onCreated(d.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const fileList = useMemo(() => files ?? [], [files]);
  return (
    <div className="relative" ref={box}>
      <button className="text-ink-3 hover:text-ink cursor-pointer" title="New document" onClick={() => setOpen((o) => !o)}>
        +
      </button>
      {open && (
        <div className="absolute right-0 top-5 z-30 w-64 rounded-lg border border-line-2 bg-surface p-1.5 text-sm normal-case tracking-normal shadow-2xl">
          <MenuItem onClick={() => void make({ kind: "sheet", title: `Sheet ${count + 1}` })}>▦ New spreadsheet</MenuItem>
          <MenuItem onClick={() => void make({ kind: "notebook", title: `Notebook ${count + 1}` })}>📓 New notebook</MenuItem>
          <MenuItem onClick={() => void make({ kind: "markdown", title: `Document ${count + 1}` })}>📝 New Markdown document</MenuItem>
          <div className="mx-2 mb-1 mt-2 text-[11px] font-medium uppercase tracking-wider text-ink-3">Open from Files</div>
          {files === null ? (
            <div className="px-2 py-1 text-xs text-ink-3">Loading…</div>
          ) : fileList.length === 0 ? (
            <div className="px-2 py-1 text-xs leading-relaxed text-ink-3">No PDFs, spreadsheets (.xlsx, .csv), notebooks (.ipynb) or Markdown (.md) in Files yet. Upload them on the Files tab.</div>
          ) : (
            <div className="max-h-60 overflow-y-auto">
              {fileList.map((f) => (
                <MenuItem key={f.id} onClick={() => void make({ kind: docKindFor(f.name)!, attachmentId: f.id })}>
                  {DOC_ICON[docKindFor(f.name)!]} <span className="truncate">{f.name}</span>
                </MenuItem>
              ))}
            </div>
          )}
          {error && <div className="px-2 py-1 text-xs text-bad">{error}</div>}
        </div>
      )}
    </div>
  );
}

function MenuItem({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-ink-2 hover:bg-panel-2 hover:text-ink cursor-pointer">
      {children}
    </button>
  );
}
