"""check_files: reopen deliverables the way their reader will (services/filecheck.py)."""

from __future__ import annotations

import asyncio

from app.services import filecheck
from app.tools.base import S, ToolContext, ToolError, arr, obj, resolve_in_workspace, tool

AMBIENT = ("check_files",)  # for whoever writes files (resolve.py)


@tool("check_files", "Reopen files you made the way their reader will and see what it sees: "
      "sheets and filled cells of a workbook, formula cells with no saved value (blank to "
      "pandas and graders), error cells, CSV rows with the wrong number of fields, placeholder "
      "text, links to missing files, files that don't open. Without paths: every document, "
      "sheet, CSV, JSON, Markdown and HTML file in your workdir, newest first.",
      obj({"paths": {**arr(S), "description": "Files or folders (default: your workdir)."}}),
      category="workspace")
async def check_files(args: dict, ctx: ToolContext) -> str:
    root = ctx.workdir
    paths = []
    for rel in args.get("paths") or []:
        p = resolve_in_workspace(ctx, str(rel))
        if p.is_dir():
            paths += filecheck._walk(p)
        elif p.is_file():
            paths.append(p)
        else:
            raise ToolError(f"{rel} doesn't exist")
    if not args.get("paths"):
        paths = filecheck._walk(root)
    if not paths:
        return "No documents, sheets or data files to check."
    results = await asyncio.to_thread(filecheck.check_paths, root, paths)
    bad = sum(1 for r in results if r.problems)
    more = (f"\n(Checked the newest {filecheck.MAX_FILES} of {len(paths)}; name paths for the "
            "rest.)" if len(paths) > filecheck.MAX_FILES else "")
    return (f"{len(results)} file{'s' * (len(results) != 1)} checked, "
            f"{bad or 'none'} with problems.\n" + filecheck.report(results) + more)
