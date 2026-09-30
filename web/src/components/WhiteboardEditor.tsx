"use client";

/**
 * A shared Excalidraw whiteboard. Your edits are saved (merged per element on
 * the server); edits from agents or other viewers are reconciled in live; and
 * shapes agents asked for are converted into real Excalidraw elements here.
 * Load with next/dynamic (ssr: false).
 */

import "@excalidraw/excalidraw/index.css";
import {
  CaptureUpdateAction,
  convertToExcalidrawElements,
  Excalidraw,
  exportToBlob,
  getSceneVersion,
  reconcileElements,
} from "@excalidraw/excalidraw";
import type { ExcalidrawImperativeAPI } from "@excalidraw/excalidraw/types";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { WhiteboardScene } from "@/lib/types";
import { useOrg } from "@/store/org";

// Fonts are served from our own origin (copied on install), not a CDN.
if (typeof window !== "undefined") (window as unknown as { EXCALIDRAW_ASSET_PATH: string }).EXCALIDRAW_ASSET_PATH = "/excalidraw/";

type Elements = Parameters<typeof getSceneVersion>[0];
type Skeletons = Parameters<typeof convertToExcalidrawElements>[0];

export default function WhiteboardEditor({ boardId }: { boardId: string }) {
  const [scene, setScene] = useState<WhiteboardScene | null>(null);
  const [error, setError] = useState<string | null>(null);
  const excali = useRef<ExcalidrawImperativeAPI | null>(null);
  const sent = useRef(-1); // scene version last saved
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const thumbAt = useRef(0);
  const info = useOrg((s) => s.whiteboards[boardId]);

  useEffect(() => {
    let alive = true;
    api
      .whiteboard(boardId)
      .then((s) => alive && setScene(s))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [boardId]);

  const thumbnail = useCallback(async () => {
    const x = excali.current;
    if (!x || Date.now() - thumbAt.current < 4000) return;
    thumbAt.current = Date.now();
    const elements = x.getSceneElements();
    if (!elements.length) return;
    try {
      const blob = await exportToBlob({
        elements,
        appState: { ...x.getAppState(), exportBackground: true },
        files: x.getFiles(),
        mimeType: "image/png",
        maxWidthOrHeight: 1024,
      });
      const url = await new Promise<string>((res) => {
        const r = new FileReader();
        r.onload = () => res(String(r.result));
        r.readAsDataURL(blob);
      });
      await api.saveWhiteboardThumbnail(boardId, url);
    } catch {
      /* thumbnails are best-effort */
    }
  }, [boardId]);

  const save = useCallback(() => {
    const x = excali.current;
    if (!x) return;
    const elements = x.getSceneElementsIncludingDeleted();
    const v = getSceneVersion(elements);
    if (v === sent.current) return;
    sent.current = v;
    const appState = x.getAppState();
    api
      .saveWhiteboard(boardId, { elements: elements as unknown[], appState: { viewBackgroundColor: appState.viewBackgroundColor }, files: x.getFiles() as unknown as Record<string, unknown> })
      .then(() => {
        setError(null);
        void thumbnail();
      })
      .catch((e: Error) => setError(`Not saved: ${e.message}`));
  }, [boardId, thumbnail]);

  // Someone else changed the board (an agent drew, or another viewer): merge it in.
  const pull = useCallback(async () => {
    const x = excali.current;
    if (!x) return;
    const remote = await api.whiteboard(boardId);
    const merged = reconcileElements(
      x.getSceneElementsIncludingDeleted(),
      remote.elements as Parameters<typeof reconcileElements>[1],
      x.getAppState(),
    );
    x.updateScene({ elements: merged, captureUpdate: CaptureUpdateAction.NEVER });
    sent.current = getSceneVersion(merged as unknown as Elements);
    if (remote.pendingCount > 0) {
      const { pending } = await api.claimWhiteboardPending(boardId);
      if (pending.length) {
        const drawn = convertToExcalidrawElements(pending as Skeletons, { regenerateIds: false });
        x.updateScene({ elements: [...x.getSceneElementsIncludingDeleted(), ...drawn], captureUpdate: CaptureUpdateAction.IMMEDIATELY });
        x.scrollToContent(undefined, { fitToContent: true, animate: true });
        save();
      }
    }
  }, [boardId, save]);

  const version = info?.version;
  useEffect(() => {
    if (excali.current && version !== undefined) void pull().catch(() => {});
  }, [version, pull]);

  useEffect(
    () => () => {
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        save();
      }
    },
    [save],
  );

  if (error && !scene) return <div className="p-6 text-sm text-bad">{error}</div>;
  if (!scene) return <div className="p-6 text-sm text-ink-3">Loading whiteboard…</div>;
  return (
    <div className="relative h-full w-full">
      <Excalidraw
        excalidrawAPI={(x) => {
          excali.current = x;
          sent.current = getSceneVersion(scene.elements as Elements);
          // Agents may have drawn while nobody had the board open.
          if (scene.pendingCount > 0) setTimeout(() => void pull().catch(() => {}), 50);
        }}
        initialData={{
          elements: scene.elements as Elements,
          appState: { viewBackgroundColor: String(scene.appState.viewBackgroundColor ?? "#ffffff") },
          files: scene.files as never,
          scrollToContent: true,
        }}
        onChange={() => {
          if (saveTimer.current) clearTimeout(saveTimer.current);
          saveTimer.current = setTimeout(save, 700);
        }}
        UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false } }}
      />
      {error && <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded bg-bad px-3 py-1 text-xs text-white">{error}</div>}
    </div>
  );
}
