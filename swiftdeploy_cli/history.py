"""Append-only audit log. One JSON object per line."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append(path: Path, event: dict) -> None:
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows
