"""Agent base class and result type."""

from __future__ import annotations

import subprocess
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

    # --- Project context injection ---

    def read_claude_md(self) -> str:
        """Read CLAUDE.md from workspace root if it exists."""
        for name in ("CLAUDE.md", "claude.md"):
            path = self.config.workspace / name
            if path.exists():
                content = path.read_text()
                if len(content) > 6000:
                    return content[:6000] + "\n... (truncated)"
                return content
        return ""

    def read_project_structure(self, max_depth: int = 3) -> str:
        """Read directory tree of the workspace (excluding common noise dirs)."""
        skip = {".git", ".venv", "venv", "__pycache__", "node_modules", ".chase",
                ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
                ".eggs", "*.egg-info", ".tox", ".nox"}
        try:
            result = subprocess.run(
                ["find", ".", "-maxdepth", str(max_depth), "-type", "f",
                 "-not", "-path", "./.git/*",
                 "-not", "-path", "./.venv/*",
                 "-not", "-path", "./node_modules/*",
                 "-not", "-path", "./__pycache__/*",
                 "-not", "-path", "./.chase/*",
                 "-not", "-path", "./.mypy_cache/*",
                 "-not", "-path", "./.pytest_cache/*"],
                capture_output=True, text=True, timeout=5,
                cwd=str(self.config.workspace),
            )
            lines = result.stdout.strip().splitlines()
            # Limit output size
            if len(lines) > 200:
                lines = lines[:200] + [f"... ({len(lines) - 200} more files)"]
            output = "\n".join(lines)
            if len(output) > 4000:
                output = output[:4000] + "\n... (truncated)"
            return output
        except Exception:
            return ""

    def read_recent_commits(self, count: int = 15) -> str:
        """Read recent git commits for context."""
        try:
            proc = subprocess.run(
                ["git", "log", f"-{count}", "--oneline", "--decorate"],
                capture_output=True, text=True, timeout=5,
                cwd=str(self.config.workspace),
            )
            return proc.stdout.strip() or ""
        except Exception:
            return ""

    def read_pyproject(self) -> str:
        """Read pyproject.toml for tech stack info."""
        for name in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod"):
            path = self.config.workspace / name
            if path.exists():
                content = path.read_text()
                if len(content) > 3000:
                    return content[:3000] + "\n... (truncated)"
                return content
        return ""

    def build_project_context(self) -> str:
        """Build a combined project context section for agent prompts."""
        sections = []

        claude_md = self.read_claude_md()
        if claude_md:
            sections.append(f"## Project Instructions (CLAUDE.md)\n{claude_md}")

        structure = self.read_project_structure()
        if structure:
            sections.append(f"## Project Structure\n```\n{structure}\n```")

        commits = self.read_recent_commits()
        if commits:
            sections.append(f"## Recent Commits\n```\n{commits}\n```")

        pyproject = self.read_pyproject()
        if pyproject:
            sections.append(f"## Project Config\n```\n{pyproject}\n```")

        return "\n\n".join(sections)
