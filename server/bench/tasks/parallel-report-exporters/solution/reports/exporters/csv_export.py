"""CSV exporter (RFC 4180)."""

import csv
import io

from ..model import format_value
from . import register


@register("csv")
def export_csv(report):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([c.name for c in report.columns])
    for row in report.rows:
        writer.writerow([format_value(v, c.type) for v, c in zip(row, report.columns)])
    return buffer.getvalue()
