"use client";

import dynamic from "next/dynamic";
import { useOrg } from "@/store/org";
import { Button } from "./ui";

export const WhiteboardEditor = dynamic(() => import("./WhiteboardEditor"), {
  ssr: false,
  loading: () => <div className="p-6 text-sm text-ink-3">Loading whiteboard…</div>,
});

/** A whiteboard, full screen over the page (opened from the office). */
export function WhiteboardOverlay({ boardId, onClose }: { boardId: string; onClose: () => void }) {
  const board = useOrg((s) => s.whiteboards[boardId]);
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-panel">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2 pt-[max(0.5rem,env(safe-area-inset-top))]">
        <span className="font-semibold">{board?.title ?? "Whiteboard"}</span>
        <span className="text-xs text-ink-3">shared with the agents · saves as you draw</span>
        <Button size="sm" className="ml-auto" onClick={onClose}>
          Done
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <WhiteboardEditor boardId={boardId} />
      </div>
    </div>
  );
}
