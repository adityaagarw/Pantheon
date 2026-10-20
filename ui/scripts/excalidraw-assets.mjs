// Copies Excalidraw's fonts into public/ so the whiteboard works offline
// (window.EXCALIDRAW_ASSET_PATH = "/excalidraw/"). Runs on install and build.
import { cpSync, existsSync } from "node:fs";

const from = "node_modules/@excalidraw/excalidraw/dist/prod/fonts";
const to = "public/excalidraw/fonts";
if (existsSync(from)) {
  cpSync(from, to, { recursive: true });
  console.log("excalidraw fonts ->", to);
}
