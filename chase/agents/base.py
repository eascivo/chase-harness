"""Agent base class and result type."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chase.config import ChaseConfig
from chase.state import StateDir


@dataclass
class AgentResult:
    success: bool
    cost: float
    raw_text: str
    parsed_data: dict | list | None


class AgentBase:
    """Common prompt-building and file-reading logic for all agents."""

    def __init__(self, state: StateDir, config: ChaseConfig):
        self.state = state
        self.config = config

    def read_prompt(self, name: str) -> str:
        path = self.config.prompts_dir / f"{name}.md"
        if path.exists():
            return path.read_text()
        return ""

    def read_mission(self) -> str:
        return self.state.read_mission()

    def read_notes(self) -> str:
        return self.state.read_notes()

    def read_latest_handoff(self) -> str:
        return self.state.read_latest_handoff()
