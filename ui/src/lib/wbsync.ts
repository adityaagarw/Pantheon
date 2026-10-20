/**
 * Keeps a whiteboard's picture (the office's 3D stand shows it) up to date when no
 * editor has the board open: shapes agents asked for are drawn in with Excalidraw's
 * converter and saved, and a fresh picture is uploaded. Loaded on demand (it pulls in
 * Excalidraw's code, not its editor).
 */

import { convertToExcalidrawElements, exportToBlob, getSceneVersion } from "@excalidraw/excalidraw";
import { api } from "@/lib/api";

if (typeof window !== "undefined") (window as unknown as { EXCALIDRAW_ASSET_PATH: string }).EXCALIDRAW_ASSET_PATH = "/excalidraw/";

type Elements = Parameters<typeof getSceneVersion>[0];
type Skeletons = Parameters<typeof convertToExcalidrawElements>[0];
type Files = Parameters<typeof exportToBlob>[0]["files"];

/** Boards open in an editor: the editor keeps those up to date itself. */
export const editing = new Set<string>();
const busy = new Set<string>();

/** A PNG data: URL of the board, or null when it has nothing on it. */
export async function picture(elements: readonly unknown[], background: string, files: unknown): Promise<string | null> {
  const live = (elements as { isDeleted?: boolean }[]).filter((e) => !e.isDeleted) as unknown as Elements;
  if (!live.length) return null;
  const blob = await exportToBlob({
    elements: live,
    appState: { viewBackgroundColor: background, exportBackground: true },
    files: (files ?? {}) as Files,
    mimeType: "image/png",
    maxWidthOrHeight: 1024,
  });
  return new Promise<string>((res) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result));
    r.readAsDataURL(blob);
  });
}

/** Draw in agents' queued shapes and upload a picture of the board as it now is. */
export async function syncBoard(boardId: string): Promise<void> {
  if (editing.has(boardId) || busy.has(boardId)) return;
  busy.add(boardId);
  try {
    let scene = await api.whiteboard(boardId);
    if (scene.pendingCount > 0) {
      const { pending } = await api.claimWhiteboardPending(boardId);
      if (pending.length) {
        const drawn = convertToExcalidrawElements(pending as Skeletons, { regenerateIds: false });
        scene = await api.saveWhiteboard(boardId, { elements: [...scene.elements, ...(drawn as unknown[])] });
      }
    }
    const background = String(scene.appState.viewBackgroundColor ?? "#ffffff");
    const url = await picture(scene.elements, background, scene.files);
    if (url) await api.saveWhiteboardThumbnail(boardId, url, scene.version);
  } finally {
    busy.delete(boardId);
  }
}
