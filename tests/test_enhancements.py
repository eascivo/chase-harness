"""Tests for sprint branches, deterministic checks, and retry breakpoint logic."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from chase.cli import cmd_retry, cmd_status, cmd_logs, _find_last_failed_sprint
from chase.config import ChaseConfig
from chase.orchestrator import Orchestrator
from chase.state import StateDir
from chase.agents.evaluator import EvaluatorAgent


class Args:
    """Minimal args namespace matching CLI parser output."""
    def __init__(self, workspace: str = None, sprint_id: int = None,
                 force: bool = False, watch: bool = False,
                 tail: int = 20, all: bool = False,
                 sprint: int = None, agent: str = None):
        self.workspace = workspace
        self.sprint_id = sprint_id
        self.force = force
        self.watch = watch
        self.tail = tail
        self.all = all
        self.sprint = sprint
        self.agent = agent


def _init_workspace(tmp_path, *, with_mission: bool = True,
                    mission_text: str = "# Goal\nShip the feature\n",
                    with_sprints: bool = False,
                    init_git: bool = False) -> Path:
    """Create a minimal workspace with .chase/ structure."""
    ws = tmp_path
    state = StateDir.for_workspace(ws)
    state.init_directories()
    state.init_cost_file()
    if with_mission:
        (ws / "MISSION.md").write_text(mission_text, encoding="utf-8")
    if with_sprints:
        (state.sprints / "01-contract.md").write_text(json.dumps({
            "id": 1,
            "title": "Test sprint",
            "contract": {"criteria": ["Do the thing"], "test_command": "pytest"},
            "files_likely_touched": ["main.py"],
        }), encoding="utf-8")
    if init_git:
        subprocess.run(["git", "init"], capture_output=True, cwd=str(ws))
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       capture_output=True, cwd=str(ws))
        subprocess.run(["git", "config", "user.name", "Test"],
                       capture_output=True, cwd=str(ws))
        subprocess.run(["git", "add", "."], capture_output=True, cwd=str(ws))
        subprocess.run(["git", "commit", "-m", "init"], capture_output=True, cwd=str(ws))
    return ws


# ============================================================
# Sprint state management tests
# ============================================================

def test_sprint_state_path(tmp_path):
    """Sprint state file has correct path pattern."""
    state = StateDir.for_workspace(tmp_path)
    path = state.sprint_state(1)
    assert path == tmp_path / ".chase" / "sprints" / "01-state.json"
    path2 = state.sprint_state(5)
    assert path2 == tmp_path / ".chase" / "sprints" / "05-state.json"


def test_orchestrator_read_write_sprint_state(tmp_path):
    """Orchestrator can read and write sprint state."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    # Write state
    orch._write_sprint_state(1, {
        "branch": "chase/sprint-1",
        "base_branch": "main",
        "status": "running",
        "agent_chain": [{"agent": "Negotiator", "status": "success"}],
    })

    # Read it back
    data = orch._read_sprint_state(1)
    assert data["branch"] == "chase/sprint-1"
    assert data["status"] == "running"
    assert len(data["agent_chain"]) == 1


def test_orchestrator_update_sprint_agent(tmp_path):
    """Update sprint agent chain appends or updates entries."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    orch._update_sprint_agent(1, "Negotiator", "success")
    orch._update_sprint_agent(1, "Generator", "success")
    orch._update_sprint_agent(1, "Evaluator", "failed", "no JSON output")

    data = orch._read_sprint_state(1)
    assert data["agent_chain"][0]["agent"] == "Negotiator"
    assert data["agent_chain"][0]["status"] == "success"
    assert data["agent_chain"][2]["agent"] == "Evaluator"
    assert data["agent_chain"][2]["status"] == "failed"
    assert data["agent_chain"][2]["error"] == "no JSON output"


def test_orchestrator_update_sprint_status(tmp_path):
    """Update sprint overall status and finished_at."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    orch._update_sprint_status(1, "failed", "retries exhausted")
    data = orch._read_sprint_state(1)
    assert data["status"] == "failed"
    assert data["last_error"] == "retries exhausted"
    assert "finished_at" in data


