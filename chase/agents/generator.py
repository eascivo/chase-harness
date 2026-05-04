"""Generator Agent — implement a sprint contract."""

import json

from chase.agents.base import AgentBase, AgentResult
from chase.computer_use import is_web_sprint, run_browser_verification
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude


class GeneratorAgent(AgentBase):
    def run(self, sprint_id: int, feedback: str, cost: CostTracker, logger: ChaseLogger) -> AgentResult:
        # Prefer negotiated contract
        negotiated_path = self.state.sprint_negotiated(sprint_id)
        contract_path = self.state.sprint_contract(sprint_id)
        active_contract = negotiated_path if negotiated_path.exists() else contract_path

        if not active_contract.exists():
            logger.error(f"Sprint contract not found: {active_contract}")
            return AgentResult(success=False, cost=0.0, raw_text="", parsed_data=None)

        logger.sprint(sprint_id, "generator", "Implementing...")

        contract = active_contract.read_text()
        gen_prompt = self.read_prompt("generator")
        handoff = self.read_latest_handoff()
        notes = self.read_notes()

        # Build computer use section if applicable
        computer_use_section = ""
        if self.config.computer_use_enabled and self.config.app_url and is_web_sprint(contract):
            computer_use_section = f"""
## Computer Use (Browser Verification)

This is a web/UI-related sprint. After implementing, use browser automation to verify:
1. The app is running at: {self.config.app_url}
2. Navigate to the relevant page
3. Take screenshots to verify visual output
4. Check that interactive elements work correctly

Browser verification will run automatically after you complete the implementation.
"""
            logger.sprint(sprint_id, "generator", "Computer Use enabled for web sprint")

        # Build feedback section if retry
        feedback_section = ""
        if feedback:
            feedback_section = f"""
## Evaluator Feedback (fix these)

{feedback}

Fix the issues above and re-submit.
"""

        full_prompt = f"""{gen_prompt}

## Sprint Contract (negotiated)
{contract}

## Previous Progress
{handoff}

## NOTES
{notes}
{computer_use_section}{feedback_section}
## Instructions

Implement the sprint contract defined above. When done:
1. git add relevant files and commit (commit message starts with "sprint {sprint_id}:")
2. Output SPRINT RESULT report"""

        result = run_claude(
            full_prompt,
            cli=self.config.cli,
            max_turns=30,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            model=self.config.get_model("generator"),
            env=self.config.get_agent_env("generator"),
            cwd=str(self.config.workspace),
        )

        cost.track(result.cost, str(sprint_id), "generator")

        # Save result report
        result_path = self.state.sprint_result(sprint_id)
        result_path.write_text(result.result_text)

        logger.sprint(sprint_id, "generator", f"Done, cost ${result.cost:.4f}")

        # Run browser verification for web sprints
        if self.config.computer_use_enabled and self.config.app_url and is_web_sprint(contract):
            self._run_browser_verification(sprint_id, logger)

        return AgentResult(success=True, cost=result.cost, raw_text=result.result_text, parsed_data=None)

    def _run_browser_verification(self, sprint_id: int, logger: ChaseLogger) -> None:
        """Run browser automation and save evidence for the evaluator."""
        logger.sprint(sprint_id, "generator", "Running browser verification...")

        screenshot_path = self.state.sprint_screenshot(sprint_id)
        evidence = run_browser_verification(
            app_url=self.config.app_url,
            screenshot_path=screenshot_path,
            port=self.config.cdp_port,
        )

        # Save evidence JSON
        evidence_path = self.state.sprint_browser_evidence(sprint_id)
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")

        if evidence.get("error"):
            logger.sprint(sprint_id, "generator", f"Browser verification error: {evidence['error']}")
        else:
            logger.sprint(sprint_id, "generator", f"Browser evidence saved: {evidence_path}")
