"""The catalog team's exports: warehouses, stock and inbound deliveries."""

import json
from pathlib import Path


def load(data_dir):
    data = Path(data_dir)
    warehouses = json.loads((data / "warehouses.json").read_text(encoding="utf-8"))
    stock = json.loads((data / "stock.json").read_text(encoding="utf-8"))
    inbound = json.loads((data / "inbound.json").read_text(encoding="utf-8"))
    return warehouses, stock, inbound