def test_orchestrator_get_last_error(tmp_path):
    """Get last error from sprint state."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    # No state → empty string
    assert orch._get_last_error(1) == ""

    orch._update_sprint_status(1, "failed", "generator crashed")
    assert orch._get_last_error(1) == "generator crashed"


# ============================================================
# Branch management tests (with real git)
# ============================================================

def test_create_sprint_branch(tmp_path):
    """Orchestrator creates a sprint branch from base."""
    ws = _init_workspace(tmp_path, init_git=True)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    # Get current branch (should be main or master)
    base = orch._get_current_branch()
    assert base in ("main", "master")

    # Create sprint branch
    orch._create_sprint_branch(base, 1)

    current = orch._get_current_branch()
    assert current == "chase/sprint-1"
    assert orch._branch_exists("chase/sprint-1")


def test_merge_sprint_branch(tmp_path):
    """Orchestrator merges sprint branch back to base with --no-ff."""
    ws = _init_workspace(tmp_path, init_git=True)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    base = orch._get_current_branch()
    orch._create_sprint_branch(base, 1)

    # Make a commit on the sprint branch
    (ws / "test_file.txt").write_text("hello")
    subprocess.run(["git", "add", "test_file.txt"], capture_output=True, cwd=str(ws))
    subprocess.run(["git", "commit", "-m", "sprint 1: add file"], capture_output=True, cwd=str(ws))

    # Merge back
    result = orch._merge_sprint_branch(1, base)
    assert result is True

    # Should be back on base branch
    current = orch._get_current_branch()
    assert current == base

    # File should exist
    assert (ws / "test_file.txt").exists()


def test_branch_exists(tmp_path):
    """branch_exists returns True/False correctly."""
    ws = _init_workspace(tmp_path, init_git=True)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    assert not orch._branch_exists("chase/sprint-1")
    base = orch._get_current_branch()
    orch._create_sprint_branch(base, 1)
    assert orch._branch_exists("chase/sprint-1")


def test_checkout_branch(tmp_path):
    """checkout_branch switches branches."""
    ws = _init_workspace(tmp_path, init_git=True)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    base = orch._get_current_branch()
    orch._create_sprint_branch(base, 1)
    assert orch._get_current_branch() == "chase/sprint-1"

    assert orch._checkout_branch(base) is True
    assert orch._get_current_branch() == base


# ============================================================
# Deterministic checks tests
# ============================================================

def test_deterministic_checks_parse_contract(tmp_path):
    """Evaluator parses contract to extract test_command and files."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    contract_path = state.sprint_contract(1)
    data = evaluator._parse_contract(contract_path)
    assert data is not None
    assert evaluator._get_test_command(data) == "pytest"


def test_deterministic_checks_no_test_command(tmp_path):
    """No test_command means test check is skipped."""
    ws = _init_workspace(tmp_path, with_sprints=False)
    state = StateDir.for_workspace(ws)
    state.init_directories()
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    # Contract without test_command
    (state.sprints / "01-contract.md").write_text(json.dumps({
        "id": 1,
        "title": "No test sprint",
        "contract": {"criteria": ["Do the thing"]},
    }), encoding="utf-8")

    contract_path = state.sprint_contract(1)
    data = evaluator._parse_contract(contract_path)
    assert evaluator._get_test_command(data) == ""


def test_run_command_success(tmp_path):
    """_run_command captures output for successful commands."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    result = evaluator._run_command("echo hello")
    assert result["ran"] is True
    assert result["passed"] is True
    assert "hello" in result["output"]


def test_run_command_failure(tmp_path):
    """_run_command captures output for failing commands."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    result = evaluator._run_command("exit 1")
    assert result["ran"] is True
    assert result["passed"] is False


def test_run_command_timeout(tmp_path):
    """_run_command handles timeouts."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    result = evaluator._run_command("sleep 60", timeout=1)
    assert result["ran"] is True
    assert result["passed"] is False
    assert "TIMEOUT" in result["output"]


def test_format_deterministic_evidence_all_passed(tmp_path):
    """Evidence formatting shows ALL PASSED when checks pass."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    results = {
        "test": {"ran": True, "passed": True, "output": "3 passed"},
        "lint": {"ran": True, "passed": True, "output": "(ruff: clean)"},
        "typecheck": None,
        "git_diff": "",
        "file_existence": {"checked": True, "missing": []},
        "all_passed": True,
        "fail_summary": "",
    }
    evidence = evaluator._format_deterministic_evidence(results)
    assert "ALL DETERMINISTIC CHECKS PASSED" in evidence
    assert "Tests: PASSED" in evidence


