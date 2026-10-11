"""Plain text exporter with fixed width columns. Use it as an example."""

from ..model import NUMERIC_TYPES, format_value
from . import register


@register("text")
def export_text(report):
    header = [c.name for c in report.columns]
    body = [[format_value(v, c.type) for v, c in zip(row, report.columns)] for row in report.rows]
    widths = [max([len(header[i])] + [len(r[i]) for r in body]) for i in range(len(header))]

    def line(cells):
        parts = []
        for cell, width, column in zip(cells, widths, report.columns):
            parts.append(cell.rjust(width) if column.type in NUMERIC_TYPES else cell.ljust(width))
        return "  ".join(parts).rstrip()

    lines = [report.title, "=" * len(report.title), line(header), line(["-" * w for w in widths])]
    lines += [line(r) for r in body]
    return "\n".join(lines) + "\n"
