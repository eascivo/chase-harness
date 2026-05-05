import json
from typing import Optional

from chase.ray.cli import cmd_approve
from chase.ray.config import STATUS_PENDING, STATUS_WAITING_APPROVAL, Project, RayConfig, RayStateDir


class Args:
    def __init__(self, cwd: str, name: Optional[str] = None, all_low_risk: bool = False):
        self.cwd = cwd
        self.name = name
        self.all_low_risk = all_low_risk


def _write_contract(workspace, sid, criteria, files, test_command):
    sprints = workspace / ".chase" / "sprints"
    sprints.mkdir(parents=True, exist_ok=True)
    (sprints / f"{sid:02d}-contract.md").write_text(json.dumps({
        "id": sid,
        "title": f"Sprint {sid}",
        "contract": {
            "criteria": criteria,
            "test_command": test_command,
        },
        "files_likely_touched": files,
    }), encoding="utf-8")


def test_approve_all_low_risk_only_approves_low_risk_projects(tmp_path, capsys):
    low = tmp_path / "low"
    high = tmp_path / "high"
    low.mkdir()
    high.mkdir()
    _write_contract(low, 1, ["One behavior"], ["one.py"], "pytest tests/test_one.py")
    _write_contract(high, 1, ["A", "B", "C", "D", "E"], ["a.py", "b.py", "c.py", "d.py"], "")
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[
        Project(name="low", path=str(low), status=STATUS_WAITING_APPROVAL),
        Project(name="high", path=str(high), status=STATUS_WAITING_APPROVAL),
    ]))

    exit_code = cmd_approve(Args(str(tmp_path), all_low_risk=True))
    out = capsys.readouterr().out
    projects = {p.name: p for p in state.load_queue().projects}

    assert exit_code == 0
    assert projects["low"].approved is True
    assert projects["low"].status == STATUS_PENDING
    assert (low / ".chase" / "approved.json").exists()
    assert projects["high"].approved is False
    assert projects["high"].status == STATUS_WAITING_APPROVAL
    assert not (high / ".chase" / "approved.json").exists()
    assert "Approved:" in out
    assert "low" in out
    assert "Skipped" in out
    assert "high" in out


def test_approve_all_low_risk_with_no_waiting_projects_is_noop(tmp_path, capsys):
    workspace = tmp_path / "api"
    workspace.mkdir()
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_PENDING),
    ]))

    exit_code = cmd_approve(Args(str(tmp_path), all_low_risk=True))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "No projects waiting for approval" in out


def test_single_project_approve_still_works_with_name(tmp_path):
    workspace = tmp_path / "api"
    workspace.mkdir()
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_WAITING_APPROVAL),
    ]))

    exit_code = cmd_approve(Args(str(tmp_path), name="api", all_low_risk=False))
    project = state.load_queue().projects[0]

    assert exit_code == 0
    assert project.approved is True
    assert project.status == STATUS_PENDING
