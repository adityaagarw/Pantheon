"""Markdown table exporter."""

from ..model import NUMERIC_TYPES, format_value
from . import register


def cell(text):
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def table_row(cells):
    return "| " + " | ".join(cell(c) for c in cells) + " |"


@register("markdown")
def export_markdown(report):
    lines = [f"# {report.title}", "", table_row([c.name for c in report.columns])]
    lines.append("| " + " | ".join("---:" if c.type in NUMERIC_TYPES else "---" for c in report.columns) + " |")
    for row in report.rows:
        lines.append(table_row([format_value(v, c.type) for v, c in zip(row, report.columns)]))
    if not report.rows:
        lines += ["", "_No rows._"]
    return "\n".join(lines) + "\n"
