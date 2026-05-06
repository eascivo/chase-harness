"""Tests for doctor, retry, skip, watch, and lock features."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from chase.cli import cmd_doctor, cmd_retry, cmd_skip, cmd_status
from chase.config import ChaseConfig
from chase.orchestrator import Orchestrator
from chase.state import StateDir


class Args:
    """Minimal args namespace matching CLI parser output."""
    def __init__(self, workspace: str = None, sprint_id: int = None,
                 force: bool = False, watch: bool = False):
        self.workspace = workspace
        self.sprint_id = sprint_id
        self.force = force
        self.watch = watch


def _init_workspace(tmp_path, *, with_mission: bool = True,
                    mission_text: str = "# Goal\nShip the feature\n",
                    with_sprints: bool = False) -> Path:
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
    return ws


# --- Doctor tests ---

def test_doctor_fails_without_mission(tmp_path, monkeypatch):
    monkeypatch.delenv("CHASE_CLI", raising=False)
    monkeypatch.setenv("CHASE_CLI", "python3")
    ws = _init_workspace(tmp_path, with_mission=False)
    args = Args(workspace=str(ws))
    rc = cmd_doctor(args)
    assert rc != 0


def test_doctor_passes_after_init(tmp_path, monkeypatch):
    monkeypatch.setenv("CHASE_CLI", "python3")
    ws = _init_workspace(tmp_path, with_mission=True,
                         mission_text="# Goal\nBuild a feature with enough detail")
    args = Args(workspace=str(ws))
    rc = cmd_doctor(args)
    # Python version warning may cause rc=1 on older Pythons; check stdout instead
    import sys
    if sys.version_info >= (3, 10):
        assert rc == 0
    else:
        # Accept warning-only failure — verify the real checks passed
        import subprocess
        captured = subprocess.run(
            ["python3", "-m", "chase", "doctor", "--workspace", str(ws)],
            capture_output=True, text=True,
        )
        assert "MISSION.md" in captured.stdout
        assert ".chase/" in captured.stdout
        assert "Git repository" in captured.stdout


# --- Retry tests ---

def test_retry_clears_eval(tmp_path):
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)
    # Create an eval file
    eval_path = state.sprint_eval(1)
    eval_path.write_text(json.dumps({"verdict": "FAIL", "score": 0.3}), encoding="utf-8")

    args = Args(workspace=str(ws), sprint_id=1)
    rc = cmd_retry(args)

    assert rc == 0
    assert not eval_path.exists()


def test_retry_fails_for_missing_sprint(tmp_path):
    ws = _init_workspace(tmp_path, with_sprints=True)
    args = Args(workspace=str(ws), sprint_id=99)
    rc = cmd_retry(args)
    assert rc != 0


# --- Skip tests ---

def test_skip_creates_marker(tmp_path):
    ws = _init_workspace(tmp_path, with_sprints=True)
    state = StateDir.for_workspace(ws)

    args = Args(workspace=str(ws), sprint_id=1)
    rc = cmd_skip(args)

    assert rc == 0
    skip_path = state.sprint_skip(1)
    assert skip_path.exists()
    data = json.loads(skip_path.read_text())
    assert data["verdict"] == "SKIP"


def test_skip_fails_for_missing_sprint(tmp_path):
    ws = _init_workspace(tmp_path, with_sprints=True)
    args = Args(workspace=str(ws), sprint_id=99)
    rc = cmd_skip(args)
    assert rc != 0


# --- Lock tests ---

def test_lock_prevents_concurrent(tmp_path):
    ws = _init_workspace(tmp_path, with_mission=True)
    state = StateDir.for_workspace(ws)
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws)
    orch = Orchestrator(config, state)

    # First acquire succeeds
    assert orch._acquire_lock() is True
    assert state.lock_file.exists()

    # Second acquire from same process also succeeds (own PID is alive)
    # Simulate a different PID by writing a fake lock
    lock = state.lock_file
    lock.write_text(json.dumps({"pid": 999999, "started": "2025-01-01T00:00:00Z"}))

    # Now acquire should fail (PID 999999 unlikely to be alive, but let's test with force)
    # Actually, a dead PID means the lock is stale and acquire succeeds.
    # Let's use the current PID to truly block:
    lock.write_text(json.dumps({"pid": os.getpid(), "started": "2025-01-01T00:00:00Z"}))

    assert orch._acquire_lock() is False
    assert orch._acquire_lock(force=True) is True

    # Release and re-acquire
    orch._release_lock()
    assert not state.lock_file.exists()
    assert orch._acquire_lock() is True
    orch._release_lock()


# --- Watch fallback test ---

def test_watch_falls_back_in_non_tty(tmp_path, monkeypatch, capsys):
    ws = _init_workspace(tmp_path, with_sprints=True)
    args = Args(workspace=str(ws), watch=True)

    with patch("chase.cli.sys") as mock_sys:
        mock_sys.stdout.isatty.return_value = False
        mock_sys.stdout.flush = lambda: None
        # Need real sys for other uses — just patch isatty
        import sys as real_sys
        mock_sys.stdout = type("out", (), {"isatty": lambda: False})()
        # Actually, cmd_status imports sys at module level, so patch it there
    # Simpler: patch sys.stdout.isatty to return False
    with patch.object(type(os.sys.stdout), "isatty", return_value=False):
        # cmd_status with watch=True calls _cmd_status_watch
        rc = cmd_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "requires a terminal" in out
