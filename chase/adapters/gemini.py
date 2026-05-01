"""Google Gemini CLI adapter."""

from __future__ import annotations

import json

from chase.adapters import BaseAdapter, CLIResult, register_adapter


@register_adapter
class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def build_command(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_turns: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        cmd = ["gemini", "-p", prompt]
        cmd.extend(["--output-format", "json"])
        cmd.append("--yolo")
        if model:
            cmd.extend(["--model", model])
        # Note: gemini has no --max-turns or --allowed-tools flags
        return cmd

    def parse_output(self, raw_stdout: str) -> CLIResult:
        """Parse gemini JSON output.

        Gemini CLI outputs JSON with a 'response' field containing the text.
        """
        result_text = ""
        cost = 0.0

        try:
            data = json.loads(raw_stdout)
            if isinstance(data, dict):
                # Primary: 'response' field
                if "response" in data:
                    resp = data["response"]
                    if isinstance(resp, str):
                        result_text = resp
                    elif isinstance(resp, dict):
                        result_text = resp.get("text", resp.get("content", ""))
                    else:
                        result_text = str(resp)
                elif "text" in data:
                    result_text = data["text"]
                elif "result" in data:
                    result_text = data["result"]
                else:
                    result_text = raw_stdout

                # Try to extract cost if available
                cost = float(data.get("cost_usd", data.get("total_cost_usd", 0)) or 0)

                return CLIResult(result_text=result_text, cost=cost, raw_output=raw_stdout)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: try JSONL
        try:
            for line in raw_stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and "response" in event:
                        resp = event["response"]
                        result_text = resp if isinstance(resp, str) else str(resp)
                except json.JSONDecodeError:
                    continue

            if not result_text:
                result_text = raw_stdout
        except Exception:
            result_text = raw_stdout

        return CLIResult(result_text=result_text, cost=cost, raw_output=raw_stdout)
