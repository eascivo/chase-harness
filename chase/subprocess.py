"""Subprocess management for AI CLI invocations."""

from __future__ import annotations

import json
import re
import subprocess

from chase.adapters import get_adapter, CLIResult


def run_cli(
    prompt: str,
    *,
    cli: str = "claude",
    max_turns: int = 10,
    allowed_tools: list[str] | None = None,
    model: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int = 600,
) -> CLIResult:
    """Run an AI coding CLI and return parsed result.

    Dispatches to the appropriate adapter based on `cli` name.
    """
    adapter = get_adapter(cli)
    cmd = adapter.build_command(
        prompt,
        model=model,
        max_turns=max_turns,
        allowed_tools=allowed_tools,
    )

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        raw_stdout = proc.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return CLIResult(result_text="", cost=0.0, raw_output="")

    return adapter.parse_output(raw_stdout)


# Backward-compatible alias
run_claude = run_cli


def extract_json_from_text(text: str) -> dict | list | None:
    """Extract JSON from LLM output text using regex fallback.

    Strategy:
    1. Strip markdown code blocks and try direct parse
    2. Search for JSON object: {.*} (priority — evaluator output is usually a dict)
    3. Search for JSON array: [.*] (only if non-empty)
    """
    if not text or not text.strip():
        return None

    # Strip markdown code blocks: ```json ... ``` or ``` ... ```
    stripped = re.sub(r"```(?:json)?\s*\n?", "", text)
    stripped = re.sub(r"```\s*", "", stripped)

    # Try direct parse on cleaned text
    try:
        return json.loads(stripped.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Also try original text (in case stripping broke something)
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting JSON object first (priority for evaluator output)
    for source in (stripped, text):
        match = re.search(r"\{.*\}", source, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

    # Try extracting JSON array (skip empty arrays — likely a false match)
    for source in (stripped, text):
        match = re.search(r"\[.*\]", source, re.DOTALL)
        if match:
            candidate = match.group()
            if candidate.strip() != "[]":
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

    return None
