"""Generator Agent — implement a sprint contract."""

import json
import re

from chase.agents.base import AgentBase, AgentResult
from chase.computer_use import is_web_sprint, run_browser_verification, run_interaction_test
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude, run_cli_streaming


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

        import time as _time
        _label = f"Generator/Sprint {sprint_id}"
        logger.sprint(sprint_id, "generator", f">>> {_label} working... (timeout 900s)")
        t0 = _time.monotonic()
        result = run_cli_streaming(
            full_prompt,
            cli=self.config.cli,
            max_turns=30,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            model=self.config.get_model("generator"),
            env=self.config.get_agent_env("generator"),
            cwd=str(self.config.workspace),
            timeout=900,  # Generator needs time for browser interaction
            label="Generator",
        )
        elapsed = _time.monotonic() - t0
        logger.sprint(sprint_id, "generator", f">>> Generator done ({elapsed:.1f}s)")

        cost.track(result.cost, str(sprint_id), "generator")

        # Save result report (skip if empty — prevents Evaluator from reading blank file)
        result_path = self.state.sprint_result(sprint_id)
        if result.result_text and result.result_text.strip():
            result_path.write_text(result.result_text)
        else:
            logger.sprint(
                sprint_id, "generator",
                f"Generator produced empty output.\n"
                f"Suggestions:\n"
                f"  1. Check if your CLI adapter ({self.config.cli}) is authenticated\n"
                f"  2. Try a more powerful model: CHASE_GENERATOR_MODEL=gpt-4o\n"
                f"  3. Simplify the sprint contract for sprint {sprint_id}\n"
                f"  4. Increase timeout: current is 900s",
            )
            return AgentResult(success=False, cost=result.cost, raw_text=result.result_text or "", parsed_data=None)

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

        # Check for interaction tests in the negotiated contract
        self._run_interaction_tests(sprint_id, logger)

    def _run_interaction_tests(self, sprint_id: int, logger: ChaseLogger) -> None:
        """Parse interaction tests from contract and run them if present."""
        negotiated_path = self.state.sprint_negotiated(sprint_id)
        contract_path = self.state.sprint_contract(sprint_id)
        active_contract = negotiated_path if negotiated_path.exists() else contract_path

        if not active_contract.exists():
            return

        contract_text = active_contract.read_text()
        all_steps = _parse_interaction_steps(contract_text)
        if not all_steps:
            return

        logger.sprint(sprint_id, "generator", f"Running {len(all_steps)} interaction test steps...")

        screenshot_dir = self.state.sprints / f"{sprint_id:02d}-interaction-screenshots"
        interaction_result = run_interaction_test(
            steps=all_steps,
            base_url=self.config.app_url,
            screenshot_dir=screenshot_dir,
            port=self.config.cdp_port,
        )

        # Save interaction evidence
        evidence_path = self.state.sprint_interaction_evidence(sprint_id)
        evidence_path.write_text(json.dumps(interaction_result, ensure_ascii=False, indent=2) + "\n")

        if interaction_result.get("error"):
            logger.sprint(sprint_id, "generator", f"Interaction test error: {interaction_result['error']}")
        else:
            n = len(interaction_result.get("steps", []))
            logger.sprint(sprint_id, "generator", f"Interaction test done: {n} steps, saved to {evidence_path}")


def _parse_interaction_steps(contract_text: str) -> list[dict]:
    """Extract interaction test steps from contract markdown.

    Looks for YAML blocks containing ``interaction_tests:`` and parses
    the flat list-of-dicts format without requiring PyYAML.
    """
    # Find all yaml code blocks
    yaml_blocks = re.findall(r"```ya?ml\s*\n(.*?)```", contract_text, re.DOTALL)
    if not yaml_blocks:
        return []

    for block in yaml_blocks:
        if "interaction_tests:" not in block:
            continue

        # Extract all steps entries — each is a list item starting with "  - "
        # We look for the steps: sub-key inside an interaction_tests entry
        steps: list[dict] = []
        in_steps = False
        current: dict | None = None

        for line in block.split("\n"):
            stripped = line.strip()

            # Detect "steps:" inside interaction_tests
            if stripped == "steps:":
                in_steps = True
                continue

            if not in_steps:
                continue

            # New list item starts with "- "
            if stripped.startswith("- "):
                # Flush previous
                if current is not None:
                    steps.append(current)
                current = {}
                # Parse "key: value" after "- "
                kv_text = stripped[2:]
                _parse_kv(kv_text, current)
                continue

            # Continuation key: value for current item
            if current is not None and ": " in stripped:
                _parse_kv(stripped, current)
                continue

            # End of steps block — something at same or lower indent that isn't a step
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                # Might be a new top-level key like "page:"
                if not line.startswith("      "):  # steps items are deeply indented
                    in_steps = False
                    if current is not None:
                        steps.append(current)
                        current = None

        # Flush last
        if current is not None:
            steps.append(current)

        if steps:
            return steps

    return []


def _parse_kv(text: str, target: dict) -> None:
    """Parse ``key: value`` from *text* and store in *target*.

    Handles quoted and unquoted values.  Strips surrounding quotes.
    """
    if ": " not in text:
        return
    key, _, value = text.partition(": ")
    key = key.strip()
    value = value.strip()
    # Remove surrounding quotes
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        value = value[1:-1]
    # Convert numeric strings
    if key == "wait_ms":
        try:
            value = int(value)
        except ValueError:
            pass
    target[key] = value
