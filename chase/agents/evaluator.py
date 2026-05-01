"""Evaluator Agent — verify sprint results."""

import json
import subprocess

from chase.agents.base import AgentBase, AgentResult
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude, extract_json_from_text


class EvaluatorAgent(AgentBase):
    def run(self, sprint_id: int, cost: CostTracker, logger: ChaseLogger) -> AgentResult:
        # Prefer negotiated contract
        negotiated_path = self.state.sprint_negotiated(sprint_id)
        contract_path = self.state.sprint_contract(sprint_id)
        result_path = self.state.sprint_result(sprint_id)
        eval_path = self.state.sprint_eval(sprint_id)

        active_contract = negotiated_path if negotiated_path.exists() else contract_path
        if not active_contract.exists():
            logger.error(f"Sprint contract not found: {active_contract}")
            return AgentResult(success=False, cost=0.0, raw_text="", parsed_data=None)

        if not result_path.exists():
            logger.error(f"Sprint result not found: {result_path}")
            return AgentResult(success=False, cost=0.0, raw_text="", parsed_data=None)

        logger.sprint(sprint_id, "evaluator", "Evaluating...")

        contract = active_contract.read_text()
        result = result_path.read_text()
        eval_prompt = self.read_prompt("evaluator")

        # Get latest git diff
        git_diff = self._get_git_diff()

        # Build Playwright section if enabled
        playwright_section = ""
        allowed_tools = ["Read", "Bash", "Glob", "Grep"]
        if self.config.playwright_enabled and self.config.app_url:
            allowed_tools.extend([
                "mcp__playwright__browser_navigate",
                "mcp__playwright__browser_click",
                "mcp__playwright__browser_screenshot",
                "mcp__playwright__browser_fill",
                "mcp__playwright__browser_select",
            ])
            playwright_section = f"""
## Playwright Browser Testing

The app is running at: {self.config.app_url}

You have browser automation tools. For UI-related criteria:
1. Navigate to the relevant page
2. Interact with elements (click, fill forms)
3. Take screenshots as evidence
4. Verify page content, element visibility, and behavior
"""
            logger.sprint(sprint_id, "evaluator", f"Playwright enabled, app at {self.config.app_url}")

        full_prompt = f"""{eval_prompt}

## Sprint Contract (negotiated)
{contract}

## Generator Result Report
{result}

## Code Changes
{git_diff}
{playwright_section}
## Instructions

Strictly evaluate the above sprint. Verify each criterion:
1. Run test_command (if specified)
2. Read relevant code to confirm implementation
3. Check edge cases and error handling
4. If Playwright is available and criteria involve UI, test in browser
5. Output JSON evaluation result (only JSON, no other text)"""

        claude_result = run_claude(
            full_prompt,
            max_turns=15,
            allowed_tools=allowed_tools,
            model=self.config.get_model("evaluator"),
            env=self.config.get_agent_env("evaluator"),
        )

        cost.track(claude_result.cost, str(sprint_id), "evaluator")

        # Extract JSON eval result
        eval_json = extract_json_from_text(claude_result.result_text)
        if eval_json is None:
            eval_json = {
                "score": 0.0,
                "verdict": "ERROR",
                "feedback": "No JSON in evaluator output",
                "criteria": [],
            }

        # Write eval file
        eval_path.write_text(json.dumps(eval_json, ensure_ascii=False, indent=2) + "\n")

        score = eval_json.get("score", 0)
        verdict = eval_json.get("verdict", "UNKNOWN")
        logger.sprint(sprint_id, "evaluator", f"Score: {score}, Verdict: {verdict}, Cost ${claude_result.cost:.4f}")

        return AgentResult(success=True, cost=claude_result.cost, raw_text=claude_result.result_text, parsed_data=eval_json)

    def _get_git_diff(self) -> str:
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD~1", "--stat"],
                capture_output=True, text=True, timeout=10,
            )
            return proc.stdout.strip() or "no diff"
        except Exception:
            return "no diff"
