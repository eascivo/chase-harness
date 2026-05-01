"""CLI adapter interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CLIResult:
    """Generic result from any AI coding CLI."""
    result_text: str
    cost: float
    raw_output: str


class BaseAdapter(ABC):
    """Base class for AI coding CLI adapters."""

    name: str = ""

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        """Build the command-line invocation for this CLI."""
        ...

    @abstractmethod
    def parse_output(self, raw_stdout: str) -> CLIResult:
        """Parse the CLI's stdout into a structured result."""
        ...


# --- Registry ---

_ADAPTERS: dict[str, type[BaseAdapter]] = {}


def register_adapter(cls: type[BaseAdapter]) -> type[BaseAdapter]:
    """Decorator to register an adapter class by name."""
    _ADAPTERS[cls.name] = cls
    return cls


def get_adapter(name: str) -> BaseAdapter:
    """Get an adapter instance by name."""
    # Ensure all built-in adapters are imported and registered
    if not _ADAPTERS:
        import chase.adapters.claude as _claude  # noqa: F401
        import chase.adapters.codex as _codex  # noqa: F401
        import chase.adapters.gemini as _gemini  # noqa: F401

    if name not in _ADAPTERS:
        available = ", ".join(_ADAPTERS.keys()) or "none"
        raise ValueError(f"Unknown CLI adapter: '{name}'. Available: {available}")
    return _ADAPTERS[name]()
