"""OpenAI Codex CLI adapter."""

from __future__ import annotations

import json

from chase.adapters import BaseAdapter, CLIResult, register_adapter


@register_adapter
class CodexAdapter(BaseAdapter):
    name = "codex"

    def build_command(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        cmd = ["codex", "-q", prompt]
        cmd.extend(["--format", "json"])
        cmd.append("--full-auto")
        if model:
            cmd.extend(["--model", model])
        # Note: codex has no --max-turns or --allowed-tools flags
        return cmd

    def parse_output(self, raw_stdout: str) -> CLIResult:
        """Parse codex JSON output.

        Codex outputs a stream of JSON objects (one per event).
        We look for the last agent_message to get the result text.
        """
        result_text = ""
        cost = 0.0

        # Parse all lines as JSON events (handles both single JSON and JSONL)
        events = []
        for line in raw_stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not events:
            return CLIResult(result_text=raw_stdout, cost=cost, raw_output=raw_stdout)

        for event in reversed(events):
            if not isinstance(event, dict):
                continue

            # agent_message via item wrapper
            if event.get("item", {}).get("type") == "agent_message":
                result_text = event["item"].get("text", "")
                break

            # message with role=assistant, content array
            if event.get("type") == "message" and event.get("role") == "assistant":
                for content in event.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        result_text = content.get("text", "")
                        break
                if result_text:
                    break

            # Direct text field
            if "text" in event:
                result_text = event["text"]
                break

        if not result_text:
            result_text = raw_stdout

        return CLIResult(result_text=result_text, cost=cost, raw_output=raw_stdout)
