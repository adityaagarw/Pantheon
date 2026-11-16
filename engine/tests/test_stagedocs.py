"""Stage documents: spreadsheets, PDFs and notebooks shared by the user and agents."""

from __future__ import annotations

import io
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.models import Turn
from app.services import notebook_kernel
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script, tool_results


def tiny_pdf(pages: list[str]) -> bytes:
    """A real (if minimal) PDF with one line of text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for i, text in enumerate(pages):
        page, content = 3 + i * 2, 4 + i * 2
        kids.append(f"{page} 0 R")
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                    f"/Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


async def _done(agent_id: str) -> bool:
    return any(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id))


async def test_spreadsheet_agent_and_user_work_together(app_client):
    org = await make_org(app_client)
    ana = await make_agent(app_client, org["id"], "Ana")
    prompts = script(
        ana["id"],
        call("sheet_new", title="Budget"),
        call("sheet_write", doc="Budget", start="A1",
             rows=[["Item", "Cost"], ["Rent", 1200], ["Food", "450"], ["Total", "=SUM(B2:B3)"]]),
        call("sheet_write", doc="Budget", cells=[{"cell": "C4", "formula": "B4/12"},
                                                 {"cell": "D1", "formula": "=1/0"}]),
        call("sheet_read", doc="Budget"),
        say("done"), then=say("ok"))
    await dm(app_client, org["id"], "Ana", "make a budget")
    await wait_for(lambda: _done(ana["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "4 cells" in results[0] or "Created spreadsheet 'Budget'" in results[0]
    assert "Formula errors: D1 #DIV/0!" in results[2]
    table = results[3]
    assert "| 4 | Total | 1650 | 137.5 |" in table and "B4 =SUM(B2:B3) → 1650" in table

    doc = (await app_client.get(f"/api/v1/orgs/{org['id']}/docs")).json()[0]
    # The user edits in the browser; formulas that depend on it follow.
    # (The grid sends typed numbers as text; they're stored as numbers.)
    r = await app_client.put(f"/api/v1/docs/{doc['id']}/cells", json={
        "changes": {"Sheet1": {"B3": {"v": "550"}, "D1": None}}})
    assert r.status_code == 200
    full = (await app_client.get(f"/api/v1/docs/{doc['id']}")).json()
    cells = full["data"]["sheets"][0]["cells"]
    assert cells["B3"]["v"] == 550 and cells["B4"]["v"] == 1750 and "D1" not in cells

    # xlsx out and back in, formulas intact.
    x = await app_client.get(f"/api/v1/docs/{doc['id']}/export")
    wb = load_workbook(io.BytesIO(x.content))
    assert wb["Sheet1"]["B4"].value == "=SUM(B2:B3)"
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("budget.xlsx", x.content, "application/octet-stream")})).json()
    again = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "sheet", "attachmentId": up["id"]})).json()
    assert again["data"]["sheets"][0]["cells"]["B4"]["f"] == "=SUM(B2:B3)"
    assert again["data"]["sheets"][0]["cells"]["B4"]["v"] == 1750


async def test_csv_import():
    from app.services.stagedocs import sheets_from_file

    sheets = sheets_from_file("t.csv", b"name,score\nann,3\nben,4.5\n")
    assert sheets[0]["cells"]["B3"] == {"v": 4.5} and sheets[0]["cells"]["A1"] == {"v": "name"}
    wb = Workbook()
    wb.active["A1"] = 2
    wb.active["A2"] = "=A1*10"
    buf = io.BytesIO()
    wb.save(buf)
    cells = sheets_from_file("t.xlsx", buf.getvalue())[0]["cells"]
    assert cells["A2"]["f"] == "=A1*10"


async def test_pdf_reading_together(app_client):
    org = await make_org(app_client)
    leo = await make_agent(app_client, org["id"], "Leo")
    await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("report.pdf", tiny_pdf(["Introduction", "Revenue grew 12 percent"]),
                 "application/pdf")})
    prompts = script(
        leo["id"],
        call("pdf_open", file="report.pdf"),
        call("pdf_read", doc="report.pdf", page=2),
        call("pdf_show", doc="report.pdf", page=2, highlight="Revenue grew 12 percent",
             note="the key number"),
        say("look at page 2"), then=say("ok"))
    await dm(app_client, org["id"], "Leo", "walk me through the report")
    await wait_for(lambda: _done(leo["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "Opened 'report.pdf'" in results[0]
    assert "Revenue grew 12 percent" in results[1] and "page 2 of 2" in results[1]
    doc = (await app_client.get(f"/api/v1/orgs/{org['id']}/docs")).json()[0]
    data = (await app_client.get(f"/api/v1/docs/{doc['id']}")).json()["data"]
    assert data["page"] == 2 and data["highlights"][0]["by"] == "Leo"
    pdf = await app_client.get(f"/api/v1/docs/{doc['id']}/export")
    assert pdf.content.startswith(b"%PDF")


async def test_notebook_cells_run_in_the_kernel(app_client, monkeypatch):
    ran: list[tuple[str, str]] = []

    async def fake_execute(notebook: str, code: str, timeout: float = 300):
        ran.append((notebook, code))
        if "boom" in code:
            return {"count": len(ran), "status": "error", "outputs": [
                {"type": "error", "ename": "NameError", "evalue": "name 'boom' is not defined",
                 "traceback": "NameError: name 'boom' is not defined"}]}
        return {"count": len(ran), "status": "ok", "outputs": [
            {"type": "stream", "name": "stdout", "text": "hello\n"},
            {"type": "image", "data": "iVBORw0KGgo="}, {"type": "result", "text": "42"}]}

    monkeypatch.setattr(notebook_kernel, "execute", fake_execute)
    org = await make_org(app_client)
    kai = await make_agent(app_client, org["id"], "Kai")
    prompts = script(
        kai["id"],
        call("notebook_new", title="Analysis"),
        call("notebook_add_cell", doc="Analysis", source="# Sales", type="markdown"),
        call("notebook_add_cell", doc="Analysis", source="print('hello'); 6*7", run=True),
        call("notebook_add_cell", doc="Analysis", source="boom", run=True),
        call("notebook_read", doc="Analysis"),
        say("done"), then=say("ok"))
    await dm(app_client, org["id"], "Kai", "analyse")
    await wait_for(lambda: _done(kai["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "hello" in results[2] and "42" in results[2]
    assert "NameError" in results[3]
    assert "cell 1 (markdown)" in results[4] and "[a chart/image]" in results[4]
    ran_msgs = [m for m in prompts[-1] if m.type == "tool"]
    assert getattr(ran_msgs[2], "content", None) is not None

    doc = (await app_client.get(f"/api/v1/orgs/{org['id']}/docs")).json()[0]
    assert [c for _, c in ran] == ["print('hello'); 6*7", "boom"]
    assert all(nb == doc["id"] for nb, _ in ran)

    # .ipynb out and back in.
    nb = json.loads((await app_client.get(f"/api/v1/docs/{doc['id']}/export")).content)
    assert nb["nbformat"] == 4 and nb["cells"][0]["cell_type"] == "markdown"
    assert nb["cells"][1]["outputs"][1]["data"]["image/png"] == "iVBORw0KGgo="
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("a.ipynb", json.dumps(nb).encode(), "application/json")})).json()
    again = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "notebook", "attachmentId": up["id"]})).json()
    assert [c["type"] for c in again["data"]["cells"]] == ["markdown", "code", "code"]
    assert again["data"]["cells"][1]["outputs"][0]["text"] == "hello\n"

    # The user edits, moves and runs cells from the page.
    cid = again["data"]["cells"][2]["id"]
    await app_client.patch(f"/api/v1/docs/{again['id']}/cells/{cid}", json={"source": "1+1"})
    await app_client.patch(f"/api/v1/docs/{again['id']}/cells/{cid}", json={"to": 1})
    r = await app_client.post(f"/api/v1/docs/{again['id']}/cells/{cid}/run")
    assert r.status_code == 202
    await wait_for(lambda: _cell_ran(app_client, again["id"]), msg="user's cell ran")


async def _cell_ran(client, doc_id: str) -> bool:
    cells = (await client.get(f"/api/v1/docs/{doc_id}")).json()["data"]["cells"]
    return cells[0]["source"] == "1+1" and cells[0]["status"] == "ok"


async def test_markdown_agent_and_user_write_together(app_client):
    org = await make_org(app_client)
    ana = await make_agent(app_client, org["id"], "Ana")
    prompts = script(
        ana["id"],
        call("md_new", title="Plan", text="# Plan\n\n- [ ] research\n- [ ] write"),
        call("md_write", doc="Plan", find="- [ ] research", replace="- [x] research"),
        call("md_write", doc="Plan", find="nowhere", replace="x"),
        call("md_write", doc="Plan", append="## Notes\n\nDone soon."),
        call("md_read", doc="Plan"),
        say("done"), then=say("ok"))
    await dm(app_client, org["id"], "Ana", "plan it")
    await wait_for(lambda: _done(ana["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "Created Markdown document 'Plan'" in results[0]
    assert "isn't in the document" in results[2]
    assert "- [x] research" in results[4] and results[4].rstrip().endswith("Done soon.")

    doc = (await app_client.get(f"/api/v1/orgs/{org['id']}/docs")).json()[0]
    assert doc["kind"] == "markdown"
    # The user saves on top of the version they have; an old version is a conflict.
    r = await app_client.put(f"/api/v1/docs/{doc['id']}/text",
                             json={"text": "# Mine", "base": doc["version"]})
    assert r.status_code == 200
    stale = await app_client.put(f"/api/v1/docs/{doc['id']}/text",
                                 json={"text": "# Old", "base": doc["version"]})
    assert stale.status_code == 409 and stale.json()["detail"]["doc"]["data"]["text"] == "# Mine"
    x = await app_client.get(f"/api/v1/docs/{doc['id']}/export")
    assert x.text == "# Mine" and ".md" in x.headers["content-disposition"]

    # A .md from Files opens as a Markdown document.
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("notes.md", b"# Notes\n\nhello", "text/markdown")})).json()
    opened = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "markdown", "attachmentId": up["id"]})).json()
    assert opened["title"] == "notes" and opened["data"]["text"].startswith("# Notes")


async def test_word_documents_open_as_markdown(app_client):
    """A .docx or .doc from Files opens on the Stage as Markdown: headings, lists, tables."""
    org = await make_org(app_client)
    fixtures = Path(__file__).parent / "fixtures"

    async def open_word(name: str, mime: str) -> str:
        up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
            "file": (name, (fixtures / name).read_bytes(), mime)})).json()
        r = await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
            "kind": "markdown", "attachmentId": up["id"]})
        assert r.status_code == 201, r.text
        assert r.json()["title"] == name.rsplit(".", 1)[0]
        return r.json()["data"]["text"]

    docx = await open_word("sample.docx", "application/vnd.openxmlformats-officedocument."
                                          "wordprocessingml.document")
    assert docx.startswith("# Supply agreement\n\n## Terms\n\n")
    assert "- First point\n- Second point" in docx
    assert "| Region | Units |\n| --- | --- |\n| North | 1200 |" in docx
    doc = await open_word("sample.doc", "application/msword")
    assert "| Region | Units |\n| --- | --- |\n| North | 1200 |" in doc
    assert doc.endswith("Signed in Pune.")

    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("locked.doc", (fixtures / "locked.doc").read_bytes(), "application/msword")})).json()
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "markdown", "attachmentId": up["id"]})
    assert r.status_code == 400 and "password-protected" in r.text


async def test_the_users_selection_reaches_the_agent(app_client):
    """The user highlights a passage in an open document and asks about "this": the agent asks
    word_selection (or reads with selection: true) and gets the text with paragraph numbers."""
    from app.services import stage as stage_svc

    org = await make_org(app_client)
    fixtures = Path(__file__).parent / "fixtures"
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ana = await make_agent(app_client, org["id"], "Ana", tools=[
        {"name": n} for n in ("doc_list", "md_new", "md_read", "md_write", "word_new",
                              "word_read", "word_write", "word_selection", "stage_list")])
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("sample.docx", (fixtures / "sample.docx").read_bytes(), docx_mime)})).json()
    doc = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "word", "attachmentId": up["id"]})).json()
    md = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs",
                                json={"kind": "markdown", "title": "Notes"})).json()
    await app_client.put(f"/api/v1/docs/{md['id']}/text",
                         json={"text": "# Plans\n\nShip on Friday.\n\nHold the review.\n"})

    def highlight(kind, doc_id, title, text, tool, **args):
        """One turn: the user moves their highlight in the editor, then the agent asks."""
        def step(messages, tools):
            stage_svc.set_attention(ana["id"], {"kind": kind, "docId": doc_id, "title": title,
                                                "text": text})
            return call(tool, **args)
        return step

    passage = "The warranty period is 24 months.\nFirst point\nSecond point"
    prose = ('they\'ve selected paragraphs 3-5 (as word_read numbers them), so "this" means '
             f'this passage:\n"""\n{passage}\n"""\n')
    lines = 'they\'ve selected line 3, so "this" means this passage:\n"""\nShip on Friday.\n"""\n'
    prompts = script(ana["id"],
                     call("stage_list"),  # this Stage has documents and no pages
                     call("word_selection"),  # nothing is open on the Stage yet
                     highlight("word", doc["id"], "sample", "they're reading it",
                               "word_selection"),
                     highlight("word", doc["id"], "sample", prose, "word_selection"),
                     highlight("word", doc["id"], "sample", prose, "word_read",
                               doc="sample", selection=True),
                     highlight("markdown", md["id"], "Notes", lines, "word_selection"),
                     highlight("markdown", md["id"], "Notes", lines, "md_read",
                               doc="Notes", selection=True),
                     highlight("markdown", md["id"], "Notes", "they're reading it",
                               "word_selection", doc="sample"))
    await dm(app_client, org["id"], ana["id"], "what did I highlight?")
    await wait_for(lambda: _done(ana["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "No pages" in results[0] and "Read them with doc_list" in results[0], results[0]
    assert "No selection" in results[1] and "no document open" in results[1]
    assert "has not highlighted anything" in results[2]
    assert "paragraphs 3-5" in results[3] and passage in results[3]
    assert results[4] == results[3], results[4]  # word_read gets there the same way
    assert "Markdown document 'Notes'" in results[5], results[5]
    assert "md_read with selection: true" in results[5]
    assert "line 3" in results[6] and "Ship on Friday." in results[6]
    assert "No selection in 'sample'" in results[7] and "open instead" in results[7]

    # Whoever reads documents is given the tool, and can turn it off like any other tool.
    from app.core.db import SessionLocal
    from app.models import Agent, Org
    from app.tools.resolve import resolve_tools

    bo = await make_agent(app_client, org["id"], "Bo", tools=[{"name": "md_read"}])
    async with SessionLocal() as s:
        o, a = await s.get(Org, org["id"]), await s.get(Agent, bo["id"])
    assert {"word_read", "word_selection"} <= set((await resolve_tools(o, a)).tools)
    stage_svc.clear_attention(ana["id"])


async def test_word_documents_on_the_stage(app_client):
    """A .docx (or .doc) opens as a Word document: the editor loads and saves the file, and
    agents read and change it with word_* tools, formatting kept."""
    from docx import Document as Docx

    from app.core.db import SessionLocal
    from app.models import Agent, Org
    from app.tools.resolve import resolve_tools

    org = await make_org(app_client)
    fixtures = Path(__file__).parent / "fixtures"
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("sample.docx", (fixtures / "sample.docx").read_bytes(), docx_mime)})).json()
    doc = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "word", "attachmentId": up["id"]})).json()
    assert doc["kind"] == "word" and doc["title"] == "sample"

    r = await app_client.get(f"/api/v1/docs/{doc['id']}/word")
    assert r.status_code == 200 and r.headers["x-doc-version"] == str(doc["version"])
    original = r.content
    # The editor saves the whole file on top of the version it has; an old version conflicts.
    saved = await app_client.put(f"/api/v1/docs/{doc['id']}/word?base={doc['version']}",
                                 content=original)
    assert saved.status_code == 200
    stale = await app_client.put(f"/api/v1/docs/{doc['id']}/word?base={doc['version']}",
                                 content=original)
    assert stale.status_code == 409
    junk = await app_client.put(f"/api/v1/docs/{doc['id']}/word", content=b"not a docx")
    assert junk.status_code == 400

    # A .doc opens as .docx; a blank one can be made too.
    up2 = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("sample.doc", (fixtures / "sample.doc").read_bytes(), "application/msword")})).json()
    old = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "word", "attachmentId": up2["id"], "title": "Old contract"})).json()
    text = "\n".join(p.text for p in Docx(io.BytesIO((await app_client.get(
        f"/api/v1/docs/{old['id']}/word")).content)).paragraphs)
    assert "Supply agreement" in text and "Signed in Pune." in text
    blank = await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={"kind": "word",
                                                                          "title": "Memo"})
    assert blank.status_code == 201

    # An agent with the Markdown tools gets the Word ones too, and edits keep formatting.
    ana = await make_agent(app_client, org["id"], "Ana", tools=[
        {"name": n} for n in ("md_new", "md_read", "md_write")])
    async with SessionLocal() as s:
        o, a = await s.get(Org, org["id"]), await s.get(Agent, ana["id"])
    assert {"word_new", "word_read", "word_write"} <= set((await resolve_tools(o, a)).tools)
    prompts = script(
        ana["id"],
        call("word_read", doc="sample"),
        call("word_write", doc="sample", find="24 months", replace="36 months"),
        call("word_write", doc="sample", append="## Payment\n- Net 30", after=2),
        call("word_write", doc="sample", find="nowhere", replace="x"),
        call("word_read", doc="sample"),
        say("done"), then=say("ok"))
    await dm(app_client, org["id"], "Ana", "update the terms")
    await wait_for(lambda: _done(ana["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "[1] (Title) Supply agreement" in results[0] and "[table 1]" in results[0]
    assert "isn't in the document" in results[3]
    assert "[3] (Heading 2) Payment" in results[4] and "[4] (List Bullet) Net 30" in results[4]
    assert "36 months" in results[4]
    final = Docx(io.BytesIO((await app_client.get(f"/api/v1/docs/{doc['id']}/word")).content))
    assert final.paragraphs[0].style.name == "Title" and final.tables[0].cell(1, 1).text == "1200"

    x = await app_client.get(f"/api/v1/docs/{doc['id']}/export")
    assert x.headers["content-type"] == docx_mime and ".docx" in x.headers["content-disposition"]
    assert (await app_client.delete(f"/api/v1/docs/{doc['id']}")).status_code in (200, 204)


def _commented_docx(extended: bool) -> bytes:
    """A .docx with a comment on a range across two paragraphs, a reply to it, and a comment
    in a table; with ``extended``, a commentsExtended part as Word and the Stage editor write
    one (which records replies and resolved comments)."""
    from docx import Document as Docx
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    doc = Docx()
    doc.add_paragraph("Skills")
    langs = doc.add_paragraph("Languages: Python, Rust, Dart")
    tools = doc.add_paragraph("Tools: Git, Docker")
    first = doc.add_comment([langs.runs[0], tools.runs[0]], text="Remove rust and dart.",
                            author="You")
    reply = doc.add_comment(tools.runs[0], text="Will do.", author="Marcus")
    cell = doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    cell.add_run("Pune")
    doc.add_comment(cell.runs[0], text="Wrong city.", author="You")
    if extended:
        ids = []
        for i, c in enumerate((first, reply)):
            p = c.paragraphs[-1]._p
            pid = f"0000000{i + 1}"
            p.set("{http://schemas.microsoft.com/office/word/2010/wordml}paraId", pid)
            ids.append(pid)
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w15:commentsEx '
               'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
               f'<w15:commentEx w15:paraId="{ids[0]}" w15:done="0"/>'
               f'<w15:commentEx w15:paraId="{ids[1]}" w15:paraIdParent="{ids[0]}" w15:done="0"/>'
               '</w15:commentsEx>')
        part = Part(PackURI("/word/commentsExtended.xml"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml."
                    "commentsExtended+xml", xml.encode(), doc.part.package)
        doc.part.relate_to(part, "http://schemas.microsoft.com/office/2011/relationships/"
                                 "commentsExtended")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_comments_are_read_with_where_they_sit_and_resolved():
    from docx import Document as Docx

    from app.services import worddocs

    for extended in (True, False):
        doc = Docx(io.BytesIO(_commented_docx(extended)))
        found = {c.text: c for c in worddocs.comments(doc)}
        top = found["Remove rust and dart."]
        assert top.paragraphs == [2, 3] and top.author == "You"
        assert top.anchor == "Languages: Python, Rust, Dart Tools: Git, Docker"
        assert found["Wrong city."].in_table and found["Wrong city."].anchor == "Pune"
        shown = worddocs.describe_comments(list(found.values()))
        assert f'[comment {top.id}] You, at paragraphs 2-3 on "Languages' in shown
        assert "a table" in shown
        if extended:  # the reply is shown under what it answers, not as a comment of its own
            assert found["Will do."].parent == top.id
            assert "    reply from Marcus: Will do." in shown and "open" in shown
        worddocs.resolve(doc, top.id)
        buf = io.BytesIO()
        doc.save(buf)
        again = Docx(io.BytesIO(buf.getvalue()))
        left = worddocs.comments(again)
        if extended:  # marked done, as Word does: it stays in the file
            assert next(c for c in left if c.id == top.id).done
        else:  # nowhere to record it: the comment and its marks go
            assert top.id not in {c.id for c in left}
            assert f'w:id="{top.id}"' not in again.element.body.xml
        assert "Remove rust" not in worddocs.describe_comments(left)
        assert "Wrong city." in worddocs.describe_comments(left)
        try:
            worddocs.resolve(again, "99")
            raise AssertionError("resolved a comment that isn't there")
        except worddocs.DocError as e:
            assert "no comment 99" in str(e)


async def test_an_agent_reads_the_users_comments_and_resolves_them(app_client):
    org = await make_org(app_client)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("cv.docx", _commented_docx(True), docx_mime)})).json()
    made = await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "word", "attachmentId": up["id"]})
    assert made.status_code == 201, made.text
    ana = await make_agent(app_client, org["id"], "Ana", tools=[
        {"name": n} for n in ("word_read", "word_write")])
    prompts = script(
        ana["id"],
        call("word_read", doc="cv"),
        call("word_write", doc="cv", find=", Rust, Dart", replace="", resolve="0"),
        call("word_read", doc="cv"),
        say("done"), then=say("ok"))
    await dm(app_client, org["id"], "Ana", "do what my comments say")
    await wait_for(lambda: _done(ana["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "[2] Languages: Python, Rust, Dart [comment 0]" in results[0]
    assert "[3] Tools: Git, Docker [comment 0]" in results[0]
    assert "[comment 0] You, at paragraphs 2-3" in results[0]
    assert "comment 0 is resolved" in results[1]
    assert "[2] Languages: Python\n" in results[2] and "Remove rust" not in results[2]
    assert "Wrong city." in results[2]

def test_an_agent_answers_a_comment_in_its_thread():
    """An answer belongs under the comment it answers — in a document Word already threaded and
    in one that never had a reply, where the thread part itself has to be written."""
    from docx import Document as Docx

    from app.services import worddocs

    for extended in (True, False):
        doc = Docx(io.BytesIO(_commented_docx(extended)))
        asked = next(c for c in worddocs.comments(doc) if c.text.startswith("Remove rust"))
        made = worddocs.reply(doc, asked.id, "Cut to Python only.", "Ana")
        assert made.parent == asked.id, made
        buf = io.BytesIO()
        doc.save(buf)
        left = worddocs.comments(Docx(io.BytesIO(buf.getvalue())))
        got = next((c for c in left if c.text == "Cut to Python only."), None)
        assert got is not None and got.parent == asked.id, f"extended={extended}: {left}"
        assert got.author == "Ana" and got.paragraphs == asked.paragraphs
        assert "reply from Ana: Cut to Python only." in worddocs.describe_comments(left)
        assert next(c for c in left if c.id == asked.id).done is False  # answering is not closing

    doc = Docx(io.BytesIO(_commented_docx(True)))
    for mistake, note, why in (("99", "thanks", "no comment 99"), ("0", "  ", "say something")):
        try:
            worddocs.reply(doc, mistake, note, "Ana")
            raise AssertionError(f"answering {mistake!r} must fail")
        except worddocs.DocError as e:
            assert why in str(e)


async def test_an_agent_reads_the_comments_and_answers_one(app_client):
    """word_read with comments:true is the work list on its own, and word_write answers in the
    thread beside the edit that settles it — the user's comment stays open for them to close."""
    from docx import Document as Docx

    from app.services import worddocs

    org = await make_org(app_client)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    up = (await app_client.post(f"/api/v1/orgs/{org['id']}/attachments", files={
        "file": ("cv.docx", _commented_docx(True), docx_mime)})).json()
    doc = (await app_client.post(f"/api/v1/orgs/{org['id']}/docs", json={
        "kind": "word", "attachmentId": up["id"]})).json()
    ana = await make_agent(app_client, org["id"], "Ana", tools=[
        {"name": n} for n in ("word_read", "word_write")])

    def answer(messages: list, tools: list):
        """Answer whichever comment word_read just reported: the id is the document's to give."""
        import re

        cid = re.search(r"\[comment (\d+)\]", str(messages[-1].content)).group(1)
        return call("word_write", doc="cv", find=", Rust, Dart", replace="", reply=cid,
                    text="Cut to Python only.")

    prompts = script(ana["id"],
                     call("word_read", doc="cv", comments=True),
                     answer,
                     call("word_read", doc="cv"),
                     say("done"), then=say("ok"))
    await dm(app_client, org["id"], "Ana", "answer my comments")
    await wait_for(lambda: _done(ana["id"]), timeout=30, msg="agent finished")
    results = tool_results(prompts[-1])
    assert "[comment 0] You, at paragraphs 2-3" in results[0], results[0]
    assert 'on "Languages: Python' in results[0], results[0]
    assert "answered what comment 0 asked" in results[1], results[1]
    assert "[2] Languages: Python [comment 0]" in results[2], results[2]
    assert "reply from Ana: Cut to Python only." in results[2], results[2]

    left = worddocs.comments(Docx(io.BytesIO(
        (await app_client.get(f"/api/v1/docs/{doc['id']}/word")).content)))
    said = next(c for c in left if c.text == "Cut to Python only.")
    asked = next(c for c in left if c.text.startswith("Remove rust"))
    assert said.author == "Ana" and said.parent == asked.id, "not in the user's thread"
    assert asked.done is False, "answering is not resolving: the user closes their own comments"
