"""Text from Word 97-2003 documents (.doc), with no dependency.

A .doc is a Compound File (an OLE2 container of streams). Its text lives in the
"WordDocument" stream, in pieces listed by the piece table (the Clx) in the table stream
("0Table" or "1Table"); each piece is either 8-bit (Windows-1252) or UTF-16. This reads the
main document's text: what Word shows in the body, without headers, footnotes or comments.

Specs: [MS-CFB] (the container) and [MS-DOC] (the FIB, the Clx and the special characters).
"""

from __future__ import annotations

import re
import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_END = 0xFFFFFFFE  # ENDOFCHAIN
_FREE = 0xFFFFFFFF


class DocError(ValueError):
    """The file isn't a .doc this can read; the message is for the user."""


class _Compound:
    """Just enough of [MS-CFB] to read a named stream."""

    def __init__(self, data: bytes) -> None:
        if data[:8] != MAGIC or len(data) < 512:
            raise DocError("not a Word 97-2003 document")
        self.data = data
        self.sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_sector = 1 << struct.unpack_from("<H", data, 0x20)[0]
        if self.sector not in (512, 4096) or self.mini_sector != 64:
            raise DocError("the file's container is damaged")
        (n_fat, dir_start, _, self.cutoff, minifat_start, n_minifat, difat_start,
         n_difat) = struct.unpack_from("<IIIIIIII", data, 0x2C)
        # The FAT's own sectors: 109 listed in the header, the rest in a chain of DIFAT sectors.
        fat_sectors = list(struct.unpack_from("<109I", data, 0x4C))
        per = self.sector // 4
        sect, seen = difat_start, 0
        while sect not in (_END, _FREE) and seen < n_difat:
            entries = struct.unpack_from(f"<{per}I", data, self._offset(sect))
            fat_sectors += entries[:-1]
            sect, seen = entries[-1], seen + 1
        fat: list[int] = []
        for s in fat_sectors[:n_fat]:
            fat += struct.unpack_from(f"<{per}I", data, self._offset(s))
        self.fat = fat
        raw = self._chain(minifat_start) if n_minifat else b""
        self.minifat = list(struct.unpack(f"<{len(raw) // 4}I", raw))
        self.entries: dict[str, tuple[int, int]] = {}
        root: tuple[int, int] | None = None
        dirs = self._chain(dir_start)
        for off in range(0, len(dirs) - 127, 128):
            name_len, kind = struct.unpack_from("<HB", dirs, off + 0x40)
            start, size = struct.unpack_from("<IQ", dirs, off + 0x74)
            if self.sector == 512:
                size &= 0xFFFFFFFF  # version 3: the high half may hold junk
            name = dirs[off:off + max(0, name_len - 2)].decode("utf-16-le", "replace")
            if kind == 5:
                root = (start, size)
            elif kind == 2:
                self.entries.setdefault(name, (start, size))
        self.ministream = self._chain(root[0])[:root[1]] if root else b""

    def _offset(self, sect: int) -> int:
        off = (sect + 1) * self.sector
        if off + self.sector > len(self.data):
            raise DocError("the file is cut short (damaged, or not fully downloaded)")
        return off

    def _chain(self, start: int) -> bytes:
        out, sect, n = bytearray(), start, 0
        while sect not in (_END, _FREE):
            if sect >= len(self.fat) or n > len(self.fat):
                raise DocError("the file's container is damaged")
            off = self._offset(sect)
            out += self.data[off:off + self.sector]
            sect, n = self.fat[sect], n + 1
        return bytes(out)

    def _mini_chain(self, start: int) -> bytes:
        out, sect, n = bytearray(), start, 0
        while sect not in (_END, _FREE):
            if sect >= len(self.minifat) or n > len(self.minifat):
                raise DocError("the file's container is damaged")
            off = sect * self.mini_sector
            out += self.ministream[off:off + self.mini_sector]
            sect, n = self.minifat[sect], n + 1
        return bytes(out)

    def stream(self, name: str) -> bytes | None:
        if name not in self.entries:
            return None
        start, size = self.entries[name]
        raw = self._mini_chain(start) if size < self.cutoff else self._chain(start)
        return raw[:size]


