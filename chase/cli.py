"""CLI — command-line interface for chase."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from chase.config import ChaseConfig
from chase.cost import CostTracker
from chase.fmt import print_bold, print_green, print_red, print_yellow
from chase.logging import ChaseLogger
from chase.orchestrator import Orchestrator
from chase.ray.cli import handle_ray, register_parser as register_ray
from chase.state import StateDir
from chase.trust import classify_failure, estimate_contract_risk, render_plan_preview


def resolve_workspace(arg: str | None) -> Path:
    """Resolve workspace directory with same logic as bash version."""
    # 1. Explicit argument
    if arg:
        path = Path(arg).resolve()
        if not path.is_dir():
            print_red(f"Directory not found: {arg}")
            raise SystemExit(1)
        return path

    # 2. Environment variable
    ws_env = __import__("os").environ.get("CHASE_WORKSPACE", "")
    if ws_env:
        path = Path(ws_env).resolve()
        if not path.is_dir():
            print_red(f"CHASE_WORKSPACE invalid: {ws_env}")
            raise SystemExit(1)
        return path

    cwd = Path.cwd().resolve()

    # 3. Current directory has MISSION.md
    if (cwd / "MISSION.md").exists():
        return cwd

    # 4. Current directory has .chase/
    if (cwd / ".chase").is_dir():
        return cwd

    # 5. Current directory is git root
    try:
        subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, timeout=5, check=True)
        return cwd
    except Exception:
        pass

    print_red("Cannot detect workspace. Run in a project root, or use --workspace <path>")
    raise SystemExit(1)


def cmd_init(args) -> int:
    ws = resolve_workspace(args.workspace)
    print_bold(f"Initializing chase in: {ws}")

    config = ChaseConfig.from_env(ws)
    state = StateDir.for_workspace(ws)

    # MISSION.md
    mission_file = ws / "MISSION.md"
    if mission_file.exists():
        print_yellow("MISSION.md already exists, skipping")
    else:
        mission_file.write_text("""# Goal

<!-- Describe what you want to accomplish -->
<!-- Tip: Keep under 2KB. Each sprint takes ~10-20 min. Be specific and measurable. -->

# Context

<!-- Project background, tech stack, relevant files -->

# Acceptance Criteria (MUST)

<!-- These MUST be satisfied for the project to be considered complete. -->
<!-- Each criterion becomes a sprint or is verified in a sprint. -->

- [ ] <!-- Criterion 1: specific, testable outcome -->
- [ ] <!-- Criterion 2 -->

# Nice to Have

<!-- These are desired but not required. Lower priority sprints. -->

- [ ] <!-- Optional enhancement -->

# Performance Requirements

<!-- Optional: specific performance targets -->

# UI Requirements

<!-- Optional: specific UI/UX expectations -->

""")
        print_green("Created MISSION.md")

    # .chase/ directory
    state.init_directories()
    state.init_cost_file()

    # .env example
    env_file = state.root / ".env"
    if not env_file.exists():
        env_file.write_text("""# Chase configuration — edit and uncomment as needed

# CLI adapter (claude / codex / gemini)
# CHASE_CLI=claude

# LLM provider (uncomment for custom endpoint)
# CHASE_LLM_API_KEY=your-api-key-here
# CHASE_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# CHASE_MODEL=glm-4-plus

# Budget
# CHASE_COST_LIMIT=10000.0

# Approval is required by default before Generator modifies code.
# Set to 0 only when you want fully automatic runs.
# CHASE_REQUIRE_APPROVAL=0

