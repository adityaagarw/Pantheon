"""The report model that all exporters use."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

COLUMN_TYPES = ("text", "int", "money", "date")
NUMERIC_TYPES = ("int", "money")


@dataclass(frozen=True)
class Column:
    name: str
    type: str = "text"

    def __post_init__(self):
        if self.type not in COLUMN_TYPES:
            raise ValueError(f"unknown column type: {self.type}")


@dataclass
class Report:
    title: str
    columns: list
    rows: list
    generated_at: datetime

    @classmethod
    def from_dict(cls, data):
        columns = [Column(c["name"], c.get("type", "text")) for c in data["columns"]]
        rows = []
        for raw in data["rows"]:
            values = [raw.get(c.name) for c in columns] if isinstance(raw, dict) else list(raw)
            rows.append([parse_value(v, c.type) for v, c in zip(values, columns)])
        return cls(data["title"], columns, rows, datetime.fromisoformat(data["generated_at"]))


def parse_value(value, column_type):
    if value is None:
        return None
    if column_type == "int":
        return int(value)
    if column_type == "money":
        return Decimal(str(value))
    if column_type == "date":
        return value if isinstance(value, date) else date.fromisoformat(value)
    return str(value)


def format_value(value, column_type):
    """The text form of a value. All text based exporters use it."""
    if value is None:
        return ""
    if column_type == "money":
        return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if column_type == "date":
        return value.isoformat()
    return str(value)
