"""Cost tracking with JSON file persistence."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CostEntry:
    sprint_id: str
    phase: str
    cost: float
    timestamp: str


class CostTracker:
    def __init__(self, cost_file: Path):
        self._file = cost_file
        self._data = self._load()

    def _load(self) -> dict:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"total_cost": 0.0, "sprints": []}

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._data, indent=2, ensure_ascii=False) + "\n")

    @property
    def total_cost(self) -> float:
        return self._data.get("total_cost", 0.0)

    def track(self, cost: float, sprint_id: str, phase: str) -> None:
        if cost is None or cost == "null":
            cost = 0.0
        self._data["total_cost"] = self.total_cost + float(cost)
        self._data["sprints"].append({
            "sprint_id": sprint_id,
            "phase": phase,
            "cost": float(cost),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        self._save()

    def is_over_budget(self, limit: float) -> bool:
        return self.total_cost >= limit
