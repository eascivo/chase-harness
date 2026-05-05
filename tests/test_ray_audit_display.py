from typing import Optional

from chase.ray.cli import cmd_log, cmd_status
from chase.ray.config import Project, RayConfig, RayStateDir


class Args:
    def __init__(self, cwd: str, name: Optional[str] = None):
        self.cwd = cwd
        self.name = name


def test_status_prints_last_event_column(tmp_path, capsys):
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[
        Project(
            name="api",
            path=str(tmp_path / "api"),
            approved_at="2026-05-05T10:30:00Z",
        ),
    ]))

    exit_code = cmd_status(Args(str(tmp_path)))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Last Event" in out
    assert "approved 2026-05-05T10:30:00Z" in out


def test_ray_log_prints_timeline_in_chronological_order(tmp_path, capsys):
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[
        Project(
            name="api",
            path=str(tmp_path / "api"),
            completed_at="2026-05-05T10:40:00Z",
            planned_at="2026-05-05T10:10:00Z",
            approved_at="2026-05-05T10:20:00Z",
            run_at="2026-05-05T10:30:00Z",
        ),
    ]))

    exit_code = cmd_log(Args(str(tmp_path), "api"))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert out.index("planned - 2026-05-05T10:10:00Z") < out.index("approved - 2026-05-05T10:20:00Z")
    assert out.index("approved - 2026-05-05T10:20:00Z") < out.index("run - 2026-05-05T10:30:00Z")
    assert out.index("run - 2026-05-05T10:30:00Z") < out.index("completed - 2026-05-05T10:40:00Z")


def test_ray_log_missing_project_returns_1(tmp_path, capsys):
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[]))

    exit_code = cmd_log(Args(str(tmp_path), "api"))
    err = capsys.readouterr().err

    assert exit_code == 1
    assert "api" in err
