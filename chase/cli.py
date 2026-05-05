"""CLI — command-line interface for chase."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from chase.config import ChaseConfig
from chase.cost import CostTracker
from chase.fmt import print_bold, print_green, print_red, print_yellow
from chase.logging import ChaseLogger
from chase.orchestrator import Orchestrator
from chase.ray.cli import handle_ray, register_parser as register_ray
from chase.state import StateDir


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

# Context

<!-- Project background, tech stack, relevant files -->

# Acceptance Criteria

<!-- Specific, testable completion conditions -->
<!-- 1. ... -->
<!-- 2. ... -->
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

# Sprint tuning
# CHASE_MAX_RETRIES=10
# CHASE_EVAL_THRESHOLD=0.7
""")
        print_green("Created .chase/.env")

    print_green("Done! Next steps:")
    print()
    print("  1. Edit MISSION.md with your goal")
    print("  2. Edit .chase/.env to configure your LLM provider")
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

    config = ChaseConfig.from_env(ws)
    orch = Orchestrator(config, state)
    return orch.run()


def cmd_status(args) -> int:
    ws = resolve_workspace(args.workspace)
    state = StateDir.for_workspace(ws)

    if not state.root.is_dir():
        print_red("No .chase/ found. Run 'chase init' first.")
        return 1

    print_bold("Chase Status")
    print(f"  Workspace: {ws}")
    print()

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
            title = "?"

        eval_path = state.sprint_eval(sid)
        if eval_path.exists():
            try:
                eval_data = json.loads(eval_path.read_text())
                verdict = eval_data.get("verdict", "?")
                score = eval_data.get("score", "?")
                if verdict == "PASS":
                    pass_count += 1
                    print(f"  \033[32m[PASS]\033[0m  Sprint {sid}: {title} (score: {score})")
                else:
                    fail_count += 1
                    print(f"  \033[31m[FAIL]\033[0m Sprint {sid}: {title} (score: {score})")
            except Exception:
                pend_count += 1
                print(f"  [----]  Sprint {sid}: {title}")
        else:
            pend_count += 1
            print(f"  [----]  Sprint {sid}: {title}")

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


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="chase",
        description="Chase — Multi-Agent autonomous development for Claude Code",
    )
    sub = parser.add_subparsers(dest="command")

    for name in ("init", "run", "resume", "status", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", default=None)

    # 注册 ray 子命令
    register_ray(sub)

    args = parser.parse_args()

    dispatch = {
        "init": cmd_init,
        "run": cmd_run,
        "resume": cmd_run,
        "status": cmd_status,
        "reset": cmd_reset,
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
