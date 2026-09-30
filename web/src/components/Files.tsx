"use client";

/** Files given to agents: the per-agent library, plus small pieces used in chat. */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { AttachmentBrief, AttachmentInfo } from "@/lib/types";
import { useOrg } from "@/store/org";
import { Badge, Button, cx, ErrorNote, Spinner } from "./ui";

export const attachmentUrl = (id: string, download = false) => `/api/v1/attachments/${id}/file${download ? "?download=true" : ""}`;

export const humanSize = (n: number): string => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const EXT_ICON: Record<string, string> = { pdf: "📕", doc: "📘", docx: "📘", xls: "📗", xlsx: "📗", csv: "📗", md: "📝", txt: "📝", json: "🧾" };

function iconFor(f: { name: string; kind: string }): string {
  if (f.kind === "image") return "🖼";
  return EXT_ICON[f.name.split(".").pop()?.toLowerCase() ?? ""] ?? (f.kind === "document" ? "📄" : "📦");
}

/** Detail line, e.g. "12 pages · 34,000 characters". */
function detail(f: AttachmentInfo): string {
  const i = f.info ?? {};
  return [humanSize(f.size), i.pages ? `${i.pages} ${i.pages === 1 ? "page" : "pages"}` : "", i.chars ? `${i.chars.toLocaleString()} characters` : "", i.width ? `${i.width}×${i.height}` : "", i.sheets ? `${i.sheets} ${i.sheets === 1 ? "sheet" : "sheets"}` : ""]
    .filter(Boolean)
    .join(" · ");
}

/** Upload files and return what the server made of them (errors are reported per file). */
export async function uploadFiles(orgId: string, files: File[], agentId: string | null | undefined, onError: (msg: string) => void): Promise<AttachmentInfo[]> {
  const done: AttachmentInfo[] = [];
  for (const file of files) {
    try {
      done.push(await api.uploadAttachment(orgId, file, agentId));
    } catch (e) {
      onError(`${file.name || "file"}: ${(e as Error).message}`);
    }
  }
  return done;
}

/** A file attached to a message (or waiting to be sent). */
export function FilePill({ file, onRemove }: { file: { id: string; name: string; kind: string; status?: string; error?: string }; onRemove?: () => void }) {
  return (
    <span
      className={cx("inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs", file.status === "failed" ? "border-bad/50 bg-bad/10 text-bad" : "border-line-2 bg-panel-2 text-ink-2")}
      title={file.error || file.name}
    >
      {file.status === "processing" ? <Spinner className="size-3" /> : <span>{iconFor(file)}</span>}
      <span className="max-w-[12rem] truncate">{file.name}</span>
      {onRemove && (
        <button onClick={onRemove} className="text-ink-3 hover:text-ink cursor-pointer" aria-label={`Remove ${file.name}`}>
          ✕
        </button>
      )}
    </span>
  );
}

/** Attachments under a chat message: image thumbnails and document links. */
export function MessageAttachments({ items }: { items: AttachmentBrief[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const images = items.filter((a) => a.kind === "image");
  const docs = items.filter((a) => a.kind !== "image");
  return (
    <div className="mt-1.5 flex flex-wrap items-start gap-2">
      {images.map((a) => (
        <button key={a.id} onClick={() => setOpen(a.id)} className="overflow-hidden rounded-lg border border-line cursor-zoom-in" title={a.name}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={attachmentUrl(a.id)} alt={a.name} className="max-h-40 max-w-[14rem] object-cover" loading="lazy" />
        </button>
      ))}
      {docs.map((a) => (
        <a key={a.id} href={attachmentUrl(a.id, true)} className="inline-flex items-center gap-1.5 rounded-md border border-line-2 bg-panel-2 px-2 py-1 text-xs text-ink-2 hover:text-ink" title={`Download ${a.name}`}>
          <span>{iconFor(a)}</span>
          <span className="max-w-[14rem] truncate">{a.name}</span>
          <span className="text-ink-3">{humanSize(a.size)}</span>
        </a>
      ))}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6" onClick={() => setOpen(null)}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={attachmentUrl(open)} alt="" className="max-h-full max-w-full rounded-lg" />
        </div>
      )}
    </div>
  );
}

/**
 * One agent's library (and files shared with the whole organization): drop files
 * here, or attach them to a chat message. Documents are read, indexed and
 * searchable by the agent; images are shown to it when its model can see.
 */
