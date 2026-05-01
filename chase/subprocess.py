"""Subprocess management for claude CLI invocations."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


@dataclass
class ClaudeResult:
    """Parsed output from a claude -p invocation."""
    result_text: str
    cost: float
    raw_json: dict | None


def run_claude(
    prompt: str,
    *,
    max_turns: int = 10,
    allowed_tools: list[str] | None = None,
    model: str | None = None,
) -> ClaudeResult:
    """Run claude -p with --output-format json, capture and parse output."""
    cmd = ["claude", "-p", prompt]
    cmd.extend(["--max-turns", str(max_turns)])
    if allowed_tools:
        cmd.extend(["--allowed-tools", ",".join(allowed_tools)])
    cmd.append("--dangerously-skip-permissions")
    cmd.extend(["--output-format", "json"])
    if model:
        cmd.extend(["--model", model])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        raw_stdout = proc.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        return ClaudeResult(result_text="", cost=0.0, raw_json=None)

    # Parse claude's JSON output
    try:
        data = json.loads(raw_stdout)
        result_text = data.get("result", "")
        cost = data.get("total_cost_usd", data.get("cost_usd", 0)) or 0
        return ClaudeResult(result_text=str(result_text), cost=float(cost), raw_json=data)
    except (json.JSONDecodeError, ValueError):
        return ClaudeResult(result_text=raw_stdout, cost=0.0, raw_json=None)


def extract_json_from_text(text: str) -> dict | list | None:
    """Extract JSON from LLM output text using regex fallback.

    Strategy:
    1. Try json.loads on the full text
    2. Search for JSON array: [.*]
    3. Search for JSON object: {.*}
    """
    if not text or not text.strip():
        return None

    # Try direct parse
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting JSON array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try extracting JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
