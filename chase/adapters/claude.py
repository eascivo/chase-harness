"""Claude Code CLI adapter."""

from __future__ import annotations

import json

from chase.adapters import BaseAdapter, CLIResult, register_adapter


@register_adapter
class ClaudeAdapter(BaseAdapter):
    name = "claude"

    def build_command(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        cmd = ["claude", "-p", prompt]
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])
        if allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(allowed_tools)])
        cmd.append("--dangerously-skip-permissions")
        cmd.extend(["--output-format", "json"])
        if model:
            cmd.extend(["--model", model])
        return cmd

    def parse_output(self, raw_stdout: str) -> CLIResult:
        try:
            data = json.loads(raw_stdout)
            result_text = str(data.get("result", ""))
            cost = data.get("total_cost_usd", data.get("cost_usd", 0)) or 0
            return CLIResult(result_text=result_text, cost=float(cost), raw_output=raw_stdout)
        except (json.JSONDecodeError, ValueError):
            return CLIResult(result_text=raw_stdout, cost=0.0, raw_output=raw_stdout)
