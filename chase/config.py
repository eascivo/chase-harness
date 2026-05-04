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

    # Computer Use (CDP)
    cdp_port: int = 9222
    computer_use_enabled: bool = False

    # CLI adapter
    cli: str = "claude"

    # Global LLM defaults
    llm_api_key: str = ""
    llm_base_url: str = ""
    model: str = ""

    # Per-agent overrides (empty = fall back to global)
    planner_model: str = ""
    planner_api_key: str = ""
    planner_base_url: str = ""

    generator_model: str = ""
    generator_api_key: str = ""
    generator_base_url: str = ""

    evaluator_model: str = ""
    evaluator_api_key: str = ""
    evaluator_base_url: str = ""

    @classmethod
    def from_env(cls, workspace: Path) -> "ChaseConfig":
        # Load .env from .chase/.env first, then workspace root .env
        load_dotenv(workspace / ".chase" / ".env")
        load_dotenv(workspace / ".env")

        home = os.environ.get("CHASE_HOME", "")
        if not home:
            home = str(Path(__file__).resolve().parent.parent)

        return cls(
            chase_home=Path(home),
            workspace=workspace,
            # Budget & iteration
            cost_limit=float(os.environ.get("CHASE_COST_LIMIT", "10000.0")),
            max_sprints=int(os.environ.get("CHASE_MAX_SPRINTS", "50")),
            max_retries=int(os.environ.get("CHASE_MAX_RETRIES", "3")),
            stale_limit=int(os.environ.get("CHASE_STALE_LIMIT", "3")),
            eval_threshold=float(os.environ.get("CHASE_EVAL_THRESHOLD", "0.7")),
            # Playwright
            app_url=os.environ.get("CHASE_APP_URL", ""),
            playwright_enabled=os.environ.get("CHASE_PLAYWRIGHT", "") == "1",
            # Computer Use
            cdp_port=int(os.environ.get("CHASE_CDP_PORT", "9222")),
            computer_use_enabled=os.environ.get("CHASE_COMPUTER_USE", "") == "1",
            # CLI adapter
            cli=os.environ.get("CHASE_CLI", "claude"),
            # Global LLM
            llm_api_key=os.environ.get("CHASE_LLM_API_KEY", ""),
            llm_base_url=os.environ.get("CHASE_LLM_BASE_URL", ""),
            model=os.environ.get("CHASE_MODEL", ""),
            # Per-agent
            planner_model=os.environ.get("CHASE_PLANNER_MODEL", ""),
            planner_api_key=os.environ.get("CHASE_PLANNER_API_KEY", ""),
            planner_base_url=os.environ.get("CHASE_PLANNER_BASE_URL", ""),
            generator_model=os.environ.get("CHASE_GENERATOR_MODEL", ""),
            generator_api_key=os.environ.get("CHASE_GENERATOR_API_KEY", ""),
            generator_base_url=os.environ.get("CHASE_GENERATOR_BASE_URL", ""),
            evaluator_model=os.environ.get("CHASE_EVALUATOR_MODEL", ""),
            evaluator_api_key=os.environ.get("CHASE_EVALUATOR_API_KEY", ""),
            evaluator_base_url=os.environ.get("CHASE_EVALUATOR_BASE_URL", ""),
        )

    @property
    def prompts_dir(self) -> Path:
        return self.chase_home / "prompts"

    def get_model(self, agent: str) -> str | None:
        """Get model for agent. Priority: agent-specific > global > None."""
        specific = getattr(self, f"{agent}_model", "")
        if specific:
            return specific
        return self.model or None

    def get_agent_env(self, agent: str) -> dict[str, str]:
        """Build env dict for an agent's subprocess call.

        Per-agent api_key/base_url override global ones.
        Maps to provider-specific env vars based on active CLI adapter.
        """
        env = os.environ.copy()

        api_key = getattr(self, f"{agent}_api_key", "") or self.llm_api_key
        base_url = getattr(self, f"{agent}_base_url", "") or self.llm_base_url

        if api_key or base_url:
            # Claude CLI reads ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            if base_url:
                env["ANTHROPIC_BASE_URL"] = base_url
            # Other CLIs may use OPENAI_API_KEY or similar
            if api_key:
                env["OPENAI_API_KEY"] = api_key
            if base_url:
                env["OPENAI_BASE_URL"] = base_url

        return env
