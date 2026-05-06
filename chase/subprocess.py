"""Subprocess management for AI CLI invocations."""

from __future__ import annotations

import json
import logging
import re
import subprocess

from chase.adapters import get_adapter, CLIResult

logger = logging.getLogger(__name__)


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
    except subprocess.TimeoutExpired:
        return CLIResult(result_text="", cost=0.0, raw_output="[TIMEOUT]")
    except (subprocess.CalledProcessError, FileNotFoundError):
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

    # Last resort: fix common LLM JSON mistakes and retry
    logger.debug("LLM JSON quirks fix attempted")
    for fix_fn in (_fix_llm_json_quirks,):
        for source in (stripped, text):
            fixed = fix_fn(source)
            try:
                return json.loads(fixed)
            except (json.JSONDecodeError, ValueError):
                pass
            for pattern in (r"\{.*\}", r"\[.*\]"):
                match = re.search(pattern, fixed, re.DOTALL)
                if match:
                    candidate = match.group()
                    if candidate.strip() not in ("{}", "[]"):
                        # Fix smart/curly quotes in the extracted candidate only
                        candidate = candidate.replace("\u201c", '\\"').replace("\u201d", '\\"')
                        candidate = candidate.replace("\u2018", "'").replace("\u2019", "'")
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass

    return None


def _fix_llm_json_quirks(text: str) -> str:
    """Fix common LLM output issues in JSON text."""
    # Fix unescaped double quotes inside JSON string values using a state machine
    text = _fix_unescaped_quotes_in_json(text)
    return text


def _fix_unescaped_quotes_in_json(text: str) -> str:
    """Fix unescaped double quotes inside JSON string values by escaping them.

    Uses a character-by-character parser that tracks JSON string boundaries:
    opening quotes start a string, and a quote followed by a JSON structural
    char (:, ,, ], }) closes it. Any other quote inside a string is unescaped
    and gets escaped.
    """
    result = []
    i = 0
    in_string = False
    while i < len(text):
        c = text[i]
        if c == "\\" and in_string and i + 1 < len(text):
            result.append(c)
            result.append(text[i + 1])
            i += 2
            continue
        if c == '"' and not in_string:
            in_string = True
            result.append(c)
            i += 1
            continue
        if c == '"' and in_string:
            # Check if this is a closing quote: next non-whitespace should be
            # a JSON structural character
            rest = text[i + 1 :].lstrip()
            if not rest or rest[0] in ":,]}":
                in_string = False
                result.append(c)
            else:
                # Unescaped quote inside a string value — escape it
                logger.warning("Fixed unescaped quotes in JSON (heuristic)")
                result.append('\\"')
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)