# Sprint tuning
# CHASE_MAX_RETRIES=10
# CHASE_EVAL_THRESHOLD=0.7
""")
        print_green("Created .chase/.env")

    print_green("Done! Next steps:")
    print()
    print("  1. Edit MISSION.md with your goal")
    print("  2. Edit .chase/.env to configure your LLM provider")
    config = ChaseConfig.from_env(ws)
    if config.require_approval:
        print("  3. Run: chase plan")
        print("  4. Review the plan, then: chase approve")
        print("  5. Run: chase run")
    else:
        print("  3. Run: chase run")
    print()
    return 0


def cmd_run(args) -> int:
    ws = resolve_workspace(args.workspace)

    state = StateDir.for_workspace(ws)
    if not state.root.is_dir():
        print_red("No .chase/ found. Run 'chase init' first.")
        return 1

    if not state.mission_file.exists():
        print_red("MISSION.md not found. Create it first.")
        return 1

    watchdog = getattr(args, "watchdog", False)

    if watchdog:
        return _run_with_watchdog(ws, args)

    config = ChaseConfig.from_env(ws)
    force = getattr(args, "force", False)
    orch = Orchestrator(config, state)
    return orch.run(force=force)


def _run_with_watchdog(ws: Path, args) -> int:
    """Run orchestrator under a watchdog supervisor.

    The supervisor monitors the orchestrator subprocess. If it crashes
    (non-zero exit), the supervisor auto-restarts it. Exits only when:
    - orchestrator exits with code 0 (success)
    - user presses Ctrl+C
    - max restarts exceeded (5 consecutive crashes in 60s)
    """
    import time

    max_restarts = 5
    window_seconds = 60
    restart_times: list[float] = []
    attempt = 0

    print_bold("Chase Watchdog Mode")
    print(f"  Workspace: {ws}")
    print(f"  Max restarts: {max_restarts} per {window_seconds}s")
    print()

    while True:
        attempt += 1
        print_bold(f"--- Attempt {attempt} ---")

        config = ChaseConfig.from_env(ws)
        force = getattr(args, "force", False) or attempt > 1
        state = StateDir.for_workspace(ws)
        orch = Orchestrator(config, state)
        exit_code = orch.run(force=force)

        if exit_code == 0:
            print_green("Chase completed successfully.")
            return 0

        # Track restart timing
        now = time.monotonic()
        restart_times.append(now)
        # Prune old entries
        restart_times = [t for t in restart_times if now - t < window_seconds]

        if len(restart_times) >= max_restarts:
            print_red(f"Watchdog: {max_restarts} crashes in {window_seconds}s. Stopping.")
            return 1

        print_yellow(f"Watchdog: orchestrator exited with code {exit_code}. Restarting in 5s...")
        print_yellow(f"  ({len(restart_times)}/{max_restarts} restarts in {window_seconds}s window)")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print()
            print_yellow("Watchdog stopped by user.")
            return 130


def cmd_plan(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)
    state.init_directories()

    if not state.mission_file.exists():
        print_red("MISSION.md not found. Create it first.")
        return 1

    config = ChaseConfig.from_env(ws)
    contracts = state.existing_contracts()
    if not contracts:
        orch = Orchestrator(config, state)
        orch.preflight()
        result = orch.planner.run(orch.cost, orch.logger)
        if not result.success:
            print_red("Planner failed.")
            return 1
        contracts = state.existing_contracts()
        for contract_path in contracts:
            sid = int(contract_path.stem.split("-")[0])
            orch.negotiator.run(sid, orch.cost, orch.logger)

    contract_data = [_read_preview_contract(state, path) for path in contracts]
    preview = render_plan_preview(contract_data)
    state.plan_preview_file.write_text(preview, encoding="utf-8")
    print(preview)
    print_green(f"Plan preview written to {state.plan_preview_file}")
    return 0


def cmd_approve(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)
    state.init_directories()
    payload = {
        "approved": True,
        "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    state.approval_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print_green("Plan approved. `chase run` may now execute code changes.")
    return 0


def cmd_status(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)

    if not state.root.is_dir():
        print_red("No .chase/ found. Run 'chase init' first.")
        return 1

    watch = getattr(args, "watch", False)
    if watch:
        return _cmd_status_watch(ws, state)

    return _cmd_status_render(ws, state)


def _cmd_status_render(ws, state) -> int:
    print_bold("Chase Status")
    print(f"  Workspace: {ws}")
    print()

    # Current agent
    agent_file = state.current_agent_file
    if agent_file.exists():
        try:
            agent_data = json.loads(agent_file.read_text())
            agent_name = agent_data.get("agent", "?")
            sprint_id = agent_data.get("sprint_id", "?")
            retry = agent_data.get("retry", "?")
            print(f"  Currently: {agent_name} (Sprint {sprint_id}, retry {retry})")
            print()
        except Exception:
            pass

    # Mission
    if state.mission_file.exists():
        print_bold("Mission:")
        for line in state.mission_file.read_text().split("\n")[:5]:
            print(f"  {line}")
        print()

    # Cost
    if state.cost_file.exists():
        cost = CostTracker(state.cost_file)
        config = ChaseConfig.from_env(ws)
        remaining = config.cost_limit - cost.total_cost
        print_bold("Cost:")
        print(f"  ${cost.total_cost:.4f} spent / ${config.cost_limit} budget (${remaining:.2f} remaining)")
        print()

    # Sprint progress
    contracts = state.existing_contracts()
    sprint_count = pass_count = fail_count = pend_count = 0
    print_bold("Sprints:")

    for contract_path in contracts:
        sprint_count += 1
        sid = int(contract_path.stem.split("-")[0])
        try:
            contract = json.loads(contract_path.read_text())
            title = contract.get("title", "?")
        except Exception:
            contract = {}
            title = "?"
        risk = estimate_contract_risk(contract)

        eval_path = state.sprint_eval(sid)
        sprint_state_path = state.sprint_state(sid)
        sprint_state = {}
        if sprint_state_path.exists():
            try:
                sprint_state = json.loads(sprint_state_path.read_text())
            except Exception:
                pass

        branch = sprint_state.get("branch", "")
        branch_info = f"  branch: {branch}" if branch else ""

        if eval_path.exists():
            try:
                eval_data = json.loads(eval_path.read_text())
                verdict = eval_data.get("verdict", "?")
                score = eval_data.get("score", "?")
                reason = classify_failure(eval_data)
                if verdict == "PASS":
                    pass_count += 1
                    print(f"  \033[32m[PASS]\033[0m  Sprint {sid}: {title} (score: {score})")
                else:
                    fail_count += 1
                    print(f"  \033[31m[FAIL]\033[0m Sprint {sid}: {title} (score: {score})")
                print(f"          risk: {risk} | reason: {reason}")
                card_path = state.sprint_verification_card(sid)
                if card_path.exists():
                    print(f"          evidence: {card_path}")
                if branch_info:
                    print(f"  {branch_info}")
            except Exception:
                pend_count += 1
                print(f"  [----]  Sprint {sid}: {title}")
                print(f"          risk: {risk}")
                if branch_info:
                    print(f"  {branch_info}")
        else:
            pend_count += 1
            status_str = sprint_state.get("status", "")
            status_info = f" | status: {status_str}" if status_str else ""
            last_error = sprint_state.get("last_error", "")
            error_info = f" | error: {last_error}" if last_error else ""

            print(f"  [----]  Sprint {sid}: {title}{status_info}{error_info}")
            print(f"          risk: {risk}")
            if branch_info:
                print(f"  {branch_info}")

            # Show agent chain
            agent_chain = sprint_state.get("agent_chain", [])
            if agent_chain:
                chain_str = " → ".join(
                    f"{a['agent']} {_status_icon(a['status'])}"
                    for a in agent_chain
                )
                print(f"          chain: {chain_str}")

    if sprint_count == 0:
        print("  (no sprints yet — run 'chase run' to start)")
    else:
        print()
        print(f"  Total: {sprint_count} | Pass: {pass_count} | Fail: {fail_count} | Pending: {pend_count}")
    print()

    # Latest handoff
    latest = state.latest_handoff()
    if latest:
        print_bold(f"Latest Handoff: {latest.name}")
        for line in latest.read_text().split("\n")[:15]:
            print(f"  {line}")

    return 0


def _status_icon(status: str) -> str:
    """Return a status icon for agent chain display."""
    if status == "success":
        return "\u2713"
    elif status == "failed":
        return "\u2717"
    elif status == "running":
        return "..."
    return "?"


def _cmd_status_watch(ws, state) -> int:
    """Refresh status display every 5 seconds until Ctrl+C."""
    import time
    import os

    if not sys.stdout.isatty():
        print_yellow("--watch requires a terminal. Showing single status render instead.")
        return _cmd_status_render(ws, state)

    try:
        while True:
            # Clear screen
            print("\033[2J\033[H", end="")
            _cmd_status_render(ws, state)
            print()
            print_yellow("  Watching... (Ctrl+C to stop)")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print()
        return 0


def _read_preview_contract(state: StateDir, contract_path: Path) -> dict:
    sid = int(contract_path.stem.split("-")[0])
    negotiated_path = state.sprint_negotiated(sid)
    active_path = negotiated_path if negotiated_path.exists() else contract_path
    return json.loads(active_path.read_text(encoding="utf-8"))


def cmd_reset(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)

    if not state.root.is_dir():
        print_red("No .chase/ found.")
        return 1

    print_yellow("This will delete all sprint contracts, evaluations, handoffs, and logs.")
    response = input("Continue? [y/N] ")
    if response.lower() != "y":
        print("Aborted.")
        return 0

    import shutil
    for d in [state.sprints, state.handoffs, state.logs]:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    state.init_cost_file()
    print_green("Reset complete. Run 'chase run' to start fresh.")
    return 0


def cmd_doctor(args) -> int:
    """Diagnose common setup issues."""
    import sys

    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig.from_env(ws)

    ok = "\033[32m[OK]\033[0m"
    fail = "\033[31m[FAIL]\033[0m"
    warn = "\033[33m[WARN]\033[0m"
    issues = 0

    # 1. Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        print(f"  {ok}  Python {py_ver}")
    else:
        print(f"  {warn}  Python {py_ver} (Chase recommends 3.10+)")
        issues += 1

    # 2. CLI adapter installed
    cli_cmd = config.cli
    try:
        proc = subprocess.run([cli_cmd, "--version"], capture_output=True, text=True, timeout=5)
        ver = (proc.stdout or proc.stderr or "").strip().split("\n")[0]
        print(f"  {ok}  CLI: {cli_cmd} ({ver})")
    except FileNotFoundError:
        print(f"  {fail}  CLI: '{cli_cmd}' not found in PATH. Install it or set CHASE_CLI.")
        issues += 1
    except Exception as e:
        print(f"  {fail}  CLI: '{cli_cmd}' check failed: {e}")
        issues += 1

    # 3. MISSION.md exists and size
    mission = state.mission_file
    if mission.exists():
        size = mission.stat().st_size
        if size < 30:
            print(f"  {warn}  MISSION.md exists but seems too short ({size}B). Add more detail.")
            issues += 1
        elif size > 3072:
            print(f"  {warn}  MISSION.md is large ({size}B). Keep under 2KB for best results.")
            issues += 1
        else:
            print(f"  {ok}  MISSION.md ({size}B)")
    else:
        print(f"  {fail}  MISSION.md not found. Create it in the workspace root.")
        issues += 1

    # 4. .chase/ directory structure
    if state.root.is_dir():
        dirs_ok = all(d.is_dir() for d in [state.sprints, state.handoffs, state.logs])
        if dirs_ok:
            print(f"  {ok}  .chase/ directory structure")
        else:
            print(f"  {warn}  .chase/ exists but incomplete. Run 'chase init' to fix.")
            issues += 1
    else:
        print(f"  {fail}  .chase/ not found. Run 'chase init' first.")
        issues += 1

    # 5. Git repository
    try:
        subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, timeout=5, check=True)
        print(f"  {ok}  Git repository")
    except Exception:
        print(f"  {fail}  Not a git repository. Run 'git init' first.")
        issues += 1

    # 6. .env config validity
    env_ok = True
    if config.cost_limit <= 0:
        print(f"  {fail}  CHASE_COST_LIMIT={config.cost_limit} must be > 0")
        env_ok = False
    if config.eval_threshold < 0 or config.eval_threshold > 1:
        print(f"  {fail}  CHASE_EVAL_THRESHOLD={config.eval_threshold} must be 0-1")
        env_ok = False
    if config.max_retries < 1:
        print(f"  {fail}  CHASE_MAX_RETRIES={config.max_retries} must be >= 1")
        env_ok = False
    if env_ok:
        print(f"  {ok}  Configuration valid (cost_limit={config.cost_limit}, threshold={config.eval_threshold})")
    else:
        issues += 1

    print()
    if issues == 0:
        print_green("All checks passed. Ready to run.")
        return 0
    else:
        print_yellow(f"{issues} issue(s) found. Fix them before running 'chase run'.")
        return 1


def cmd_logs(args) -> int:
    """View chase log files with filtering."""
    import re as _re

    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)

    # If sprint_id is given, show sprint-specific log
    sprint_id = getattr(args, "sprint_id", None)
    if sprint_id is not None:
        sprint_log = state.logs / f"sprint-{sprint_id}.log"
        if sprint_log.exists():
            lines = sprint_log.read_text().splitlines()
            tail = getattr(args, "tail", 20)
            show_all = getattr(args, "all", False)
            if not show_all:
                lines = lines[-tail:]
            for line in lines:
                if "ERROR" in line:
                    print_red(line)
                elif "WARNING" in line or "WARN" in line:
                    print_yellow(line)
                elif "PASSED" in line or "PASS" in line:
                    print_green(line)
                else:
                    print(line)
        else:
            print_yellow(f"No sprint log found for sprint {sprint_id}.")
            # Fallback: filter daily logs by sprint
            log_dir = state.logs
            if log_dir.is_dir():
                log_files = sorted(log_dir.glob("*.log"))
                all_lines: list[str] = []
                for lf in log_files:
                    try:
                        all_lines.extend(lf.read_text().splitlines())
                    except OSError:
                        pass
                pattern = _re.compile(rf"\[Sprint {sprint_id}/")
                matching = [l for l in all_lines if pattern.search(l)]
                if matching:
                    print_yellow(f"Showing daily log entries for sprint {sprint_id}:")
                    tail = getattr(args, "tail", 20)
                    show_all = getattr(args, "all", False)
                    if not show_all:
                        matching = matching[-tail:]
                    for line in matching:
                        if "ERROR" in line:
                            print_red(line)
                        elif "WARNING" in line or "WARN" in line:
                            print_yellow(line)
                        else:
                            print(line)
                else:
                    print_yellow(f"No log entries found for sprint {sprint_id}.")
        return 0

    # Original daily log behavior
    log_dir = state.logs
    if not log_dir.is_dir():
        print_yellow("No logs directory found. Run 'chase run' first.")
        return 0

    # Collect all log files, sorted by name (date)
    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        print_yellow("No log files found.")
        return 0

    # Read all lines from all log files
    lines: list[str] = []
    for lf in log_files:
        try:
            lines.extend(lf.read_text().splitlines())
        except OSError:
            pass

    if not lines:
        print_yellow("Log files are empty.")
        return 0

    # Apply filters
    sprint_filter = getattr(args, "sprint", None)
    agent_filter = getattr(args, "agent", None)

    if sprint_filter is not None:
        sprint_pattern = _re.compile(rf"\[Sprint {sprint_filter}/")
        lines = [l for l in lines if sprint_pattern.search(l)]

    if agent_filter is not None:
        agent_lower = agent_filter.lower()
        lines = [l for l in lines if agent_lower in l.lower()]

    # Tail
    show_all = getattr(args, "all", False)
    tail = getattr(args, "tail", 20)
    if not show_all:
        lines = lines[-tail:]

    if not lines:
        print_yellow("No matching log entries found.")
        return 0

    # Print with color coding
    for line in lines:
        if "ERROR" in line:
            print_red(line)
        elif "WARNING" in line or "WARN" in line:
            print_yellow(line)
        elif "PASSED" in line or "PASS" in line:
            print_green(line)
        else:
            print(line)

    return 0


def cmd_retry(args) -> int:
    """Retry a specific sprint from its breakpoint.

    If sprint_id is not given, retry the last failed sprint.
    If evaluator failed: only re-run evaluator (keep generator result).
    If generator failed: re-run both generator and evaluator.
    """
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)

    sprint_id = getattr(args, "sprint_id", None)

    # Auto-detect last failed sprint
    if sprint_id is None:
        sprint_id = _find_last_failed_sprint(state)
        if sprint_id is None:
            print_yellow("No failed sprints found to retry.")
            return 1
        print(f"Retrying last failed sprint: {sprint_id}")

    eval_path = state.sprint_eval(sprint_id)
    result_path = state.sprint_result(sprint_id)

    if not state.sprint_contract(sprint_id).exists():
        print_red(f"Sprint {sprint_id} contract not found.")
        return 1

    # Determine retry mode from sprint state
    sprint_state_path = state.sprint_state(sprint_id)
    retry_mode = "full"  # Default: re-run both generator and evaluator
    last_agent = None

    if sprint_state_path.exists():
        try:
            sprint_state = json.loads(sprint_state_path.read_text())
            agent_chain = sprint_state.get("agent_chain", [])
            # Find the last failed agent
            for entry in reversed(agent_chain):
                if entry.get("status") == "failed":
                    last_agent = entry.get("agent")
                    break

            if last_agent == "Evaluator" and result_path.exists() and result_path.stat().st_size > 0:
                retry_mode = "eval_only"
        except Exception:
            pass

    # Also check eval directly
    if eval_path.exists():
        try:
            eval_data = json.loads(eval_path.read_text())
            verdict = eval_data.get("verdict", "")
            if verdict == "FAIL" and result_path.exists() and result_path.stat().st_size > 0:
                retry_mode = "eval_only"
        except Exception:
            pass

    removed = []
    if eval_path.exists():
        eval_path.unlink()
        removed.append("eval")

    if retry_mode != "eval_only":
        if result_path.exists():
            result_path.unlink()
            removed.append("result")
    else:
        removed.append("(keeping result for eval-only retry)")

    # Checkout sprint branch if it exists
    branch = f"chase/sprint-{sprint_id}"
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True, text=True, timeout=5, check=True,
            cwd=str(ws),
        )
        subprocess.run(
            ["git", "checkout", branch],
            capture_output=True, text=True, timeout=10,
            cwd=str(ws),
        )
        print(f"Switched to branch: {branch}")
    except Exception:
        pass  # Branch doesn't exist yet, will be created by orchestrator

    if removed:
        if retry_mode == "eval_only":
            print_green(f"Sprint {sprint_id}: eval-only retry (keeping generator result). Run 'chase run' to continue.")
        else:
            print_green(f"Sprint {sprint_id}: removed {', '.join(removed)}. Run 'chase run' to retry.")
    else:
        print_yellow(f"Sprint {sprint_id}: no eval/result to clear (not yet run).")
    return 0


def _find_last_failed_sprint(state: StateDir) -> int | None:
    """Find the last failed sprint ID. Returns None if no failures."""
    contracts = state.existing_contracts()
    for contract_path in reversed(contracts):
        sid = int(contract_path.stem.split("-")[0])
        eval_path = state.sprint_eval(sid)
        if eval_path.exists():
            try:
                eval_data = json.loads(eval_path.read_text())
                if eval_data.get("verdict") != "PASS":
                    return sid
            except Exception:
                return sid
        else:
            # Has contract but no eval — might have been interrupted
            sprint_state_path = state.sprint_state(sid)
            if sprint_state_path.exists():
                try:
                    sprint_state = json.loads(sprint_state_path.read_text())
                    if sprint_state.get("status") == "failed":
                        return sid
                except Exception:
                    pass
    return None


def cmd_skip(args) -> int:
    """Skip a specific sprint."""
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)

    sprint_id = args.sprint_id
    if not state.sprint_contract(sprint_id).exists():
        print_red(f"Sprint {sprint_id} contract not found.")
        return 1

    skip_path = state.sprint_skip(sprint_id)
    skip_path.write_text(json.dumps({"verdict": "SKIP"}) + "\n")
    print_green(f"Sprint {sprint_id}: marked as SKIP. Orchestrator will skip it.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="chase",
        description="Chase — Multi-Agent autonomous development for Claude Code",
    )
    sub = parser.add_subparsers(dest="command")

    cmd_help = {
        "init": "Initialize chase in a workspace",
        "plan": "Generate sprint contracts from MISSION.md",
        "approve": "Approve a plan for execution",
        "run": "Execute the full plan (plan → approve → sprints)",
        "resume": "Alias for run; resumes from existing sprint state",
        "status": "Show sprint progress, branches, and costs",
        "reset": "Delete all sprint state and start fresh",
        "doctor": "Diagnose common setup issues",
        "retry": "Retry a failed sprint from its breakpoint",
        "skip": "Mark a sprint as SKIP so orchestrator skips it",
        "logs": "View chase log files",
    }
    simple_cmds = ("init", "plan", "approve", "reset", "doctor")
    for name in simple_cmds:
        p = sub.add_parser(name, help=cmd_help.get(name))
        p.add_argument("--workspace", default=None)

    # run and resume with --force
    for name in ("run", "resume"):
        p = sub.add_parser(name, help=cmd_help[name])
        p.add_argument("--workspace", default=None)
        p.add_argument("--force", action="store_true", help="Override existing run lock")
        p.add_argument("--watchdog", action="store_true",
                       help="Auto-restart orchestrator on crash (up to 5x per 60s)")

    # status with --watch
    p = sub.add_parser("status", help=cmd_help["status"])
    p.add_argument("--watch", action="store_true", help="Auto-refresh every 5 seconds")
    p.add_argument("--workspace", default=None)

    # retry — sprint_id is optional (auto-detect last failed)
    p = sub.add_parser("retry", help=cmd_help["retry"])
    p.add_argument("sprint_id", type=int, nargs="?", default=None,
                   help="Sprint number to retry (default: last failed)")
    p.add_argument("--workspace", default=None)

    # skip takes a sprint_id
    p = sub.add_parser("skip", help=cmd_help["skip"])
    p.add_argument("sprint_id", type=int, help="Sprint number to skip")
    p.add_argument("--workspace", default=None)

    # logs — optional sprint_id positional arg
    p = sub.add_parser("logs", help=cmd_help["logs"])
    p.add_argument("sprint_id", type=int, nargs="?", default=None,
                   help="Sprint number to view logs for")
    p.add_argument("--tail", type=int, default=20, help="Show last N lines (default: 20)")
    p.add_argument("--sprint", type=int, default=None, help="Filter by sprint ID")
    p.add_argument("--agent", type=str, default=None, help="Filter by agent name")
    p.add_argument("--all", action="store_true", help="Show all log lines (no tail limit)")
    p.add_argument("--workspace", default=None)

    # 注册 ray 子命令
    register_ray(sub)

    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "plan": cmd_plan,
        "approve": cmd_approve,
        "run": cmd_run,
        "resume": cmd_run,
        "status": cmd_status,
        "reset": cmd_reset,
        "doctor": cmd_doctor,
        "retry": cmd_retry,
        "skip": cmd_skip,
        "logs": cmd_logs,
        "ray": handle_ray,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except KeyboardInterrupt:
        print()
        print_yellow("Interrupted.")
        return 130
