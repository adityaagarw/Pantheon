"use client";

import { useEffect, useState } from "react";
import { AgentAvatar } from "@/components/agent";
import { Markdown } from "@/components/Markdown";
import { cx, Empty, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { Artifact } from "@/lib/types";
import { useOrg } from "@/store/org";

type Listing = Awaited<ReturnType<typeof api.files>>;

export default function FilesPage() {
  const orgId = useOrg((s) => s.orgId)!;
  const agents = useOrg((s) => s.agents);
  const version = useOrg((s) => s.artifactsVersion);
  const [dir, setDir] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);

  useEffect(() => {
    api.files(orgId, dir).then(setListing).catch(() => setListing(null));
  }, [orgId, dir, version]);
  useEffect(() => {
    api.artifacts(orgId).then(setArtifacts).catch(() => {});
  }, [orgId, version]);
  useEffect(() => {
    if (!file) return;
    setContent(null);
    fetch(api.rawFileUrl(orgId, file))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => setContent(t.length > 400_000 ? `${t.slice(0, 400_000)}\n…(truncated)` : t))
      .catch((e) => setContent(`Could not load: ${e.message}`));
  }, [orgId, file, version]);

  const crumbs = dir ? dir.split("/") : [];
  const isImage = file && /\.(png|jpe?g|gif|svg|webp)$/i.test(file);
  const isMd = file && /\.(md|markdown)$/i.test(file);

  return (
    <div className="flex h-full">
      <aside className={cx("w-full shrink-0 flex-col border-r border-line bg-panel md:flex md:w-80", file ? "hidden" : "flex")}>
        <div className="border-b border-line px-3 py-2.5 text-xs text-ink-3">
          <button className="hover:text-ink cursor-pointer" onClick={() => setDir("")}>
            workspace
          </button>
          {crumbs.map((c, i) => (
            <span key={i}>
              {" / "}
              <button className="hover:text-ink cursor-pointer" onClick={() => setDir(crumbs.slice(0, i + 1).join("/"))}>
                {c}
              </button>
            </span>
          ))}
          {listing?.root && <div className="mt-1 truncate font-mono text-[10px]" title={listing.root}>{listing.root}</div>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {!listing ? (
            <Spinner />
          ) : listing.entries?.length === 0 ? (
            <div className="p-3 text-sm text-ink-3">Empty folder.</div>
          ) : (
            listing.entries?.map((e) => (
              <button
                key={e.path}
                onClick={() => (e.type === "dir" ? setDir(e.path) : setFile(e.path))}
                className={cx("flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm cursor-pointer", file === e.path ? "bg-panel-2" : "hover:bg-panel-2/60")}
              >
                <span className="text-ink-3">{e.type === "dir" ? "▸" : "·"}</span>
                <span className="truncate">{e.name}</span>
              </button>
            ))
          )}
        </div>
        <div className="max-h-[40%] overflow-y-auto border-t border-line p-2">
          <div className="px-1 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-ink-3">Recently written by agents</div>
          {artifacts.slice(0, 40).map((a) => (
            <button key={a.id} onClick={() => setFile(a.path)} className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-panel-2 cursor-pointer">
              <AgentAvatar agent={a.agentId ? agents[a.agentId] : null} size={16} />
              <span className="truncate">{a.path}</span>
              <span className="ml-auto shrink-0 text-ink-3">{timeAgo(a.createdAt)}</span>
            </button>
          ))}
        </div>
      </aside>
      <section className={cx("min-w-0 flex-1 overflow-auto md:block", file ? "block" : "hidden")}>
        {!file ? (
          <Empty title="Pick a file" icon="📁">Agents work in this folder. Everything they write shows up here.</Empty>
        ) : (
          <div className="p-3 md:p-5">
            <div className="mb-3 flex items-center gap-3">
              <button onClick={() => setFile(null)} className="text-sm text-ink-2 hover:text-ink md:hidden cursor-pointer">
                ‹ Back
              </button>
              <span className="min-w-0 truncate font-mono text-sm">{file}</span>
              <a className="text-xs text-info hover:underline" href={api.rawFileUrl(orgId, file)} target="_blank" rel="noreferrer">
                open raw
              </a>
            </div>
            {isImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={api.rawFileUrl(orgId, file)} alt={file} className="max-w-full rounded border border-line" />
            ) : content === null ? (
              <Spinner />
            ) : isMd ? (
              <div className="max-w-3xl rounded-lg border border-line bg-panel p-5">
                <Markdown>{content}</Markdown>
              </div>
            ) : (
              <pre className="overflow-auto rounded-lg border border-line bg-panel p-4 font-mono text-xs leading-relaxed text-ink-2">
                {content.split("\n").map((l, i) => (
                  <div key={i} className="flex">
                    <span className="mr-4 w-10 shrink-0 select-none text-right text-ink-3">{i + 1}</span>
                    <span className="whitespace-pre-wrap break-all">{l}</span>
                  </div>
                ))}
              </pre>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
