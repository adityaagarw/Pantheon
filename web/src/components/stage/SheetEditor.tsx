"use client";

import { Workbook, type WorkbookInstance } from "@fortune-sheet/react";
import "@fortune-sheet/react/dist/index.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { SheetCell, SheetData } from "@/lib/types";
import { useOrg } from "@/store/org";

/* FortuneSheet's own shapes, kept loose: its types live in a nested package. */
type FCell = { v?: unknown; m?: unknown; f?: string; ct?: { t?: string; s?: { v?: string }[] } } | null;
type FSheet = { name: string; id?: string; data?: FCell[][]; celldata?: { r: number; c: number; v: FCell }[]; order?: number; row?: number; column?: number };
type Book = Record<string, Record<string, SheetCell>>;

export const colLetters = (i: number) => {
  let s = "";
  for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
  return s;
};
const parseRef = (ref: string): [number, number] => {
  const m = /^([A-Z]+)(\d+)$/.exec(ref)!;
  const col = [...m[1]].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  return [Number(m[2]) - 1, col];
};

function toFortune(data: SheetData): FSheet[] {
  return (data.sheets.length ? data.sheets : [{ name: "Sheet1", cells: {} }]).map((s, i) => {
    let rows = 60;
    let cols = 26;
    const celldata = Object.entries(s.cells).map(([ref, cell]) => {
      const [r, c] = parseRef(ref);
      rows = Math.max(rows, r + 20);
      cols = Math.max(cols, c + 5);
      const v = cell.v ?? null;
      return { r, c, v: { v, m: v === null ? "" : String(v), ...(cell.f ? { f: cell.f } : {}) } };
    });
    return { name: s.name, order: i, celldata, row: rows, column: cols };
  });
}

function cellOf(c: FCell): SheetCell | null {
  if (!c) return null;
  let v: unknown = c.v;
  if (v === undefined && c.ct?.t === "inlineStr") v = (c.ct.s ?? []).map((x) => x.v ?? "").join("");
  const out: SheetCell = {};
  if (c.f) out.f = c.f;
  if (v !== undefined && v !== null && v !== "") out.v = v as SheetCell["v"];
  return out.f || out.v !== undefined ? out : null;
}

function fromFortune(sheets: FSheet[]): Book {
  const book: Book = {};
  for (const s of sheets) {
    const cells: Record<string, SheetCell> = {};
    (s.data ?? []).forEach((row, r) =>
      row?.forEach((c, col) => {
        const cell = cellOf(c);
        if (cell) cells[`${colLetters(col)}${r + 1}`] = cell;
      }),
    );
    book[s.name] = cells;
  }
  return book;
}

const same = (a: SheetCell | undefined, b: SheetCell | undefined) => (a?.f ?? "") === (b?.f ?? "") && (a?.f ? true : String(a?.v ?? "") === String(b?.v ?? ""));

/** The live spreadsheet: saves the user's edits as cell changes and applies agents' changes. */
export default function SheetEditor({ docId, onSelection }: { docId: string; onSelection?: (text: string) => void }) {
  const [initial, setInitial] = useState<FSheet[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<WorkbookInstance>(null);
  const known = useRef<Book>({});
  const names = useRef<string[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef<FSheet[] | null>(null);
  const ev = useOrg((s) => s.docs[docId]);
  const loadedVersion = useRef(0);

  useEffect(() => {
    let alive = true;
    api
      .stageDoc(docId)
      .then((d) => {
        if (!alive) return;
        const data = d.data as SheetData;
        known.current = Object.fromEntries(data.sheets.map((s) => [s.name, { ...s.cells }]));
        names.current = data.sheets.map((s) => s.name);
        loadedVersion.current = d.version;
        setInitial(toFortune(data));
      })
      .catch((e) => setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [docId]);

  // Save what the user changed, a moment after they stop.
  const save = () => {
    const sheets = latest.current;
    if (!sheets) return;
    const book = fromFortune(sheets);
    const changes: Record<string, Record<string, SheetCell | null>> = {};
    for (const [name, cells] of Object.entries(book)) {
      const before = known.current[name] ?? {};
      const diff: Record<string, SheetCell | null> = {};
      for (const [r, c] of Object.entries(cells)) if (!same(before[r], c)) diff[r] = c;
      for (const r of Object.keys(before)) if (!cells[r]) diff[r] = null;
      if (Object.keys(diff).length) changes[name] = diff;
    }
    const order = sheets.map((s) => s.name);
    const renamed = order.join("\u0000") !== names.current.join("\u0000");
    if (!Object.keys(changes).length && !renamed) return;
    known.current = book;
    names.current = order;
    void api.saveSheetCells(docId, changes, renamed ? order : undefined).catch((e) => setError((e as Error).message));
  };

  // Agents' (and other windows') changes arrive live.
  useEffect(() => {
    if (!ev || !ref.current || ev.version <= loadedVersion.current) return;
    loadedVersion.current = ev.version;
    if (ev.source === "user" || !ev.changes) return;
    const api_ = ref.current;
    const all = api_.getAllSheets() as FSheet[];
    for (const [name, cells] of Object.entries(ev.changes)) {
      let index = all.findIndex((s) => s.name === name);
      if (index < 0) {
        api_.addSheet();
        const now = api_.getAllSheets() as FSheet[];
        index = now.length - 1;
        api_.setSheetName(name, { index });
      }
      const mine = (known.current[name] ??= {});
      for (const [r, cell] of Object.entries(cells)) {
        if (cell && same(mine[r], cell)) continue;
        const [row, col] = parseRef(r);
        if (cell) mine[r] = cell;
        else delete mine[r];
        try {
          api_.setCellValue(row, col, cell ? (cell.f ?? cell.v ?? null) : null, { index });
        } catch {
          /* outside the grid: shown after a reload */
        }
      }
    }
  }, [ev]);

  const hooks = useMemo(
    () => ({
      afterSelectionChange: (_sheetId: string, sel: { row: number[]; column: number[] }) => {
        const [r1, r2] = sel.row;
        const [c1, c2] = sel.column;
        const a = `${colLetters(c1)}${r1 + 1}`;
        const b = `${colLetters(c2)}${r2 + 1}`;
        onSelection?.(a === b ? `cell ${a} selected` : `cells ${a}:${b} selected`);
      },
    }),
    [onSelection],
  );

  if (error) return <div className="p-6 text-sm text-bad">{error}</div>;
  if (!initial) return <div className="p-6 text-sm text-ink-3">Loading spreadsheet…</div>;
  return (
    <div className="h-full w-full">
      <Workbook
        ref={ref}
        data={initial as never}
        hooks={hooks as never}
        showToolbar
        showFormulaBar
        allowEdit
        onChange={(d: unknown) => {
          latest.current = d as FSheet[];
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(save, 700);
        }}
      />
    </div>
  );
}
