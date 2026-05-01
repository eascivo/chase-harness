"""Negotiator Agent — refine sprint contracts into precise criteria."""

import json
import shutil

from chase.agents.base import AgentBase, AgentResult
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude, extract_json_from_text


class NegotiatorAgent(AgentBase):
    def run(self, sprint_id: int, cost: CostTracker, logger: ChaseLogger) -> AgentResult:
        contract_path = self.state.sprint_contract(sprint_id)
        negotiated_path = self.state.sprint_negotiated(sprint_id)

        if not contract_path.exists():
            logger.error(f"Sprint contract not found: {contract_path}")
            return AgentResult(success=False, cost=0.0, raw_text="", parsed_data=None)

        # Skip if already negotiated
        if negotiated_path.exists():
            logger.sprint(sprint_id, "negotiator", "Already negotiated, skipping")
            return AgentResult(success=True, cost=0.0, raw_text="", parsed_data=None)

        logger.sprint(sprint_id, "negotiator", "Refining contract...")

        contract = contract_path.read_text()
        neg_prompt = self.read_prompt("negotiator")
        handoff = self.read_latest_handoff()
        notes = self.read_notes()

        full_prompt = f"""{neg_prompt}

## Sprint Contract
{contract}

## Previous Progress
{handoff}

## NOTES
{notes}

Refine the above sprint contract into a precise, negotiable checklist. Output only JSON."""

        result = run_claude(
            full_prompt,
            cli=self.config.cli,
            max_turns=10,
            allowed_tools=["Read", "Glob", "Grep"],
            model=self.config.get_model("planner"),
            env=self.config.get_agent_env("planner"),
        )

        cost.track(result.cost, str(sprint_id), "negotiator")

        # Extract JSON
        negotiated_json = extract_json_from_text(result.result_text)

        if negotiated_json is None:
            # Non-fatal: fall back to original contract
            logger.sprint(sprint_id, "negotiator", "Failed to parse, using original contract")
            shutil.copy2(contract_path, negotiated_path)
            return AgentResult(success=True, cost=result.cost, raw_text=result.result_text, parsed_data=None)

        # Write negotiated file
        negotiated_path.write_text(json.dumps(negotiated_json, ensure_ascii=False, indent=2) + "\n")

        criteria = negotiated_json.get("negotiated_criteria", []) if isinstance(negotiated_json, dict) else []
        logger.sprint(sprint_id, "negotiator", f"Done: {len(criteria)} criteria, cost ${result.cost:.4f}")
        return AgentResult(success=True, cost=result.cost, raw_text=result.result_text, parsed_data=negotiated_json)
