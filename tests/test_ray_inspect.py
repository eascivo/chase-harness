from typing import Optional

from chase.fmt import green
from chase.ray.cli import cmd_inspect
from chase.ray.config import Project, RayConfig, RayStateDir


class Args:
    def __init__(self, cwd: str, name: str, sprint: Optional[int] = None):
        self.cwd = cwd
        self.name = name
        self.sprint = sprint


def test_inspect_shows_plan_preview(tmp_path, capsys):
    workspace = tmp_path / "api"
    chase_dir = workspace / ".chase"
    chase_dir.mkdir(parents=True)
    (chase_dir / "plan-preview.md").write_text("# Chase Plan Preview\n\nHello\n", encoding="utf-8")
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[Project(name="api", path=str(workspace))]))

    exit_code = cmd_inspect(Args(str(tmp_path), "api"))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "# Chase Plan Preview" in out


def test_inspect_shows_verification_cards(tmp_path, capsys):
    workspace = tmp_path / "api"
    sprints = workspace / ".chase" / "sprints"
    sprints.mkdir(parents=True)
    (sprints / "01-verification.md").write_text("# Sprint 1\n\nVerdict: PASS\n", encoding="utf-8")
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[Project(name="api", path=str(workspace))]))

    exit_code = cmd_inspect(Args(str(tmp_path), "api"))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "--- Sprint 1 ---" in out
    assert "Verdict:" in out
    assert green("PASS") in out


def test_inspect_nonexistent_project_returns_1(tmp_path, capsys):
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[]))

    exit_code = cmd_inspect(Args(str(tmp_path), "foo"))
    err = capsys.readouterr().err

    assert exit_code == 1
    assert "不存在" in err


def test_inspect_sprint_filter_only_shows_verification(tmp_path, capsys):
    workspace = tmp_path / "api"
    chase_dir = workspace / ".chase"
    sprints = chase_dir / "sprints"
    sprints.mkdir(parents=True)
    (chase_dir / "plan-preview.md").write_text("# Chase Plan Preview\n", encoding="utf-8")
    (sprints / "01-verification.md").write_text("# Sprint 1\n\nVerdict: PASS\n", encoding="utf-8")
    (sprints / "02-verification.md").write_text("# Sprint 2\n\nVerdict: FAIL\n", encoding="utf-8")
    state = RayStateDir(tmp_path)
    state.init_directories()
    state.save_queue(RayConfig(projects=[Project(name="api", path=str(workspace))]))

    exit_code = cmd_inspect(Args(str(tmp_path), "api", sprint=1))
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Chase Plan Preview" not in out
    assert "Sprint 1" in out
    assert "Sprint 2" not in out