def _pieces(word: bytes, table: bytes, fc_clx: int, lcb_clx: int) -> list[tuple[int, int, int, bool]]:
    """The piece table: (first cp, last cp, offset in WordDocument, 8-bit?) for each piece."""
    clx, pos = table[fc_clx:fc_clx + lcb_clx], 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc: formatting we don't need
        pos += 3 + struct.unpack_from("<h", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise DocError("the document's text index is damaged")
    lcb = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + lcb]
    n = (len(plc) - 4) // 12
    if n <= 0:
        return []
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    out = []
    for i in range(n):
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * i + 2)[0]
        compressed = bool(fc & 0x40000000)
        out.append((cps[i], cps[i + 1], (fc & 0x3FFFFFFF) // 2 if compressed else fc, compressed))
    return out


def _clean(raw: str) -> str:
    """Word's special characters to plain text: fields keep only their result, tables
    become ' | '-separated rows, breaks become new lines, objects and marks are dropped."""
    out: list[str] = []
    fields: list[bool] = []  # one per open field: True once past its instructions
    for ch in raw:
        if ch == "\x13":
            fields.append(False)
        elif ch == "\x14":
            if fields:
                fields[-1] = True
        elif ch == "\x15":
            if fields:
                fields.pop()
        elif not all(fields):
            continue  # a field's instructions (HYPERLINK "...", PAGE ...)
        else:
            out.append(ch)
    text = "".join(out)
    # A table cell ends with \x07, and a row with one more \x07 (the row mark). The text alone
    # can't tell an empty cell's mark from a row mark, so the last of a run ends the row: right
    # for empty cells at a row's end, wrong (an early line break) for empty cells mid-row.
    text = re.sub("\x07{2,}", lambda m: " | " * (len(m.group()) - 2) + "\n", text)
    text = text.replace("\x07", " | ")
    text = (text.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n\n")
            .replace("\x1e", "-").replace("\x1f", "").replace("\xa0", " "))
    text = "".join(c for c in text if c in "\n\t" or c >= " ")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


def text(data: bytes) -> tuple[str, dict]:
    """The main document's text and some info, from a .doc's bytes."""
    cf = _Compound(data)
    word = cf.stream("WordDocument")
    if word is None or len(word) < 0x200:
        raise DocError("not a Word document (an Excel or PowerPoint file named .doc?)")
    ident, nfib = struct.unpack_from("<HH", word, 0)
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if ident != 0xA5EC:
        raise DocError("not a Word document")
    if nfib < 0xC1:
        raise DocError("this is a Word 6 or Word 95 document, which Pantheon can't read; "
                       "save it from Word as .docx")
    if flags & 0x0100:
        raise DocError("the Word document is password-protected")
    table = cf.stream("1Table" if flags & 0x0200 else "0Table")
    if table is None:
        raise DocError("the document's text index is missing")
    # FibBase (32 bytes), then three counted arrays: fibRgW, fibRgLw, fibRgFcLcb.
    csw = struct.unpack_from("<H", word, 32)[0]
    lw = 32 + 2 + 2 * csw
    cslw = struct.unpack_from("<H", word, lw)[0]
    ccp_text = struct.unpack_from("<i", word, lw + 2 + 12)[0]  # fibRgLw[3]
    fclcb = lw + 2 + 4 * cslw + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", word, fclcb + 33 * 8)  # fcClx, lcbClx
    parts = []
    for cp0, cp1, fc, eight_bit in _pieces(word, table, fc_clx, lcb_clx):
        if cp0 >= ccp_text:
            break
        count = min(cp1, ccp_text) - cp0
        if eight_bit:
            parts.append(word[fc:fc + count].decode("cp1252", "replace"))
        else:
            parts.append(word[fc:fc + 2 * count].decode("utf-16-le", "replace"))
    return _clean("".join(parts)), {}
