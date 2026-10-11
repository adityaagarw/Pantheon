"""HTML table exporter."""

import html

from ..model import NUMERIC_TYPES, format_value
from . import register


def esc(text):
    return html.escape(text, quote=True).replace("\r\n", "<br>").replace("\n", "<br>")


def tag(name, text, numeric):
    attr = ' class="num"' if numeric else ""
    return f"<{name}{attr}>{esc(text)}</{name}>"


@register("html")
def export_html(report):
    numeric = [c.type in NUMERIC_TYPES for c in report.columns]
    lines = ["<table>", f"<caption>{esc(report.title)}</caption>", "<thead>"]
    lines.append("<tr>" + "".join(tag("th", c.name, n) for c, n in zip(report.columns, numeric)) + "</tr>")
    lines += ["</thead>", "<tbody>"]
    for row in report.rows:
        cells = (tag("td", format_value(v, c.type), n) for v, c, n in zip(row, report.columns, numeric))
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines += ["</tbody>", "</table>"]
    return "\n".join(lines) + "\n"
