"""State directory management — typed access to .chase/ paths."""

from __future__ import annotations

from pathlib import Path


class StateDir:
    """Manages the .chase/ directory structure."""

    def __init__(self, chase_dir: Path):
        self.root = chase_dir
        self.sprints = chase_dir / "sprints"
        self.handoffs = chase_dir / "handoffs"
        self.logs = chase_dir / "logs"

    @classmethod
    def for_workspace(cls, workspace: Path) -> "StateDir":
        return cls(workspace / ".chase")

    @property
    def mission_file(self) -> Path:
        return self.root.parent / "MISSION.md"

    @property
    def notes_file(self) -> Path:
        return self.root.parent / "NOTES.md"

    @property
    def cost_file(self) -> Path:
        return self.logs / "cost-tracking.json"

    @property
    def plan_preview_file(self) -> Path:
        return self.root / "plan-preview.md"

    @property
    def approval_file(self) -> Path:
        return self.root / "approved.json"

    def log_file(self, date_str: str) -> Path:
        return self.logs / f"{date_str}.log"

    # --- Sprint file paths ---

    def sprint_contract(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-contract.md"

    def sprint_negotiated(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-negotiated.md"

    def sprint_result(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-result.md"

    def sprint_eval(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-eval.json"

    def sprint_screenshot(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-screenshot.png"

    def sprint_browser_evidence(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-browser-evidence.json"

    def sprint_interaction_evidence(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-interaction-evidence.json"

    def sprint_verification_card(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-verification.md"

    def sprint_skip(self, sprint_id: int) -> Path:
        return self.sprints / f"{sprint_id:02d}-skip.json"

    @property
    def current_agent_file(self) -> Path:
        return self.root / "current-agent.json"

    @property
    def lock_file(self) -> Path:
        return self.root / "run.lock"

    def existing_contracts(self) -> list[Path]:
        return sorted(self.sprints.glob("??-contract.md"))

    def existing_evals(self) -> list[Path]:
        return sorted(self.sprints.glob("??-eval.json"))

    def latest_handoff(self) -> Path | None:
        files = sorted(self.handoffs.glob("*.md"), reverse=True)
        return files[0] if files else None

    # --- Initialization ---

    def init_directories(self) -> None:
        self.sprints.mkdir(parents=True, exist_ok=True)
        self.handoffs.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

    def init_cost_file(self) -> None:
        if not self.cost_file.exists():
            self.cost_file.write_text('{"total_cost": 0.0, "sprints": []}\n')

    # --- Readers ---

    def read_mission(self) -> str:
        if self.mission_file.exists():
            return self.mission_file.read_text()
        return ""

    def read_notes(self) -> str:
        if self.notes_file.exists():
            return self.notes_file.read_text()
        return ""

    def read_latest_handoff(self) -> str:
        path = self.latest_handoff()
        if path and path.exists():
            return path.read_text()
        return ""
