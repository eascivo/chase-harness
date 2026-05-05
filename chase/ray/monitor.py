"""子进程管理 + 状态轮询。"""

from __future__ import annotations

import signal
import subprocess
from datetime import datetime
from pathlib import Path

from chase.logging import ChaseLogger
from chase.ray.config import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_PENDING,
    STATUS_PLANNING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    Project,
    RayConfig,
    RayStateDir,
)
from chase.ray.scheduler import Scheduler

# 轮询间隔（秒）
POLL_INTERVAL = 5


class ProcessSlot:
    """一个正在运行的 chase 子进程。"""

    def __init__(self, project: Project, proc: subprocess.Popen, log_file, target_status: str):
        self.project = project
        self.proc = proc
        self.log_file = log_file
        self.target_status = target_status
        self.started_at = datetime.now()

    @property
    def elapsed(self) -> str:
        delta = datetime.now() - self.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        return f"{minutes}m{seconds:02d}s"


class Monitor:
    """管理所有子进程的生命周期。"""

    def __init__(self, state: RayStateDir, logger: ChaseLogger):
        self.state = state
        self.logger = logger
        self.slots: dict[str, ProcessSlot] = {}  # name -> slot
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True
        self.logger.info("收到停机信号，等待当前任务完成后退出")

    @property
    def should_stop(self) -> bool:
        return self._stop_requested

    def start_project(self, project: Project) -> bool:
        """为项目启动 chase plan 或 chase run 子进程。"""
        if project.name in self.slots:
            self.logger.error(f"项目 '{project.name}' 已在运行")
            return False

        workspace = Path(project.path).expanduser().resolve()
        if not workspace.is_dir():
            self.logger.error(f"项目路径不存在: {workspace}")
            project.status = STATUS_FAILED
            return False

        log_file = self.state.log_dir / f"{project.name}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            log_fh = open(log_file, "a", encoding="utf-8")
        except OSError as e:
            self.logger.error(f"无法打开日志文件 {log_file}: {e}")
            project.status = STATUS_FAILED
            return False

        try:
            if project.approved:
                cmd = ["chase", "run", "--workspace", str(workspace)]
                project.status = STATUS_RUNNING
                target_status = STATUS_COMPLETED
            else:
                cmd = ["chase", "plan", "--workspace", str(workspace)]
                project.status = STATUS_PLANNING
                target_status = STATUS_WAITING_APPROVAL
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError:
            self.logger.error("chase 命令未找到，请确认已安装")
            log_fh.close()
            project.status = STATUS_FAILED
            return False

        self.slots[project.name] = ProcessSlot(project, proc, log_fh, target_status)
        self.logger.info(
            f"启动项目 '{project.name}' (pid={proc.pid}, cmd={' '.join(cmd)}, workspace={workspace})"
        )
        return True

    def poll(self) -> list[Project]:
        """非阻塞轮询所有子进程，返回刚完成的项目列表。"""
        finished: list[Project] = []
        to_remove: list[str] = []

        for name, slot in self.slots.items():
            retcode = slot.proc.poll()
            if retcode is None:
                # 还在运行
                continue

            # 进程结束，关闭日志文件句柄
            try:
                slot.log_file.close()
            except Exception:
                pass

            if retcode == 0:
                slot.project.status = slot.target_status
                self.logger.info(f"项目 '{name}' 已完成")
            else:
                slot.project.status = STATUS_FAILED
                self.logger.error(f"项目 '{name}' 失败 (exit={retcode})")

            finished.append(slot.project)
            to_remove.append(name)

        for name in to_remove:
            del self.slots[name]

        return finished

    def pause_project(self, name: str) -> bool:
        """暂停指定项目（发送 SIGSTOP）。"""
        slot = self.slots.get(name)
        if not slot:
            return False
        try:
            slot.proc.send_signal(signal.SIGSTOP)
            slot.project.status = STATUS_PAUSED
            self.logger.info(f"项目 '{name}' 已暂停")
            return True
        except OSError as e:
            self.logger.error(f"暂停 '{name}' 失败: {e}")
            return False

    def resume_project(self, name: str) -> bool:
        """恢复指定项目（发送 SIGCONT）。"""
        slot = self.slots.get(name)
        if not slot:
            return False
        try:
            slot.proc.send_signal(signal.SIGCONT)
            slot.project.status = STATUS_RUNNING
            self.logger.info(f"项目 '{name}' 已恢复")
            return True
        except OSError as e:
            self.logger.error(f"恢复 '{name}' 失败: {e}")
            return False

    def terminate_all(self) -> None:
        """向所有子进程发送 SIGTERM（包含整个进程组）。"""
        import os as _os
        for name, slot in self.slots.items():
            try:
                _os.killpg(slot.proc.pid, signal.SIGTERM)
                self.logger.info(f"已发送 SIGTERM 给 '{name}' 进程组 (pid={slot.proc.pid})")
            except (OSError, ProcessLookupError):
                # Fallback: 只杀直接子进程
                try:
                    slot.proc.terminate()
                    self.logger.info(f"已发送 SIGTERM 给 '{name}' (pid={slot.proc.pid})")
                except OSError:
                    pass

    def wait_all(self, timeout: float = 30) -> None:
        """等待所有子进程退出。"""
        for slot in self.slots.values():
            try:
                slot.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                slot.proc.kill()
            try:
                slot.log_file.close()
            except Exception:
                pass
        self.slots.clear()

    def active_count(self) -> int:
        return len(self.slots)
