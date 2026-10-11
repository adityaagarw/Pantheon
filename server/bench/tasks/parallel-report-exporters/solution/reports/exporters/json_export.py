"""JSON exporter. The output loads again with Report.from_dict."""

import json

from ..model import format_value
from . import register


def json_value(value, column_type):
    if value is None:
        return None
    if column_type == "int":
        return int(value)
    return format_value(value, column_type)


@register("json")
def export_json(report):
    data = {
        "title": report.title,
        "generated_at": report.generated_at.isoformat(),
        "columns": [{"name": c.name, "type": c.type} for c in report.columns],
        "rows": [{c.name: json_value(v, c.type) for v, c in zip(row, report.columns)} for row in report.rows],
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
