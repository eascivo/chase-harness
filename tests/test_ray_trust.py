import json
from typing import Optional

from chase.logging import ChaseLogger
from chase.ray.cli import cmd_approve
from chase.ray.config import (
    STATUS_PENDING,
    STATUS_PLANNING,
    STATUS_WAITING_APPROVAL,
    Project,
    RayConfig,
    RayStateDir,
)
from chase.ray.monitor import Monitor
from chase.ray.scheduler import Scheduler


class Args:
    def __init__(self, cwd: str, name: Optional[str] = None):
        self.cwd = cwd
        self.name = name


class FakeProc:
    pid = 123

    def __init__(self, retcode=0):
        self._retcode = retcode

    def poll(self):
        return self._retcode


def test_project_approval_defaults_false_and_round_trips():
    project = Project(name="api", path="/tmp/api")

    data = project.to_dict()
    restored = Project.from_dict(data)

    assert data["approved"] is False
    assert restored.approved is False


def test_unapproved_project_runs_plan_then_waits_for_approval(tmp_path, monkeypatch):
    workspace = tmp_path / "api"
    workspace.mkdir()
    state = RayStateDir(tmp_path)
    logger = ChaseLogger(state.log_dir)
    monitor = Monitor(state, logger)
    project = Project(name="api", path=str(workspace))
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeProc(0)

    monkeypatch.setattr("chase.ray.monitor.subprocess.Popen", fake_popen)

    assert monitor.start_project(project) is True
    assert project.status == STATUS_PLANNING
    assert seen["cmd"][:2] == ["chase", "plan"]

    finished = monitor.poll()

    assert finished == [project]
    assert project.status == STATUS_WAITING_APPROVAL


def test_approved_project_runs_chase_run(tmp_path, monkeypatch):
    workspace = tmp_path / "api"
    workspace.mkdir()
    state = RayStateDir(tmp_path)
    logger = ChaseLogger(state.log_dir)
    monitor = Monitor(state, logger)
    project = Project(name="api", path=str(workspace), approved=True)
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        return FakeProc(0)

    monkeypatch.setattr("chase.ray.monitor.subprocess.Popen", fake_popen)

    assert monitor.start_project(project) is True

    assert seen["cmd"][:2] == ["chase", "run"]


def test_scheduler_does_not_dispatch_waiting_approval_projects():
    config = RayConfig(projects=[
        Project(name="api", path="/tmp/api", status=STATUS_WAITING_APPROVAL),
        Project(name="web", path="/tmp/web", status=STATUS_PENDING),
    ])

    dispatchable = Scheduler(config).dispatchable()

    assert [p.name for p in dispatchable] == ["web"]


def test_ray_approve_marks_project_approved_and_writes_chase_approval(tmp_path):
    workspace = tmp_path / "api"
    workspace.mkdir()
    state = RayStateDir(tmp_path)
    state.init_directories()
    config = RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_WAITING_APPROVAL),
    ])
    state.save_queue(config)

    exit_code = cmd_approve(Args(str(tmp_path), "api"))

    updated = state.load_queue().projects[0]
    approval_file = workspace / ".chase" / "approved.json"
    approval_data = json.loads(approval_file.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert updated.approved is True
    assert updated.status == STATUS_PENDING
    assert approval_data["approved"] is True
