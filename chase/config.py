"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChaseConfig:
    chase_home: Path
    workspace: Path

    cost_limit: float = 10000.0
    max_sprints: int = 50
    max_retries: int = 3
    stale_limit: int = 3
    eval_threshold: float = 0.7

    app_url: str = ""
    playwright_enabled: bool = False

    generator_model: str = "glm-4.7"
    evaluator_model: str = "glm-4.7"
    planner_model: str = "glm-4.7"

    @classmethod
    def from_env(cls, workspace: Path) -> "ChaseConfig":
        home = os.environ.get("CHASE_HOME", "")
        if not home:
            # Fallback: assume chase package is next to this file
            home = str(Path(__file__).resolve().parent.parent)
        return cls(
            chase_home=Path(home),
            workspace=workspace,
            cost_limit=float(os.environ.get("CHASE_COST_LIMIT", "10000.0")),
            max_sprints=int(os.environ.get("CHASE_MAX_SPRINTS", "50")),
            max_retries=int(os.environ.get("CHASE_MAX_RETRIES", "3")),
            stale_limit=int(os.environ.get("CHASE_STALE_LIMIT", "3")),
            eval_threshold=float(os.environ.get("CHASE_EVAL_THRESHOLD", "0.7")),
            app_url=os.environ.get("CHASE_APP_URL", ""),
            playwright_enabled=os.environ.get("CHASE_PLAYWRIGHT", "") == "1",
            generator_model=os.environ.get("CHASE_GENERATOR_MODEL", "glm-4.7"),
            evaluator_model=os.environ.get("CHASE_EVALUATOR_MODEL", "glm-4.7"),
            planner_model=os.environ.get("CHASE_PLANNER_MODEL", "glm-4.7"),
        )

    @property
    def prompts_dir(self) -> Path:
        return self.chase_home / "prompts"