def test_format_deterministic_evidence_failures(tmp_path):
    """Evidence formatting shows FAILED and score cap warning."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    results = {
        "test": {"ran": True, "passed": False, "output": "1 failed"},
        "lint": {"ran": False, "passed": False, "output": ""},
        "typecheck": None,
        "git_diff": "",
        "file_existence": {"checked": True, "missing": ["missing.py"]},
        "all_passed": False,
        "fail_summary": "tests failed (pytest); missing files: missing.py",
    }
    evidence = evaluator._format_deterministic_evidence(results)
    assert "SOME DETERMINISTIC CHECKS FAILED" in evidence
    assert "score should not exceed 0.5" in evidence
    assert "Tests: FAILED" in evidence
    assert "MISSING 1 file" in evidence


def test_detect_and_run_no_tool(tmp_path):
    """detect_and_run returns None when no tool is found."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    evaluator = EvaluatorAgent(state, config)

    # In a clean tmp dir, no lint/typecheck tools should be configured
    # (they may exist on the system but won't find config in the tmp dir)
    # This test just checks the method runs without error
    result = evaluator._detect_and_run("typecheck")
    # Result can be None (no tool found) or a dict (tool found but ran)
    assert result is None or isinstance(result, dict)


# ============================================================
# Retry breakpoint logic tests
# ============================================================

def test_retry_auto_detect_last_failed(tmp_path):
    """_find_last_failed_sprint finds the last failed sprint."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    # Add a second sprint
    (state.sprints / "02-contract.md").write_text(json.dumps({
        "id": 2, "title": "Sprint 2",
        "contract": {"criteria": ["Another thing"]},
    }), encoding="utf-8")

    # Sprint 1 passed, sprint 2 failed
    (state.sprints / "01-eval.json").write_text(json.dumps({"verdict": "PASS", "score": 1.0}))
    (state.sprints / "02-eval.json").write_text(json.dumps({"verdict": "FAIL", "score": 0.3}))

    result = _find_last_failed_sprint(state)
    assert result == 2


def test_retry_auto_detect_no_failures(tmp_path):
    """_find_last_failed_sprint returns None when all pass."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)
    (state.sprints / "01-eval.json").write_text(json.dumps({"verdict": "PASS", "score": 1.0}))

    result = _find_last_failed_sprint(state)
    assert result is None


def test_retry_auto_detect_from_state(tmp_path):
    """_find_last_failed_sprint uses sprint state when no eval exists."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    # Sprint 1 has state marked failed but no eval (interrupted)
    (state.sprints / "01-state.json").write_text(json.dumps({
        "status": "failed",
        "last_error": "interrupted",
    }))

    result = _find_last_failed_sprint(state)
    assert result == 1


def test_retry_eval_only_mode(tmp_path, capsys):
    """Retry with evaluator failure keeps generator result."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    # Generator produced a result
    (state.sprints / "01-result.md").write_text("Sprint result content")
    # Evaluator failed
    (state.sprints / "01-eval.json").write_text(json.dumps({"verdict": "FAIL", "score": 0.3}))
    # Sprint state shows evaluator failed
    (state.sprints / "01-state.json").write_text(json.dumps({
        "branch": "chase/sprint-1",
        "status": "failed",
        "agent_chain": [
            {"agent": "Negotiator", "status": "success"},
            {"agent": "Generator", "status": "success"},
            {"agent": "Evaluator", "status": "failed", "error": "score below threshold"},
        ],
    }))

    args = Args(workspace=str(ws), sprint_id=1)
    rc = cmd_retry(args)

    assert rc == 0
    # Eval should be removed
    assert not state.sprint_eval(1).exists()
    # Result should be kept (eval-only retry)
    assert state.sprint_result(1).exists()
    out = capsys.readouterr().out
    assert "eval-only retry" in out


