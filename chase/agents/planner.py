"""Planner Agent — decompose MISSION.md into sprint contracts."""

import json

from chase.agents.base import AgentBase, AgentResult
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude, extract_json_from_text


class PlannerAgent(AgentBase):
    def run(self, cost: CostTracker, logger: ChaseLogger) -> AgentResult:
        logger.info("Starting Planner Agent...")

        mission = self.read_mission()
        if not mission:
            logger.error("MISSION.md not found or empty")
            return AgentResult(success=False, cost=0.0, raw_text="", parsed_data=None)

        planner_prompt = self.read_prompt("planner")
        notes = self.read_notes()

        # Check for existing sprint contracts
        existing = ""
        contracts = self.state.existing_contracts()
        if contracts:
            existing = "Existing sprint contracts:\n"
            for c in contracts:
                existing += f"  {c.name}: {c.read_text()[:200]}\n"

        full_prompt = f"""{planner_prompt}

## MISSION
{mission}

## NOTES
{notes}

{existing}

Output a JSON array of sprint contracts. Only output JSON, no other text."""

        result = run_claude(
            full_prompt,
            max_turns=10,
            allowed_tools=["Read", "Glob", "Grep"],
            model=self.config.get_model("planner"),
            env=self.config.get_agent_env("planner"),
        )

        cost.track(result.cost, "0", "planner")

        # Extract JSON sprint list
        sprints_json = extract_json_from_text(result.result_text)
        if sprints_json is None:
            logger.error("Failed to parse planner output")
            # Save raw output for debugging
            debug_file = self.state.sprints / "planner-raw-output.txt"
            debug_file.write_text(result.result_text)
            return AgentResult(success=False, cost=result.cost, raw_text=result.result_text, parsed_data=None)

        # Write sprint contract files
        if isinstance(sprints_json, list):
            sprint_count = self._write_contracts(sprints_json)
        else:
            logger.error("Planner output is not a JSON array")
            return AgentResult(success=False, cost=result.cost, raw_text=result.result_text, parsed_data=None)

        logger.info(f"Planner done: {sprint_count} sprint contracts, cost ${result.cost:.4f}")
        return AgentResult(success=True, cost=result.cost, raw_text=result.result_text, parsed_data=sprints_json)

    def _write_contracts(self, sprints: list) -> int:
        count = 0
        for idx, sprint in enumerate(sprints, start=1):
            sid = sprint.get("id", idx)
            path = self.state.sprint_contract(sid)
            path.write_text(json.dumps(sprint, ensure_ascii=False, indent=2) + "\n")
            count += 1
        return count
