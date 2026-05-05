import json
from pathlib import Path

from chase.cli import cmd_approve, cmd_plan, cmd_status
from chase.config import ChaseConfig
from chase.orchestrator import Orchestrator
from chase.state import StateDir


class Args:
    def __init__(self, workspace: str):
        self.workspace = workspace


def test_cmd_approve_writes_approval_file(tmp_path):
    ws = tmp_path
    (ws / ".chase").mkdir()
    (ws / "MISSION.md").write_text("# Goal\nShip trust layer\n", encoding="utf-8")

    exit_code = cmd_approve(Args(str(ws)))

    assert exit_code == 0
    data = json.loads((ws / ".chase" / "approved.json").read_text(encoding="utf-8"))
    assert data["approved"] is True


def test_cmd_plan_renders_preview_from_existing_contracts(tmp_path):
    ws = tmp_path
    sprints = ws / ".chase" / "sprints"
    sprints.mkdir(parents=True)
    (ws / ".chase" / "handoffs").mkdir()
    (ws / ".chase" / "logs").mkdir()
    (ws / "MISSION.md").write_text("# Goal\nAdd validation\n", encoding="utf-8")
    (sprints / "01-contract.md").write_text(json.dumps({
        "id": 1,
        "title": "Add validation",
        "description": "Validate user input.",
        "contract": {
            "criteria": ["Reject empty input"],
            "test_command": "pytest tests/test_validation.py",
        },
        "files_likely_touched": ["app.py"],
    }), encoding="utf-8")

    exit_code = cmd_plan(Args(str(ws)))

    assert exit_code == 0
    preview = (ws / ".chase" / "plan-preview.md").read_text(encoding="utf-8")
    assert "Sprint 1: Add validation" in preview
    assert "Run `chase approve`" in preview


def test_cmd_plan_prefers_negotiated_contracts(tmp_path):
    ws = tmp_path
    sprints = ws / ".chase" / "sprints"
    sprints.mkdir(parents=True)
    (ws / ".chase" / "handoffs").mkdir()
    (ws / ".chase" / "logs").mkdir()
    (ws / "MISSION.md").write_text("# Goal\nAdd validation\n", encoding="utf-8")
    (sprints / "01-contract.md").write_text(json.dumps({
        "id": 1,
        "title": "Raw title",
        "contract": {"criteria": ["Raw criterion"], "test_command": ""},
    }), encoding="utf-8")
    (sprints / "01-negotiated.md").write_text(json.dumps({
        "sprint_id": 1,
        "title": "Negotiated title",
        "description": "Negotiated description",
        "negotiated_criteria": [
            {
                "id": "C1",
                "criterion": "Negotiated criterion",
                "verification": "Run pytest",
                "priority": "must",
            }
        ],
        "test_command": "pytest tests/test_validation.py",
        "files_likely_touched": ["validation.py"],
    }), encoding="utf-8")

    exit_code = cmd_plan(Args(str(ws)))

    assert exit_code == 0
    preview = (ws / ".chase" / "plan-preview.md").read_text(encoding="utf-8")
    assert "Sprint 1: Negotiated title" in preview
    assert "Negotiated criterion" in preview
    assert "Raw criterion" not in preview


def test_orchestrator_requires_approval_when_configured(tmp_path):
    ws = tmp_path
    state = StateDir.for_workspace(ws)
    state.init_directories()
    (ws / "MISSION.md").write_text("# Goal\nTest gate\n", encoding="utf-8")
    config = ChaseConfig(chase_home=Path.cwd(), workspace=ws, require_approval=True)
    orch = Orchestrator(config, state)

    assert orch._approval_granted() is False

    state.approval_file.write_text(json.dumps({"approved": True}), encoding="utf-8")

    assert orch._approval_granted() is True


def test_status_prints_failure_reason_and_evidence_path(tmp_path, capsys):
    ws = tmp_path
    sprints = ws / ".chase" / "sprints"
    logs = ws / ".chase" / "logs"
    sprints.mkdir(parents=True)
    (ws / ".chase" / "handoffs").mkdir()
    logs.mkdir()
    (logs / "cost-tracking.json").write_text('{"total_cost": 0.0, "sprints": []}\n', encoding="utf-8")
    (ws / "MISSION.md").write_text("# Goal\nStatus test\n", encoding="utf-8")
    (sprints / "01-contract.md").write_text(json.dumps({
        "id": 1,
        "title": "Fix login",
        "contract": {"criteria": ["Reject invalid login"], "test_command": ""},
        "files_likely_touched": ["login.py", "auth.py", "views.py", "tests.py"],
    }), encoding="utf-8")
    (sprints / "01-eval.json").write_text(json.dumps({
        "verdict": "FAIL",
        "score": 0.4,
        "feedback": "function is missing",
    }), encoding="utf-8")
    (sprints / "01-verification.md").write_text("# evidence\n", encoding="utf-8")

    exit_code = cmd_status(Args(str(ws)))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "reason: implementation_incomplete" in out
    assert "risk: high" in out
    assert "01-verification.md" in out
