"""Load and save the task list."""

import json
import os
from pathlib import Path


def data_path():
    return Path(os.environ.get("TODO_FILE", str(Path.home() / ".todo.json")))


def load():
    path = data_path()
    if not path.exists():
        return {"next_id": 1, "tasks": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(data):
    path = data_path()
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
