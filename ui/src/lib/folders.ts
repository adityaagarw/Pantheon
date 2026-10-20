import { inDesktopApp } from "./engine";

/** The system's folder picker in the desktop app; null when cancelled or in a browser (where
 * the caller asks for the path as text instead). */
export async function pickFolder(title: string): Promise<string | null> {
  if (!inDesktopApp()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const dir = await open({ directory: true, multiple: false, title });
  return typeof dir === "string" ? dir : null;
}
