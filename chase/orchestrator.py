"""Main orchestration loop — Planner-Negotiator-Generator-Evaluator."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone

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

        # Python version check
        if sys.version_info < (3, 10):
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            self.logger.warning(
                f"Python {ver} detected. Chase recommends 3.10+. Some features may not work."
            )

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

    def run(self, *, force: bool = False) -> int:
        """Main orchestration loop. Returns exit code.

        Manages run.lock and signal cleanup internally so Ray mode is also protected.
        """
        self.state.init_directories()
        if not self._acquire_lock(force=force):
            return 1
        try:
            self._install_signal_handlers()
            self.preflight()
            return self._execute()
        finally:
            self._release_lock()
            self._clear_current_agent()

    def _install_signal_handlers(self) -> None:
        """Ensure SIGTERM/SIGINT release the lock on abrupt termination."""
        orig_sigterm = signal.getsignal(signal.SIGTERM)
        orig_sigint = signal.getsignal(signal.SIGINT)

        def _handler(signum, frame):
            self._release_lock()
            self._clear_current_agent()
            # Chain to previous handler
            if signum == signal.SIGTERM and callable(orig_sigterm):
                orig_sigterm(signum, frame)
            elif signum == signal.SIGINT and callable(orig_sigint):
                orig_sigint(signum, frame)
            else:
                raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def _execute(self) -> int:
        """Core orchestration logic (lock already held)."""
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

        # Record base branch for sprint isolation
        base_branch = self._get_current_branch()

        # Phase 2 & 3: Sprint loop
        stale_count = 0
        prev_head = self._git_head()

        for contract_path in contracts:
            sprint_id = self._extract_sprint_id(contract_path)
            eval_path = self.state.sprint_eval(sprint_id)

            # Skip if explicitly skipped
            skip_path = self.state.sprint_skip(sprint_id)
            if skip_path.exists():
                self.logger.info(f"Sprint {sprint_id} marked as SKIP, skipping")
                continue

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

            # --- Sprint branch management ---
            sprint_branch = f"chase/sprint-{sprint_id}"
            sprint_state_path = self.state.sprint_state(sprint_id)

            # Read existing state or create new
            sprint_state = self._read_sprint_state(sprint_id)
            if not sprint_state.get("branch"):
                # First run: create sprint branch from base
                self._create_sprint_branch(base_branch, sprint_id)
                sprint_state["branch"] = sprint_branch
                sprint_state["base_branch"] = base_branch
                sprint_state["status"] = "running"
                sprint_state["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                sprint_state["agent_chain"] = []
                self._write_sprint_state(sprint_id, sprint_state)
            else:
                # Retry: switch to existing sprint branch
                existing_branch = sprint_state["branch"]
                if not self._branch_exists(existing_branch):
                    # Branch was deleted — recreate
                    self._create_sprint_branch(base_branch, sprint_id)
                    sprint_state["branch"] = sprint_branch
                else:
                    self._checkout_branch(existing_branch)

            # Contract Negotiation
            self._update_sprint_agent(sprint_id, "Negotiator", "running")
            self.negotiator.run(sprint_id, self.cost, self.logger)
            self._update_sprint_agent(sprint_id, "Negotiator", "success")

            if not self._approval_granted():
                self.logger.error("Plan approval required. Run `chase plan`, review it, then run `chase approve`.")
                generate_handoff(self.state, self.config, self.cost, sprint_id, "approval_required")
                self._update_sprint_status(sprint_id, "failed", "approval_required")
                return 1

            # Generator-Evaluator retry loop
            retry_count = 0
            error_count = 0
            max_errors = 2  # Framework-level errors: skip after 2 consecutive
            passed = False

            # Check for eval-only retry (result exists, no eval)
            result_path = self.state.sprint_result(sprint_id)
            eval_only = result_path.exists() and result_path.stat().st_size > 0 and not eval_path.exists()

            while retry_count < self.config.max_retries:
                if not eval_only:
                    # Generator
                    feedback = ""
                    if retry_count > 0 and eval_path.exists():
                        eval_data = self._read_eval(eval_path)
                        if eval_data:
                            feedback = eval_data.get("feedback", "")

                    self._write_current_agent("Generator", sprint_id, retry_count)
                    self._update_sprint_agent(sprint_id, "Generator", "running")
                    gen_result = self.generator.run(sprint_id, feedback, self.cost, self.logger)
                    if not gen_result.success:
                        self.logger.error(f"Sprint {sprint_id} generator failed")
                        self._update_sprint_agent(sprint_id, "Generator", "failed", "generator produced no output")
                        error_count += 1
                        retry_count += 1
                        if error_count >= max_errors:
                            self.logger.error(f"Sprint {sprint_id}: {max_errors} consecutive errors, skipping")
                            break
                        continue
                    self._update_sprint_agent(sprint_id, "Generator", "success")
                else:
                    # Eval-only mode: skip generator, use existing result
                    self.logger.info(f"Sprint {sprint_id}: eval-only retry — skipping generator")
                    eval_only = False  # Only skip on first iteration

                # Evaluator
                self._write_current_agent("Evaluator", sprint_id, retry_count)
                self._update_sprint_agent(sprint_id, "Evaluator", "running")
                eval_result = self.evaluator.run(sprint_id, self.cost, self.logger)
                if not eval_result.success or eval_result.parsed_data is None:
                    self.logger.error(f"Sprint {sprint_id} evaluator no output")
                    self._update_sprint_agent(sprint_id, "Evaluator", "failed", "no valid JSON output")
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
                    self._update_sprint_agent(sprint_id, "Evaluator", "success")
                    break
                else:
                    # Legitimate FAIL (not ERROR) — reset error count, allow retries
                    error_count = 0
                    retry_count += 1
                    feedback = eval_result.parsed_data.get("feedback", "score below threshold")
                    self._update_sprint_agent(sprint_id, "Evaluator", "failed", feedback)
                    self.logger.sprint(sprint_id, "result",
                        f"Not passed (score={final_score} < threshold={self.config.eval_threshold}), "
                        f"retry {retry_count}/{self.config.max_retries}")

            # --- Post-sprint branch management ---
            current_sprint_branch = sprint_state.get("branch", sprint_branch)

            if passed:
                self._update_sprint_status(sprint_id, "success")
                # Merge sprint branch back to base
                merged = self._merge_sprint_branch(sprint_id, base_branch)
                if not merged:
                    self.logger.warning(
                        f"Sprint {sprint_id} passed but merge failed. "
                        f"Branch {current_sprint_branch} preserved for manual merge."
                    )
            else:
                self._update_sprint_status(sprint_id, "failed",
                    self._get_last_error(sprint_id) or "retries exhausted")
                # Checkout base branch, leave sprint branch for review
                self._checkout_branch(base_branch)

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
        path = self.state.approval_file
        try:
            data = json.loads(path.read_text())
            return bool(data.get("approved"))
        except FileNotFoundError:
            return False
        except json.JSONDecodeError:
            self.logger.error(f"Approval file corrupt: {path}")
            return False
        except Exception as e:
            self.logger.warning(f"Approval check failed: {e}")
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
        for agent in ("planner", "negotiator", "generator", "evaluator"):
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

    # --- Lock management ---

    def _acquire_lock(self, force: bool = False) -> bool:
        """Acquire run.lock to prevent concurrent runs. Returns True on success."""
        lock = self.state.lock_file
        if lock.exists() and not force:
            try:
                data = json.loads(lock.read_text())
                pid = data.get("pid", 0)
                started = data.get("started", "?")
                if pid and _pid_alive(pid):
                    self.logger.error(
                        f"chase run already in progress (PID={pid}, started at {started}). "
                        "Use --force to override."
                    )
                    return False
            except Exception:
                pass
        lock.write_text(json.dumps({
            "pid": os.getpid(),
            "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }))
        return True

    def _release_lock(self) -> None:
        lock = self.state.lock_file
        try:
            if lock.exists():
                lock.unlink()
        except OSError:
            pass

    # --- Current agent tracking ---

    def _write_current_agent(self, agent: str, sprint_id: int, retry: int) -> None:
        try:
            self.state.current_agent_file.write_text(json.dumps({
                "agent": agent,
                "sprint_id": sprint_id,
                "retry": retry,
            }))
        except OSError:
            pass

    def _clear_current_agent(self) -> None:
        try:
            if self.state.current_agent_file.exists():
                self.state.current_agent_file.unlink()
        except OSError:
            pass

    # --- Sprint branch management ---

    def _get_current_branch(self) -> str:
        """Get current git branch name."""
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(self.config.workspace),
            )
            return proc.stdout.strip() or "HEAD"
        except Exception:
            return "HEAD"

    def _create_sprint_branch(self, base_branch: str, sprint_id: int) -> None:
        """Create and checkout a sprint branch from the base branch."""
        branch_name = f"chase/sprint-{sprint_id}"
        try:
            # Ensure we're on base branch first
            subprocess.run(
                ["git", "checkout", base_branch],
                capture_output=True, text=True, timeout=10,
                cwd=str(self.config.workspace),
            )
            # Create sprint branch (use -B to recreate if exists)
            subprocess.run(
                ["git", "checkout", "-B", branch_name],
                capture_output=True, text=True, timeout=10,
                check=True,
                cwd=str(self.config.workspace),
            )
            self.logger.info(f"Created sprint branch: {branch_name}")
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"Failed to create sprint branch: {e.stderr or e}")
            # Non-fatal — continue without branch isolation

    def _merge_sprint_branch(self, sprint_id: int, base_branch: str) -> bool:
        """Merge sprint branch back to base with --no-ff. Returns True on success."""
        branch_name = f"chase/sprint-{sprint_id}"
        try:
            # Checkout base branch
            subprocess.run(
                ["git", "checkout", base_branch],
                capture_output=True, text=True, timeout=10,
                check=True,
                cwd=str(self.config.workspace),
            )
            # Merge with --no-ff
            result = subprocess.run(
                ["git", "merge", "--no-ff", branch_name, "-m", f"chase: merge sprint {sprint_id}"],
                capture_output=True, text=True, timeout=30,
                cwd=str(self.config.workspace),
            )
            if result.returncode != 0:
                self.logger.error(f"Merge conflict for sprint {sprint_id}: {result.stderr}")
                # Abort merge to leave clean state
                subprocess.run(
                    ["git", "merge", "--abort"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(self.config.workspace),
                )
                return False
            self.logger.info(f"Merged sprint branch {branch_name} into {base_branch}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to merge sprint branch: {e}")
            return False

    def _checkout_branch(self, branch: str) -> bool:
        """Checkout a git branch. Returns True on success."""
        try:
            subprocess.run(
                ["git", "checkout", branch],
                capture_output=True, text=True, timeout=10,
                check=True,
                cwd=str(self.config.workspace),
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def _branch_exists(self, branch: str) -> bool:
        """Check if a git branch exists."""
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                capture_output=True, text=True, timeout=5,
                check=True,
                cwd=str(self.config.workspace),
            )
            return True
        except Exception:
            return False

    # --- Sprint state management ---

    def _read_sprint_state(self, sprint_id: int) -> dict:
        """Read sprint state file. Returns empty dict if not found."""
        path = self.state.sprint_state(sprint_id)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def _write_sprint_state(self, sprint_id: int, data: dict) -> None:
        """Write sprint state file atomically."""
        path = self.state.sprint_state(sprint_id)
        _tmp = path.with_suffix(".tmp")
        _tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        _tmp.rename(path)

    def _update_sprint_agent(self, sprint_id: int, agent: str, status: str,
                              error: str = "") -> None:
        """Update the agent chain in sprint state."""
        state = self._read_sprint_state(sprint_id)
        chain = state.get("agent_chain", [])

        # Update existing entry or append new one
        found = False
        for entry in chain:
            if entry.get("agent") == agent:
                entry["status"] = status
                if error:
                    entry["error"] = error
                found = True
                break
        if not found:
            entry = {"agent": agent, "status": status}
            if error:
                entry["error"] = error
            chain.append(entry)

        state["agent_chain"] = chain
        self._write_sprint_state(sprint_id, state)

    def _update_sprint_status(self, sprint_id: int, status: str,
                               error: str = "") -> None:
        """Update sprint overall status."""
        state = self._read_sprint_state(sprint_id)
        state["status"] = status
        if error:
            state["last_error"] = error
        if status in ("success", "failed"):
            state["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_sprint_state(sprint_id, state)

    def _get_last_error(self, sprint_id: int) -> str:
        """Get last error from sprint state."""
        state = self._read_sprint_state(sprint_id)
        return state.get("last_error", "")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
