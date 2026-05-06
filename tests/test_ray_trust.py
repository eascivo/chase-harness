import json
from typing import Optional

from chase.logging import ChaseLogger
from chase.ray.cli import cmd_approve, cmd_sync
from chase.ray.config import (
    STATUS_COMPLETED,
    STATUS_NEEDS_REVIEW,
    STATUS_PENDING,
    STATUS_PLANNING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    Project,
    RayConfig,
    RayStateDir,
)
from chase.ray.monitor import Monitor
from chase.ray.scheduler import Scheduler
from chase.ray.sync import sync_config


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


def test_sync_reads_manual_chase_approval(tmp_path):
    workspace = tmp_path / "api"
    chase_dir = workspace / ".chase"
    chase_dir.mkdir(parents=True)
    (chase_dir / "approved.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    config = RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_WAITING_APPROVAL),
    ])

    sync_config(config)

    project = config.projects[0]
    assert project.approved is True
    assert project.status == STATUS_PENDING
    assert project.approved_at is not None


def test_sync_moves_plan_preview_to_waiting_approval(tmp_path):
    workspace = tmp_path / "api"
    chase_dir = workspace / ".chase"
    chase_dir.mkdir(parents=True)
    (chase_dir / "plan-preview.md").write_text("# plan\n", encoding="utf-8")
    config = RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_PENDING),
    ])

    sync_config(config)

    assert config.projects[0].status == STATUS_WAITING_APPROVAL
    assert config.projects[0].planned_at is not None


def test_sync_marks_all_passed_evals_completed(tmp_path):
    workspace = tmp_path / "api"
    sprints = workspace / ".chase" / "sprints"
    sprints.mkdir(parents=True)
    (sprints / "01-contract.md").write_text("{}", encoding="utf-8")
    (sprints / "01-eval.json").write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    config = RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_WAITING_APPROVAL),
    ])

    sync_config(config)

    project = config.projects[0]
    assert project.status == STATUS_COMPLETED
    assert project.approved is False
    assert project.completed_at is not None


def test_sync_marks_failed_evals_needs_review(tmp_path):
    workspace = tmp_path / "api"
    sprints = workspace / ".chase" / "sprints"
    sprints.mkdir(parents=True)
    (sprints / "01-contract.md").write_text("{}", encoding="utf-8")
    (sprints / "01-eval.json").write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
    config = RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_WAITING_APPROVAL),
    ])

    sync_config(config)

    project = config.projects[0]
    assert project.status == STATUS_NEEDS_REVIEW
    assert project.approved is False
    assert project.needs_review_at is not None


def test_sync_does_not_override_active_projects(tmp_path):
    workspace = tmp_path / "api"
    chase_dir = workspace / ".chase"
    chase_dir.mkdir(parents=True)
    (chase_dir / "approved.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    config = RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_RUNNING),
        Project(name="web", path=str(workspace), status=STATUS_PLANNING),
    ])

    sync_config(config)

    assert config.projects[0].status == STATUS_RUNNING
    assert config.projects[0].approved is False
    assert config.projects[0].run_at is not None
    assert config.projects[1].status == STATUS_PLANNING
    assert config.projects[1].approved is False


def test_ray_sync_command_persists_project_updates(tmp_path):
    workspace = tmp_path / "api"
    chase_dir = workspace / ".chase"
    chase_dir.mkdir(parents=True)
    (chase_dir / "approved.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[
        Project(name="api", path=str(workspace), status=STATUS_WAITING_APPROVAL),
    ]))

    exit_code = cmd_sync(Args(str(tmp_path)))

    project = state.load_queue().projects[0]
    assert exit_code == 0
    assert project.approved is True
    assert project.status == STATUS_PENDING