export function AgentFiles({ orgId, agentId, agentName, compact }: { orgId: string; agentId: string | null; agentName?: string; compact?: boolean }) {
  const version = useOrg((s) => s.attachmentsVersion);
  const [files, setFiles] = useState<AttachmentInfo[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<AttachmentInfo | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .attachments(orgId, agentId ? { agentId, scope: "agent" } : { scope: "shared" })
      .then(setFiles)
      .catch(() => setFiles([]));
  }, [orgId, agentId]);
  useEffect(load, [load, version]);
  // Files being processed finish on their own; check back until they do.
  useEffect(() => {
    if (!files?.some((f) => f.status === "processing")) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [files, load]);

  const add = async (list: FileList | File[]) => {
    const picked = Array.from(list);
    if (!picked.length) return;
    setBusy(true);
    setError(null);
    await uploadFiles(orgId, picked, agentId, (m) => setError((e) => (e ? `${e}\n${m}` : m)));
    setBusy(false);
    load();
  };

  return (
    <div
      className="space-y-2"
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        void add(e.dataTransfer.files);
      }}
    >
      <div className={cx("flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-dashed px-3 text-sm transition-colors", compact ? "py-2" : "py-4", drag ? "border-accent bg-accent/10" : "border-line-2")}>
        <span className="min-w-0 flex-1 basis-40 text-ink-2">
          {agentId ? `Give ${agentName ?? "this agent"} documents and images` : "Files shared with everyone in this organization"} — drop them here or
        </span>
        <Button size="sm" className="shrink-0" loading={busy} onClick={() => input.current?.click()}>
          Choose files
        </Button>
        <input ref={input} type="file" multiple className="hidden" onChange={(e) => (void add(e.target.files ?? []), (e.target.value = ""))} />
      </div>
      <ErrorNote error={error} />
      {!files ? (
        <Spinner />
      ) : files.length === 0 ? (
        <div className="text-xs text-ink-3">
          {agentId ? "Nothing yet. PDF, Word, Excel, text, code and images work; the agent can read and search them, and see images if its model supports vision." : "Nothing shared yet."}
        </div>
      ) : (
        <div className="divide-y divide-line rounded-lg border border-line">
          {files.map((f) => (
            <FileRow key={f.id} f={f} onPreview={() => setPreview(f)} onDeleted={load} />
          ))}
        </div>
      )}
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function FileRow({ f, onPreview, onDeleted }: { f: AttachmentInfo; onPreview: () => void; onDeleted: () => void }) {
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="flex items-start gap-3 p-2.5 text-sm">
      {f.kind === "image" && f.status === "ready" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={attachmentUrl(f.id)} alt="" className="size-10 shrink-0 rounded object-cover" loading="lazy" />
      ) : (
        <span className="w-10 shrink-0 pt-0.5 text-center text-xl">{iconFor(f)}</span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <button onClick={onPreview} className="truncate font-medium hover:underline cursor-pointer" title="Preview">
            {f.name}
          </button>
          {f.status === "processing" && <Badge tone="info">reading…</Badge>}
          {f.status === "failed" && <Badge tone="bad">unreadable</Badge>}
          {f.kind === "other" && <Badge>stored only</Badge>}
        </div>
        <div className="text-xs text-ink-3">{detail(f)}</div>
        {f.error && <div className="text-xs text-bad">{f.error}</div>}
        {f.info?.note && <div className="text-xs text-warn">{f.info.note}</div>}
      </div>
      <a href={attachmentUrl(f.id, true)} className="text-xs text-ink-3 hover:text-ink" title="Download">
        ⤓
      </a>
      {confirm ? (
        <span className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
            Keep
          </Button>
          <Button size="sm" variant="danger" onClick={() => api.deleteAttachment(f.id).then(onDeleted)}>
            Delete
          </Button>
        </span>
      ) : (
        <button onClick={() => setConfirm(true)} className="text-ink-3 hover:text-bad cursor-pointer" title="Delete" aria-label={`Delete ${f.name}`}>
          ✕
        </button>
      )}
    </div>
  );
}

function FilePreview({ file, onClose }: { file: AttachmentInfo; onClose: () => void }) {
  const [text, setText] = useState<{ text: string; total: number } | null>(null);
  useEffect(() => {
    if (file.kind === "document" && file.status === "ready") void api.attachmentText(file.id).then(setText).catch(() => setText({ text: "(couldn't load the text)", total: 0 }));
  }, [file]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div className="flex max-h-[85dvh] w-full max-w-3xl flex-col rounded-xl border border-line-2 bg-panel shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <span className="truncate font-semibold">{file.name}</span>
          <span className="text-xs text-ink-3">{detail(file)}</span>
          <button onClick={onClose} className="ml-auto text-ink-3 hover:text-ink cursor-pointer" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {file.kind === "image" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={attachmentUrl(file.id)} alt={file.name} className="mx-auto max-h-full max-w-full rounded" />
          ) : file.kind === "document" ? (
            text ? (
              <>
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-ink-2">{text.text || "(no readable text)"}</pre>
                {text.total > text.text.length && <div className="mt-2 text-xs text-ink-3">Showing the first {text.text.length.toLocaleString()} of {text.total.toLocaleString()} characters — the agent can read all of it.</div>}
              </>
            ) : file.status === "ready" ? (
              <Spinner />
            ) : (
              <div className="text-sm text-ink-3">{file.error || "Still being read…"}</div>
            )
          ) : (
            <div className="text-sm text-ink-3">This kind of file is stored, but agents can&apos;t read it. Download it to open it.</div>
          )}
        </div>
      </div>
    </div>
  );
}
