"""Planner Agent — decompose MISSION.md into sprint contracts."""

import json

from chase.agents.base import AgentBase, AgentResult
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import (
    run_claude, run_cli_streaming,
    extract_json_from_text, extract_json_from_text_with_retry,
)


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

        # Build rich project context
        project_ctx = self.build_project_context()

        # Build existing progress from evaluations
        progress_ctx = self._build_progress_context()

        full_prompt = f"""{planner_prompt}

## MISSION
{mission}

{project_ctx}

## NOTES
{notes}

{existing}
{progress_ctx}
Output a JSON array of sprint contracts. Only output JSON, no other text."""

        import time
        logger.info(">>> Planner working... (timeout 300s)")
        t0 = time.monotonic()
        result = run_cli_streaming(
            full_prompt,
            cli=self.config.cli,
            max_turns=10,
            allowed_tools=["Read", "Glob", "Grep"],
            model=self.config.get_model("planner"),
            env=self.config.get_agent_env("planner"),
            cwd=str(self.config.workspace),
            timeout=300,
            label="Planner",
        )
        elapsed = time.monotonic() - t0
        logger.info(f">>> Planner done ({elapsed:.1f}s)")

        cost.track(result.cost, "0", "planner")

        # Save raw output for debugging
        debug_file = self.state.sprints / "planner-raw-output.txt"
        self.state.sprints.mkdir(parents=True, exist_ok=True)

        # Check for timeout — directory already ensured above
        if "[TIMEOUT]" in result.raw_output:
            debug_file.write_text(result.result_text or result.raw_output)
            logger.error(
                f"Planner timed out (300s). Raw output saved to {debug_file}. "
                "Try: shorten MISSION.md or check CLI auth."
            )
            return AgentResult(success=False, cost=result.cost, raw_text=result.result_text, parsed_data=None)

        # Extract JSON sprint list (with auto-retry)
        sprints_json, retry_text = extract_json_from_text_with_retry(
            result.result_text,
            retry_fn=run_cli_streaming,
            retry_kwargs=dict(
                prompt=full_prompt,
                cli=self.config.cli,
                max_turns=10,
                allowed_tools=["Read", "Glob", "Grep"],
                model=self.config.get_model("planner"),
                env=self.config.get_agent_env("planner"),
                cwd=str(self.config.workspace),
                timeout=300,
                label="Planner",
            ),
        )
        if sprints_json is None:
            debug_file.write_text(retry_text)
            logger.error(
                f"Planner failed to produce valid JSON after retry.\n"
                f"Raw output: {debug_file}\n"
                f"Suggestions:\n"
                f"  1. Shorten MISSION.md to under 2KB\n"
                f"  2. Try a more powerful model: CHASE_MODEL=gpt-4o chase plan\n"
                f"  3. Check if your CLI adapter ({self.config.cli}) is authenticated\n"
                f"  4. Read raw output to see what the model actually returned"
            )
            return AgentResult(
                success=False, cost=result.cost,
                raw_text=retry_text, parsed_data=None,
            )

        # Write sprint contract files
        if isinstance(sprints_json, list):
            sprint_count = self._write_contracts(sprints_json)
        else:
            logger.error("Planner output is not a JSON array")
            return AgentResult(success=False, cost=result.cost, raw_text=result.result_text, parsed_data=None)

        logger.info(f"Planner done: {sprint_count} sprint contracts, cost ${result.cost:.4f}")
        return AgentResult(success=True, cost=result.cost, raw_text=result.result_text, parsed_data=sprints_json)

    def _build_progress_context(self) -> str:
        """Build context from existing sprint evaluations for incremental planning."""
        evals = self.state.existing_evals()
        if not evals:
            return ""

        lines = ["## Existing Progress (evaluations from previous runs)"]
        for eval_path in evals:
            try:
                data = json.loads(eval_path.read_text())
                sid = eval_path.stem.split("-")[0]
                verdict = data.get("verdict", "?")
                score = data.get("score", "?")
                title = data.get("title", f"Sprint {sid}")
                lines.append(f"  Sprint {sid} [{verdict}] score={score}: {title}")
                if verdict == "FAIL" and data.get("feedback"):
                    lines.append(f"    feedback: {data['feedback'][:200]}")
            except Exception:
                pass

        lines.append("")
        lines.append("NOTE: Skip sprints that already PASS. Adjust failed sprints based on feedback.")
        return "\n".join(lines)

    def _write_contracts(self, sprints: list) -> int:
        count = 0
        for idx, sprint in enumerate(sprints, start=1):
            sid = sprint.get("id", idx)
            path = self.state.sprint_contract(sid)
            path.write_text(json.dumps(sprint, ensure_ascii=False, indent=2) + "\n")
            count += 1
        return count
