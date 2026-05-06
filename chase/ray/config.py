"""RAYSPACE 解析 + queue.json 管理。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目状态常量
STATUS_PENDING = "pending"
STATUS_PLANNING = "planning"
STATUS_WAITING_APPROVAL = "waiting_approval"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_FAILED = "failed"
STATUS_PAUSED = "paused"
STATUS_BLOCKED = "blocked"


@dataclass
class Project:
    """队列中的单个项目。"""

    name: str
    path: str
    priority: int = 0
    depends_on: list[str] = field(default_factory=list)
    status: str = STATUS_PENDING
    approved: bool = False
    planned_at: str | None = None
    approved_at: str | None = None
    run_at: str | None = None
    completed_at: str | None = None
    needs_review_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "priority": self.priority,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "approved": self.approved,
            "planned_at": self.planned_at,
            "approved_at": self.approved_at,
            "run_at": self.run_at,
            "completed_at": self.completed_at,
            "needs_review_at": self.needs_review_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Project:
        return cls(
            name=d["name"],
            path=d["path"],
            priority=d.get("priority", 0),
            depends_on=d.get("depends_on", []),
            status=d.get("status", STATUS_PENDING),
            approved=d.get("approved", False),
            planned_at=d.get("planned_at"),
            approved_at=d.get("approved_at"),
            run_at=d.get("run_at"),
            completed_at=d.get("completed_at"),
            needs_review_at=d.get("needs_review_at"),
        )


@dataclass
class RayConfig:
    """编排器全局配置。"""

    max_parallel: int = 2
    log_dir: str = ".chase-ray/logs"
    projects: list[Project] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "max_parallel": self.max_parallel,
            "log_dir": self.log_dir,
            "projects": [p.to_dict() for p in self.projects],
        }

    @classmethod
    def from_dict(cls, d: dict) -> RayConfig:
        return cls(
            max_parallel=d.get("max_parallel", 2),
            log_dir=d.get("log_dir", ".chase-ray/logs"),
            projects=[Project.from_dict(p) for p in d.get("projects", [])],
        )


class RayStateDir:
    """管理 .chase-ray/ 目录结构。"""

    def __init__(self, base: Path):
        self.base = base.resolve()
        self.root = self.base / ".chase-ray"

    @property
    def queue_file(self) -> Path:
        return self.root / "queue.json"

    @property
    def pid_file(self) -> Path:
        return self.root / "ray.pid"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def rayspace_file(self) -> Path:
        return self.base / "RAYSPACE.md"

    def init_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def load_queue(self) -> RayConfig:
        """从 queue.json 读取配置，不存在则返回空配置。"""
        if not self.queue_file.exists():
            return RayConfig()
        try:
            data = json.loads(self.queue_file.read_text(encoding="utf-8"))
            return RayConfig.from_dict(data)
        except json.JSONDecodeError:
            bad_path = self.queue_file.with_suffix(".json.bad")
            self.queue_file.rename(bad_path)
            logger.warning(f"Queue file corrupt, renamed to {bad_path}. Starting with empty queue.")
            return RayConfig()
        except KeyError:
            return RayConfig()

    def save_queue(self, config: RayConfig) -> None:
        """将配置写入 queue.json。"""
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue_file.write_text(
            json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def init_rayspace(self) -> None:
        """生成 RAYSPACE.md 模板。"""
        if self.rayspace_file.exists():
            return
        template = """\
# Chase Ray — 多项目编排

max_parallel: 2
log_dir: .chase-ray/logs

## 项目队列

| name | path | priority | depends_on | status |
|------|------|----------|------------|--------|
"""
        self.rayspace_file.write_text(template, encoding="utf-8")

    def init_queue(self) -> None:
        """生成默认 queue.json。"""
        if self.queue_file.exists():
            return
        self.init_directories()
        config = RayConfig()
        self.save_queue(config)

    def read_pid(self) -> int | None:
        """读取 PID 文件，返回守护进程 PID。"""
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def write_pid(self, pid: int) -> None:
        self.pid_file.write_text(str(pid), encoding="utf-8")

    def remove_pid(self) -> None:
        if self.pid_file.exists():
            self.pid_file.unlink()