def test_retry_full_mode(tmp_path, capsys):
    """Retry with generator failure — no files to clear since generator never produced output."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    # Generator failed — no result, no eval
    # Sprint state shows generator failed
    (state.sprints / "01-state.json").write_text(json.dumps({
        "branch": "chase/sprint-1",
        "status": "failed",
        "agent_chain": [
            {"agent": "Negotiator", "status": "success"},
            {"agent": "Generator", "status": "failed", "error": "empty output"},
        ],
    }))

    args = Args(workspace=str(ws), sprint_id=1)
    rc = cmd_retry(args)

    assert rc == 0
    out = capsys.readouterr().out
    # No files to clear, but retry still succeeds
    assert "Sprint 1" in out


def test_retry_no_args_finds_failed(tmp_path, capsys):
    """retry with no args auto-detects last failed sprint."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    # Sprint 1 failed
    (state.sprints / "01-eval.json").write_text(json.dumps({"verdict": "FAIL", "score": 0.3}))
    (state.sprints / "01-result.md").write_text("result")

    args = Args(workspace=str(ws), sprint_id=None)
    rc = cmd_retry(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Retrying last failed sprint: 1" in out


# ============================================================
# Enhanced status tests
# ============================================================

def test_status_shows_branch_info(tmp_path, capsys):
    """Status output includes branch name from sprint state."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    # Add sprint state with branch info
    (state.sprints / "01-state.json").write_text(json.dumps({
        "branch": "chase/sprint-1",
        "base_branch": "main",
        "status": "failed",
        "last_error": "score below threshold",
        "agent_chain": [
            {"agent": "Negotiator", "status": "success"},
            {"agent": "Generator", "status": "success"},
            {"agent": "Evaluator", "status": "failed"},
        ],
    }))

    args = Args(workspace=str(ws))
    rc = cmd_status(args)
    out = capsys.readouterr().out

    assert rc == 0
    assert "branch: chase/sprint-1" in out
    assert "status: failed" in out
    assert "error: score below threshold" in out
    # Agent chain display
    assert "Negotiator" in out
    assert "Generator" in out
    assert "Evaluator" in out


def test_status_shows_chain_for_pending(tmp_path, capsys):
    """Status shows agent chain for sprints without eval."""
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    (state.sprints / "01-state.json").write_text(json.dumps({
        "branch": "chase/sprint-1",
        "status": "running",
        "agent_chain": [
            {"agent": "Negotiator", "status": "success"},
            {"agent": "Generator", "status": "running"},
        ],
    }))

    args = Args(workspace=str(ws))
    rc = cmd_status(args)
    out = capsys.readouterr().out

    assert rc == 0
    assert "chain:" in out


# ============================================================
# Sprint log tests
# ============================================================

def test_logs_shows_sprint_log(tmp_path, capsys):
    """chase logs <sprint_id> shows sprint-specific log."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)

    # Create a sprint log file
    (state.logs / "sprint-1.log").write_text(
        "[10:00:00] INFO: [Sprint 1/generator] Working...\n"
        "[10:01:00] INFO: [Sprint 1/evaluator] Evaluating...\n"
        "[10:02:00] ERROR: [Sprint 1/evaluator] Failed\n"
    )

    args = Args(workspace=str(ws), sprint_id=1)
    rc = cmd_logs(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "generator" in out
    assert "evaluator" in out


def test_logs_sprint_not_found(tmp_path, capsys):
    """chase logs <sprint_id> handles missing sprint log gracefully."""
    ws = _init_workspace(tmp_path)
    state = StateDir.for_workspace(ws)
    state.init_directories()

    args = Args(workspace=str(ws), sprint_id=99)
    rc = cmd_logs(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "No sprint log found" in out or "No log entries" in out


# ============================================================
# Sprint log file creation test
# ============================================================

def test_sprint_log_file_created(tmp_path):
    """ChaseLogger.sprint writes to both daily log and sprint-specific log."""
    from chase.logging import ChaseLogger

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    logger = ChaseLogger(log_dir)

    logger.sprint(1, "generator", "Starting implementation")

    sprint_log = log_dir / "sprint-1.log"
    assert sprint_log.exists()
    content = sprint_log.read_text()
    assert "generator" in content
    assert "Starting implementation" in content
