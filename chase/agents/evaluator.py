"""Evaluator Agent — verify sprint results."""

from __future__ import annotations

import json
import logging
import subprocess

from chase.agents.base import AgentBase, AgentResult
from chase.cost import CostTracker
from chase.logging import ChaseLogger
from chase.subprocess import run_claude, run_cli_streaming, extract_json_from_text
from chase.computer_use import is_web_sprint

logger = logging.getLogger(__name__)


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

        # --- Deterministic checks (before LLM evaluation) ---
        deterministic = self._run_deterministic_checks(sprint_id, active_contract)
        evidence_section = self._format_deterministic_evidence(deterministic)

        # Get full git diff
        git_diff = self._get_git_diff(sprint_id)

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

{evidence_section}
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

        import time as _time
        logger.sprint(sprint_id, "evaluator", ">>> Evaluator working... (timeout 480s)")
        t0 = _time.monotonic()
        claude_result = run_cli_streaming(
            full_prompt,
            cli=self.config.cli,
            max_turns=5,  # Evaluator only needs to read files + output JSON
            allowed_tools=allowed_tools,
            model=self.config.get_model("evaluator"),
            env=self.config.get_agent_env("evaluator"),
            cwd=str(self.config.workspace),
            timeout=480,
            label="Evaluator",
        )
        elapsed = _time.monotonic() - t0
        logger.sprint(sprint_id, "evaluator", f">>> Evaluator done ({elapsed:.1f}s)")

        cost.track(claude_result.cost, str(sprint_id), "evaluator")

        # Extract JSON eval result
        eval_json = extract_json_from_text(claude_result.result_text)
        if eval_json is None:
            logger.sprint(
                sprint_id, "evaluator",
                f"Evaluator failed to produce valid JSON.\n"
                f"Suggestions:\n"
                f"  1. Check sprint contract is well-defined (sprint {sprint_id})\n"
                f"  2. Try a more powerful model: CHASE_EVALUATOR_MODEL=gpt-4o\n"
                f"  3. Reduce evaluation criteria complexity\n"
                f"  4. Raw output saved in eval result",
            )
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
            verdict = "PASS" if score >= self.config.eval_threshold else "FAIL"
            eval_json = {
                "score": score,
                "verdict": verdict,
                "criteria": criteria,
                "feedback": "; ".join(
                    c.get("evidence", c.get("name", "")) for c in criteria
                    if isinstance(c, dict) and not c.get("passes")
                ),
            }

        # Cap score if deterministic checks failed
        if not deterministic.get("all_passed", True):
            current_score = float(eval_json.get("score", 0))
            if current_score > 0.5:
                eval_json["score_capped"] = True
                eval_json["original_score"] = current_score
                eval_json["score"] = 0.5
                eval_json["verdict"] = "FAIL"
                eval_json.setdefault("feedback", "")
                eval_json["feedback"] += (
                    "\n[SYSTEM] Score capped at 0.5 due to deterministic check failures: "
                    + deterministic.get("fail_summary", "unknown")
                )

        # Write eval file (atomic via tempfile)
        _tmp = eval_path.with_suffix(".tmp")
        _tmp.write_text(json.dumps(eval_json, ensure_ascii=False, indent=2) + "\n")
        _tmp.rename(eval_path)

        score = eval_json.get("score", 0)
        verdict = eval_json.get("verdict", "UNKNOWN")
        logger.sprint(sprint_id, "evaluator", f"Score: {score}, Verdict: {verdict}, Cost ${claude_result.cost:.4f}")

        return AgentResult(success=True, cost=claude_result.cost, raw_text=claude_result.result_text, parsed_data=eval_json)

    # --- Deterministic checks ---

    def _run_deterministic_checks(self, sprint_id: int, contract_path) -> dict:
        """Run deterministic checks before LLM evaluation.

        Returns dict with check results and an all_passed flag.
        """
        results = {
            "test": {"ran": False, "passed": False, "output": ""},
            "lint": {"ran": False, "passed": False, "output": ""},
            "typecheck": {"ran": False, "passed": False, "output": ""},
            "git_diff": "",
            "file_existence": {"checked": False, "missing": []},
            "all_passed": True,
            "fail_summary": "",
        }

        # Parse contract for test_command and files
        contract_data = self._parse_contract(contract_path)
        if not contract_data:
            return results

        test_command = self._get_test_command(contract_data)
        files = contract_data.get("files_likely_touched", [])

        failures = []

        # 1. Run tests
        if test_command:
            test_result = self._run_command(test_command)
            results["test"] = test_result
            if not test_result["passed"]:
                failures.append(f"tests failed ({test_command})")

        # 2. Run lint
        lint_result = self._detect_and_run("lint")
        if lint_result:
            results["lint"] = lint_result
            if not lint_result["passed"]:
                failures.append("lint errors found")

        # 3. Run type check
        typecheck_result = self._detect_and_run("typecheck")
        if typecheck_result:
            results["typecheck"] = typecheck_result
            if not typecheck_result["passed"]:
                failures.append("type check errors found")

        # 4. File existence
        if files:
            missing = [f for f in files if not (self.config.workspace / f).exists()]
            results["file_existence"] = {"checked": True, "missing": missing}
            if missing:
                failures.append(f"missing files: {', '.join(missing[:5])}")

        if failures:
            results["all_passed"] = False
            results["fail_summary"] = "; ".join(failures)

        return results

    def _parse_contract(self, contract_path) -> dict | None:
        """Parse contract JSON from markdown file."""
        try:
            text = contract_path.read_text()
            return json.loads(text)
        except Exception:
            return None

    def _get_test_command(self, contract_data: dict) -> str:
        """Extract test_command from contract data."""
        if contract_data.get("test_command"):
            return str(contract_data["test_command"])
        nested = contract_data.get("contract", {})
        return str(nested.get("test_command", ""))

    def _run_command(self, command: str, timeout: int = 60) -> dict:
        """Run a shell command and return result dict."""
        try:
            proc = subprocess.run(
                command, shell=True,
                capture_output=True, text=True, timeout=timeout,
                cwd=str(self.config.workspace),
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            output = output[:5000]  # Truncate very long output
            return {
                "ran": True,
                "passed": proc.returncode == 0,
                "output": output.strip(),
            }
        except subprocess.TimeoutExpired:
            return {"ran": True, "passed": False, "output": f"[TIMEOUT] command took too long: {command}"}
        except Exception as e:
            return {"ran": True, "passed": False, "output": f"[ERROR] {e}"}

    def _detect_and_run(self, check_type: str) -> dict | None:
        """Detect and run a lint or typecheck tool. Returns None if no tool found."""
        tools = {
            "lint": [
                (["ruff", "check", ".", "--quiet"], "ruff"),
                (["flake8", ".", "--quiet"], "flake8"),
            ],
            "typecheck": [
                (["mypy", ".", "--no-error-summary"], "mypy"),
                (["pyright", "--quiet"], "pyright"),
            ],
        }

        for cmd_parts, name in tools.get(check_type, []):
            try:
                # Check if tool is available
                detect = subprocess.run(
                    [cmd_parts[0], "--version"],
                    capture_output=True, timeout=5,
                )
                if detect.returncode != 0:
                    continue

                # Run the check
                proc = subprocess.run(
                    cmd_parts,
                    capture_output=True, text=True, timeout=60,
                    cwd=str(self.config.workspace),
                )
                output = (proc.stdout or "") + (proc.stderr or "")
                output = output[:5000]
                return {
                    "ran": True,
                    "passed": proc.returncode == 0,
                    "output": output.strip() or f"({name}: clean)",
                }
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                return {"ran": True, "passed": False, "output": f"[TIMEOUT] {name}"}
            except Exception:
                continue

        return None  # No tool found

    def _format_deterministic_evidence(self, results: dict) -> str:
        """Format deterministic check results as evidence for the evaluator prompt."""
        lines = ["## Deterministic Check Results", ""]

        all_passed = results.get("all_passed", True)

        if all_passed:
            has_any = (
                results["test"]["ran"]
                or results["lint"]["ran"]
                or (results["typecheck"] or {}).get("ran", False)
                or results["file_existence"]["checked"]
            )
            if has_any:
                lines.append("**ALL DETERMINISTIC CHECKS PASSED**")
            else:
                lines.append("*No deterministic checks were applicable for this sprint.*")
        else:
            lines.append(
                "**SOME DETERMINISTIC CHECKS FAILED — "
                "score should not exceed 0.5 unless you can clearly explain why each failure is acceptable.**"
            )
        lines.append("")

        # Test results
        test = results.get("test", {})
        if test.get("ran"):
            status = "PASSED" if test["passed"] else "FAILED"
            lines.append(f"### Tests: {status}")
            if test.get("output"):
                lines.append(f"```\n{test['output']}\n```")
            lines.append("")

        # Lint results
        lint = results.get("lint", {})
        if lint.get("ran"):
            status = "PASSED" if lint["passed"] else "FAILED"
            lines.append(f"### Lint: {status}")
            if lint.get("output") and not lint["passed"]:
                lines.append(f"```\n{lint['output']}\n```")
            lines.append("")

        # Type check results
        typecheck = results.get("typecheck") or {}
        if typecheck.get("ran"):
            status = "PASSED" if typecheck["passed"] else "FAILED"
            lines.append(f"### Type Check: {status}")
            if typecheck.get("output") and not typecheck["passed"]:
                lines.append(f"```\n{typecheck['output']}\n```")
            lines.append("")

        # File existence
        fe = results.get("file_existence", {})
        if fe.get("checked"):
            missing = fe.get("missing", [])
            if missing:
                lines.append(f"### File Existence: MISSING {len(missing)} file(s)")
                for f in missing:
                    lines.append(f"- `{f}`")
            else:
                lines.append("### File Existence: ALL PRESENT")
            lines.append("")

        return "\n".join(lines)

    def _get_git_diff(self, sprint_id: int = None) -> str:
        """Get git diff — full diff when on a sprint branch, stat otherwise."""
        try:
            # Try to get diff against base branch
            if sprint_id:
                branch = f"chase/sprint-{sprint_id}"
                # Check if we're on the sprint branch with a base to diff against
                try:
                    base_proc = subprocess.run(
                        ["git", "merge-base", branch, f"{branch}~1"],
                        capture_output=True, text=True, timeout=5,
                        cwd=str(self.config.workspace),
                    )
                    if base_proc.returncode == 0:
                        diff_proc = subprocess.run(
                            ["git", "diff", "HEAD~1"],
                            capture_output=True, text=True, timeout=10,
                            cwd=str(self.config.workspace),
                        )
                        if diff_proc.stdout.strip():
                            # Truncate very long diffs
                            diff = diff_proc.stdout.strip()
                            if len(diff) > 20000:
                                diff = diff[:20000] + "\n... (truncated)"
                            return diff
                except Exception:
                    pass

            # Fallback: stat diff
            proc = subprocess.run(
                ["git", "diff", "HEAD~1", "--stat"],
                capture_output=True, text=True, timeout=10,
                cwd=str(self.config.workspace),
            )
            return proc.stdout.strip() or "no diff"
        except Exception as e:
            logger.debug(f"git diff failed: {e}")
            return "no diff"
