"""Evaluator Agent — verify sprint results."""

import json
import subprocess

from chase.agents.base import AgentBase, AgentResult
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude, extract_json_from_text
from chase.computer_use import is_web_sprint


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

        # Build browser evidence section
        browser_section = ""
        allowed_tools = ["Read", "Bash", "Glob", "Grep"]

        # Check for Computer Use browser evidence
        evidence_path = self.state.sprint_browser_evidence(sprint_id)
        if evidence_path.exists():
            try:
                evidence = json.loads(evidence_path.read_text())
                page_content = evidence.get("page_content", "")
                screenshot_file = evidence.get("screenshot_path", "")
                error = evidence.get("error")
                if error:
                    browser_section += f"\n## Browser Verification (Error)\n\nError: {error}\n"
                elif page_content or screenshot_file:
                    browser_section += "\n## Browser Verification Evidence\n"
                    if screenshot_file:
                        browser_section += (
                            f"\nIMPORTANT: A screenshot of the running application is available at:\n"
                            f"  {screenshot_file}\n"
                            f"You MUST use the Read tool to examine this image file and evaluate the UI visually.\n"
                            f"Check: responsive layout at 375px, color consistency, spacing rhythm, "
                            f"typography, alignment, visual polish.\n"
                        )
                    if page_content:
                        # Truncate very long page content
                        content_preview = page_content[:3000] + ("..." if len(page_content) > 3000 else "")
                        browser_section += f"\n### Page Content\n```\n{content_preview}\n```\n"
                        if screenshot_file:
                            browser_section += (
                                "\nCompare the screenshot visually against the page content above — "
                                "verify rendered output matches expected structure.\n"
                            )
                logger.sprint(sprint_id, "evaluator", "Browser evidence included in evaluation")
            except Exception as exc:
                logger.sprint(sprint_id, "evaluator", f"Failed to read browser evidence: {exc}")

        # Check for interaction test evidence
        interaction_path = self.state.sprint_interaction_evidence(sprint_id)
        if interaction_path.exists():
            try:
                interaction_data = json.loads(interaction_path.read_text())
                isteps = interaction_data.get("steps", [])
                if isteps:
                    browser_section += "\n## Interaction Test Results\n\n"
                    browser_section += "The generator executed the following interaction steps and captured screenshots:\n\n"
                    for idx, istep in enumerate(isteps):
                        browser_section += f"### Step {idx+1}: {istep.get('label', istep.get('action', ''))}\n"
                        browser_section += f"- Action: {istep.get('action')}\n"
                        if istep.get("screenshot_path"):
                            browser_section += (
                                f"- Screenshot: {istep['screenshot_path']}\n"
                                f"  You MUST read this screenshot to verify the step result.\n"
                            )
                        if istep.get("page_content"):
                            content_preview = istep["page_content"][:1000]
                            browser_section += f"- Page content: {content_preview}\n"
                        if istep.get("error"):
                            browser_section += f"- ERROR: {istep['error']}\n"
                        browser_section += "\n"
                    browser_section += (
                        "Evaluate each interaction step:\n"
                        "- Did the navigation reach the correct page?\n"
                        "- Did form inputs work correctly?\n"
                        "- Did click actions produce expected results?\n"
                        "- Is the result page visually correct (based on screenshots)?\n"
                        "- Are there any errors, empty states, or broken layouts?\n"
                    )
            except Exception as exc:
                logger.sprint(sprint_id, "evaluator", f"Failed to read interaction evidence: {exc}")

        # Build Playwright section if enabled (legacy path)
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
        else:
            playwright_section = ""

        full_prompt = f"""{eval_prompt}

## Sprint Contract (negotiated)
{contract}

## Generator Result Report
{result}

## Code Changes
{git_diff}
{browser_section}{playwright_section}
## Instructions

Strictly evaluate the above sprint. Verify each criterion:
1. Run test_command (if specified)
2. Read relevant code to confirm implementation
3. Check edge cases and error handling
4. If browser evidence is provided, use it to verify UI/web functionality
5. If Playwright is available and criteria involve UI, test in browser
6. If a screenshot path is provided, you MUST read the image with the Read tool and evaluate the UI visually (responsive layout, colors, spacing, typography, alignment, polish)
7. Include design_score and design_feedback in your JSON output when UI screenshots are available
8. Output JSON evaluation result (only JSON, no other text)"""

        claude_result = run_claude(
            full_prompt,
            cli=self.config.cli,
            max_turns=5,  # Evaluator only needs to read files + output JSON
            allowed_tools=allowed_tools,
            model=self.config.get_model("evaluator"),
            env=self.config.get_agent_env("evaluator"),
            cwd=str(self.config.workspace),
            timeout=480,
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
        elif isinstance(eval_json, list):
            # Convert criteria list to structured result
            criteria = eval_json
            passed = sum(1 for c in criteria if isinstance(c, dict) and c.get("passes"))
            total = len(criteria)
            score = round(passed / total, 2) if total > 0 else 0.0
            verdict = "PASS" if score >= 0.7 else "FAIL"
            eval_json = {
                "score": score,
                "verdict": verdict,
                "criteria": criteria,
                "feedback": "; ".join(
                    c.get("evidence", c.get("name", "")) for c in criteria
                    if isinstance(c, dict) and not c.get("passes")
                ),
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
