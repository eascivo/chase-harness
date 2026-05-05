"""Main orchestration loop — Planner-Negotiator-Generator-Evaluator."""

from __future__ import annotations

import json
import subprocess

from chase.agents.planner import PlannerAgent
from chase.agents.negotiator import NegotiatorAgent
from chase.agents.generator import GeneratorAgent
from chase.agents.evaluator import EvaluatorAgent
from chase.config import ChaseConfig
from chase.cost import CostTracker
from chase.handoff import generate_handoff
from chase.logging import ChaseLogger
from chase.state import StateDir
from chase.trust import render_verification_card


class Orchestrator:
    def __init__(self, config: ChaseConfig, state: StateDir):
        self.config = config
        self.state = state
        self.cost = CostTracker(state.cost_file)
        self.logger = ChaseLogger(state.logs)

        self.planner = PlannerAgent(state, config)
        self.negotiator = NegotiatorAgent(state, config)
        self.generator = GeneratorAgent(state, config)
        self.evaluator = EvaluatorAgent(state, config)

    def preflight(self) -> None:
        """Validate prerequisites."""
        self.logger.info("=" * 40)
        self.logger.info("Chase starting")
        self.logger.info(f"Workspace: {self.config.workspace}")
        self.logger.info(f"CLI adapter: {self.config.cli}")
        self.logger.info(f"Budget: ${self.config.cost_limit}")
        self._log_model_config()
        self.logger.info("=" * 40)

        # Check CLI is installed
        cli_cmd = self.config.cli
        try:
            subprocess.run([cli_cmd, "--version"], capture_output=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.logger.error(f"{cli_cmd} CLI not installed")
            raise SystemExit(1)

        # Check MISSION.md
        if not self.state.mission_file.exists():
            self.logger.error("MISSION.md not found — create it in the workspace root")
            raise SystemExit(1)

        # Check git repo
        try:
            subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, timeout=5, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.logger.error("Not a git repository")
            raise SystemExit(1)

    def run(self) -> int:
        """Main orchestration loop. Returns exit code."""
        self.state.init_directories()
        self.preflight()

        # Phase 1: Planning
        contracts = self.state.existing_contracts()
        if not contracts:
            self.logger.info("Phase 1: Planner Agent decomposing goal...")
            result = self.planner.run(self.cost, self.logger)
            if not result.success:
                self.logger.error("Planner failed, exiting")
                generate_handoff(self.state, self.config, self.cost, 0, "planner_failed")
                return 1
            contracts = self.state.existing_contracts()
            self.logger.info(f"Planner generated {len(contracts)} sprints")
        else:
            self.logger.info(f"Found {len(contracts)} existing sprint contracts, skipping planner")

        # Phase 2 & 3: Sprint loop
        stale_count = 0
        prev_head = self._git_head()

        for contract_path in contracts:
            sprint_id = self._extract_sprint_id(contract_path)
            eval_path = self.state.sprint_eval(sprint_id)

            # Skip if already passed
            if eval_path.exists():
                eval_data = self._read_eval(eval_path)
                if eval_data and eval_data.get("verdict") == "PASS":
                    self.logger.info(f"Sprint {sprint_id} already passed, skipping")
                    continue

            # Budget check
            if self.cost.is_over_budget(self.config.cost_limit):
                self.logger.error(f"Budget exceeded: ${self.cost.total_cost:.4f} / ${self.config.cost_limit}")
                generate_handoff(self.state, self.config, self.cost, sprint_id, "budget_exceeded")
                return 1

            self.logger.info("=" * 40)
            self.logger.info(f"Sprint {sprint_id}: starting")
            self.logger.info("=" * 40)

            # Contract Negotiation
            self.negotiator.run(sprint_id, self.cost, self.logger)

            if not self._approval_granted():
                self.logger.error("Plan approval required. Run `chase plan`, review it, then run `chase approve`.")
                generate_handoff(self.state, self.config, self.cost, sprint_id, "approval_required")
                return 1

            # Generator-Evaluator retry loop
            retry_count = 0
            error_count = 0
            max_errors = 2  # Framework-level errors: skip after 2 consecutive
            passed = False

            while retry_count < self.config.max_retries:
                # Generator
                feedback = ""
                if retry_count > 0 and eval_path.exists():
                    eval_data = self._read_eval(eval_path)
                    if eval_data:
                        feedback = eval_data.get("feedback", "")

                gen_result = self.generator.run(sprint_id, feedback, self.cost, self.logger)
                if not gen_result.success:
                    self.logger.error(f"Sprint {sprint_id} generator failed")
                    error_count += 1
                    retry_count += 1
                    if error_count >= max_errors:
                        self.logger.error(f"Sprint {sprint_id}: {max_errors} consecutive errors, skipping")
                        break
                    continue

                # Evaluator
                eval_result = self.evaluator.run(sprint_id, self.cost, self.logger)
                if not eval_result.success or eval_result.parsed_data is None:
                    self.logger.error(f"Sprint {sprint_id} evaluator no output")
                    error_count += 1
                    retry_count += 1
                    if error_count >= max_errors:
                        self.logger.error(f"Sprint {sprint_id}: {max_errors} consecutive errors, skipping")
                        break
                    continue

                # Reset error count on successful evaluation
                error_count = 0
                self._write_verification_card(sprint_id, eval_result.parsed_data)

                # Parse results
                score = float(eval_result.parsed_data.get("score", 0))
                verdict = eval_result.parsed_data.get("verdict", "UNKNOWN")
                design_score = eval_result.parsed_data.get("design_score")

                # Compute final score
                final_score = self._compute_final_score(score, design_score)

                if design_score is not None:
                    self.logger.sprint(sprint_id, "result",
                        f"Score={score}, Design={design_score}, Final={final_score}, "
                        f"Verdict={verdict}, Retry={retry_count}/{self.config.max_retries}")
                else:
                    self.logger.sprint(sprint_id, "result",
                        f"Score={score}, Verdict={verdict}, Retry={retry_count}/{self.config.max_retries}")

                # Check pass
                if final_score >= self.config.eval_threshold:
                    passed = True
                    self.logger.sprint(sprint_id, "result", "PASSED!")
                    self._mark_eval_pass(eval_path)
                    break
                else:
                    # Legitimate FAIL (not ERROR) — reset error count, allow retries
                    error_count = 0
                    retry_count += 1
                    self.logger.sprint(sprint_id, "result",
                        f"Not passed (score={final_score} < threshold={self.config.eval_threshold}), "
                        f"retry {retry_count}/{self.config.max_retries}")

            # Stale detection: check git HEAD or uncommitted changes
            current_head = self._git_head()
            has_changes = self._git_has_changes()
            if current_head == prev_head and not has_changes:
                stale_count += 1
                self.logger.info(f"Stale count: {stale_count}/{self.config.stale_limit}")
                if stale_count >= self.config.stale_limit:
                    self.logger.error(f"Consecutive {self.config.stale_limit} sprints with no progress, stopping")
                    generate_handoff(self.state, self.config, self.cost, sprint_id, "stale")
                    return 1
            else:
                stale_count = 0
                prev_head = current_head

            if not passed:
                self.logger.sprint(sprint_id, "result", "Retries exhausted, marking as FAILED, moving to next sprint")

        # Phase 4: Final handoff
        self.logger.info("=" * 40)
        self.logger.info("All sprints processed")
        self.logger.info("=" * 40)

        passed_count, failed_count = self._summarize_results()
        self.logger.info(f"Results: {passed_count} passed, {failed_count} failed")
        self.logger.info(f"Total cost: ${self.cost.total_cost:.4f}")

        generate_handoff(self.state, self.config, self.cost, len(contracts), "completed")
        self.logger.info("Chase complete")
        return 0

    def _compute_final_score(self, score: float, design_score: float | None) -> float:
        if design_score is not None:
            return round(score * 0.7 + design_score * 0.3, 2)
        return score

    def _approval_granted(self) -> bool:
        if not self.config.require_approval:
            return True
        try:
            data = json.loads(self.state.approval_file.read_text())
            return bool(data.get("approved"))
        except Exception:
            return False

    def _write_verification_card(self, sprint_id: int, eval_data: dict) -> None:
        card = render_verification_card(sprint_id, eval_data)
        self.state.sprint_verification_card(sprint_id).write_text(card, encoding="utf-8")

    def _git_head(self) -> str:
        try:
            proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
            return proc.stdout.strip()
        except Exception:
            return "none"

    def _git_has_changes(self) -> bool:
        """Check if there are any staged, unstaged, or untracked changes."""
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            # Filter out .chase/ files (those are chase internal state)
            lines = [l for l in proc.stdout.strip().splitlines() if ".chase/" not in l]
            return len(lines) > 0
        except Exception:
            return False

    def _extract_sprint_id(self, contract_path) -> int:
        name = contract_path.stem  # e.g. "01-contract"
        return int(name.split("-")[0])

    def _log_model_config(self) -> None:
        """Log LLM configuration for each agent."""
        for agent in ("planner", "generator", "evaluator"):
            model = self.config.get_model(agent)
            api_key = getattr(self.config, f"{agent}_api_key", "") or self.config.llm_api_key
            base_url = getattr(self.config, f"{agent}_base_url", "") or self.config.llm_base_url
            parts = [f"{agent}: model="]
            parts.append(model or "(Claude default)")
            if base_url:
                parts.append(f", endpoint={base_url}")
            if api_key:
                parts.append(f", key={api_key[:8]}...")
            self.logger.info("".join(parts))

    def _read_eval(self, path) -> dict | None:
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _mark_eval_pass(self, eval_path) -> None:
        try:
            data = json.loads(eval_path.read_text())
            data["verdict"] = "PASS"
            eval_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        except Exception:
            pass

    def _summarize_results(self) -> tuple[int, int]:
        passed = failed = 0
        for eval_path in self.state.existing_evals():
            data = self._read_eval(eval_path)
            if data and data.get("verdict") == "PASS":
                passed += 1
            else:
                failed += 1
        return passed, failed
