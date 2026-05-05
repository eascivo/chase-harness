"""守护进程 + PID + 信号处理。"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from chase.logging import ChaseLogger
from chase.ray.config import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_WAITING_APPROVAL,
    RayConfig,
    RayStateDir,
)
from chase.ray.monitor import POLL_INTERVAL, Monitor
from chase.ray.scheduler import Scheduler

# 全局引用，供信号处理器使用
_monitor: Monitor | None = None


def _handle_sigterm(signum: int, frame) -> None:
    """SIGTERM → 优雅停机。"""
    if _monitor:
        _monitor.request_stop()


def _handle_sigusr1(signum: int, frame) -> None:
    """SIGUSR1 → 重新加载 queue.json（在主循环中自动生效）。"""
    pass  # 主循环每轮都重新读取 queue.json


def daemonize(state: RayStateDir, logger: ChaseLogger) -> None:
    """双 fork 守护进程化。"""
    # 第一次 fork
    pid = os.fork()
    if pid > 0:
        # 父进程退出
        sys.exit(0)

    # 子进程成为会话组长
    os.setsid()

    # 第二次 fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 重定向标准 IO
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)

    # 写 PID 文件
    state.write_pid(os.getpid())


def run_loop(state: RayStateDir, logger: ChaseLogger) -> None:
    """主编排循环。前台和守护进程共用。"""
    global _monitor

    monitor = Monitor(state, logger)
    _monitor = monitor

    # 注册信号
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGUSR1, _handle_sigusr1)

    logger.info("编排循环启动")

    try:
        while True:
            # 1. 读取最新 queue.json
            config = state.load_queue()
            scheduler = Scheduler(config)

            # 2. 更新阻塞状态
            scheduler.update_blocked()

            # 3. 轮询运行中的子进程
            finished = monitor.poll()
            if finished:
                # 更新 queue.json 中完成项目的状态
                _sync_finished(state, config, finished)

            # 4. 停机检查
            if monitor.should_stop and monitor.active_count() == 0:
                logger.info("所有任务已完成，优雅退出")
                break

            # 5. 检查是否全部完成
            all_done = all(
                p.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_WAITING_APPROVAL)
                for p in config.projects
            )
            if all_done and monitor.active_count() == 0:
                logger.info("所有项目已完成")
                break

            # 6. 取可启动的项目并启动
            if not monitor.should_stop:
                dispatchable = scheduler.dispatchable()
                for project in dispatchable:
                    monitor.start_project(project)
                    # 同步状态到 queue.json
                    state.save_queue(config)

            # 7. 持久化当前状态
            state.save_queue(config)

            # 8. 等待下一轮
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("收到中断信号")
        monitor.request_stop()
        monitor.terminate_all()
        monitor.wait_all()
    finally:
        state.remove_pid()
        _monitor = None


def _sync_finished(state: RayStateDir, config: RayConfig, finished: list) -> None:
    """将子进程完成状态同步回配置。"""
    # 重新加载 queue.json 以获取最新数据
    fresh = state.load_queue()
    finished_names = {p.name for p in finished}
    status_map = {p.name: p.status for p in finished}
    for p in fresh.projects:
        if p.name in finished_names:
            p.status = status_map[p.name]
    config.projects = fresh.projects
    config.max_parallel = fresh.max_parallel
    config.log_dir = fresh.log_dir


def generate_launchd_template(state: RayStateDir) -> str:
    """生成 macOS launchd plist 模板。"""
    python = sys.executable
    chase_cli = Path(sys.executable).parent / "chase"
    workdir = state.base
    plist = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.chase.ray</string>
    <key>ProgramArguments</key>
    <array>
        <string>{chase_cli}</string>
        <string>ray</string>
        <string>start</string>
        <string>--daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{state.log_dir}/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{state.log_dir}/launchd-stderr.log</string>
</dict>
</plist>
"""
    return plist
