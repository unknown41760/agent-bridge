from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os


@dataclass(slots=True)
class HistoryRecord:
    task_id: str
    direction: str
    worker_alias: str | None
    model: str | None
    session_id: str | None
    duration_ms: int
    process_outcome: str
    validation_result: str
    correction_count: int
    failure_category: str | None
    workspace: str | None = None
    timestamp: str = ""

    def to_mapping(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["timestamp"]:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        return data


class HistoryStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
            path = state_home / "agent-bridge" / "history.jsonl"
        self.path = path

    def append(self, record: HistoryRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_mapping(), sort_keys=True) + "\n")

    def find_session(self, session_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        match = None
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("session_id") == session_id:
                    match = item
        return match
