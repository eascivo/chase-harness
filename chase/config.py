"""Configuration loaded from .env file and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from chase.dotenv import load_dotenv


@dataclass
class ChaseConfig:
    chase_home: Path
    workspace: Path

    # Cost & iteration
    cost_limit: float = 10000.0
    max_sprints: int = 50
    max_retries: int = 3
    stale_limit: int = 3
    eval_threshold: float = 0.7

    # Playwright
    app_url: str = ""
    playwright_enabled: bool = False

    # LLM configuration
    llm_api_key: str = ""
    llm_base_url: str = ""
    model: str = ""           # global default model
    planner_model: str = ""
    generator_model: str = ""
    evaluator_model: str = ""

    @classmethod
    def from_env(cls, workspace: Path) -> "ChaseConfig":
        # Load .env from .chase/.env first, then workspace root .env
        chase_env = workspace / ".chase" / ".env"
        root_env = workspace / ".env"
        load_dotenv(chase_env)
        load_dotenv(root_env)

        home = os.environ.get("CHASE_HOME", "")
        if not home:
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
            # LLM
            llm_api_key=os.environ.get("CHASE_LLM_API_KEY", ""),
            llm_base_url=os.environ.get("CHASE_LLM_BASE_URL", ""),
            model=os.environ.get("CHASE_MODEL", ""),
            planner_model=os.environ.get("CHASE_PLANNER_MODEL", ""),
            generator_model=os.environ.get("CHASE_GENERATOR_MODEL", ""),
            evaluator_model=os.environ.get("CHASE_EVALUATOR_MODEL", ""),
        )

    @property
    def prompts_dir(self) -> Path:
        return self.chase_home / "prompts"

    def get_model(self, agent: str) -> str | None:
        """Get model for a specific agent. Priority: agent-specific > global > None."""
        specific = getattr(self, f"{agent}_model", "")
        if specific:
            return specific
        return self.model or None

    @property
    def llm_env(self) -> dict[str, str]:
        """Environment dict to pass to claude subprocess for custom LLM config."""
        env = os.environ.copy()
        if self.llm_api_key:
            env["ANTHROPIC_API_KEY"] = self.llm_api_key
        if self.llm_base_url:
            env["ANTHROPIC_BASE_URL"] = self.llm_base_url
        return env
