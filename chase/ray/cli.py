"""Ray 子命令入口。"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

from chase.fmt import bold, green, red, yellow, print_bold, print_green, print_red, print_yellow
from chase.logging import ChaseLogger
from chase.ray.config import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_PAUSED,
    STATUS_PENDING,
    STATUS_PLANNING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    Project,
    RayStateDir,
)
from chase.ray.daemon import daemonize, generate_launchd_template, run_loop
from chase.ray.sync import sync_config
from chase.trust import estimate_contract_risk


def _state(args=None) -> RayStateDir:
    cwd = getattr(args, "cwd", None) if args else None
    return RayStateDir(Path(cwd) if cwd else Path.cwd())


def _logger(state: RayStateDir) -> ChaseLogger:
    state.log_dir.mkdir(parents=True, exist_ok=True)
    return ChaseLogger(state.log_dir)


# 状态 → 显示颜色
_STATUS_COLORS = {
    STATUS_PENDING: lambda s: s,
    STATUS_PLANNING: lambda s: f"\033[36m{s}\033[0m",
    STATUS_WAITING_APPROVAL: lambda s: f"\033[33m{s}\033[0m",
    STATUS_RUNNING: lambda s: green(s),
    STATUS_COMPLETED: lambda s: green(s),
    STATUS_NEEDS_REVIEW: lambda s: f"\033[33m{s}\033[0m",
    STATUS_FAILED: lambda s: f"\033[31m{s}\033[0m",
    STATUS_PAUSED: lambda s: f"\033[33m{s}\033[0m",
    STATUS_BLOCKED: lambda s: f"\033[33m{s}\033[0m",
}


def cmd_init(args) -> int:
    """创建 RAYSPACE.md 和 .chase-ray/queue.json。"""
    state = _state(args)
    state.init_directories()
    state.init_rayspace()
    state.init_queue()
    print_green("Chase Ray 初始化完成:")
    print(f"  {state.rayspace_file}")
    print(f"  {state.queue_file}")
    print()
    print("下一步:")
    print("  1. 编辑 queue.json 添加项目")
    print("  2. 运行 chase ray start 启动编排")
    return 0


def cmd_start(args) -> int:
    """启动编排循环。"""
    state = _state(args)
    config = state.load_queue()
    sync_config(config)
    state.save_queue(config)

    if not config.projects:
        print_red("队列为空。请先编辑 queue.json 或使用 chase ray dispatch 添加项目")
        return 1

    logger = _logger(state)

    if getattr(args, "daemon", False):
        # 检查是否已有守护进程在运行
        pid = state.read_pid()
        if pid and _pid_alive(pid):
            print_red(f"Ray 已在运行 (pid={pid})")
            return 1
        daemonize(state, logger)

    # Dashboard（daemonize 之后启动，确保在正确进程中）
    dashboard_server = None
    if getattr(args, "dashboard", False):
        from chase.ray.dashboard import start_dashboard
        port = getattr(args, "dashboard_port", 8765)
        dashboard_server = start_dashboard(state, port, background=True)
        logger.info(f"Dashboard 启动在 http://localhost:{port}")

    try:
        run_loop(state, logger)
    finally:
        if dashboard_server:
            dashboard_server.stop()
    return 0


def cmd_dispatch(args) -> int:
    """动态派发新项目到队列。"""
    state = _state(args)
    config = state.load_queue()

    name = args.name
    path = str(Path(args.path).resolve())

    # 检查重名
    if any(p.name == name for p in config.projects):
        print_red(f"项目 '{name}' 已存在")
        return 1

    depends_on = []
    if getattr(args, "depends_on", None):
        depends_on = [d.strip() for d in args.depends_on.split(",") if d.strip()]

    priority = getattr(args, "priority", 0)

    project = Project(
        name=name,
        path=path,
        priority=priority,
        depends_on=depends_on,
        status=STATUS_PENDING,
    )
    config.projects.append(project)
    state.save_queue(config)

    print_green(f"已派发项目 '{name}' (priority={priority})")
    return 0


def cmd_approve(args) -> int:
    """审批某项目，允许 Ray 下一轮执行 chase run。"""
    state = _state(args)
    config = state.load_queue()

    if getattr(args, "all_low_risk", False):
        return _approve_all_low_risk(state, config)

    name = args.name
    if not name:
        print_red("项目名称必填，或使用 --all-low-risk")
        return 1
    project = _find_project(config, name)
    if not project:
        print_red(f"项目 '{name}' 不存在")
        return 1

    _approve_project(project, "chase ray approve")
    state.save_queue(config)
    print_green(f"已审批项目 '{name}'，下一轮将执行 chase run")
    return 0


def _approve_all_low_risk(state: RayStateDir, config) -> int:
    waiting = [p for p in config.projects if p.status == STATUS_WAITING_APPROVAL]
    if not waiting:
        print("No projects waiting for approval.")
        return 0

    approved: list[str] = []
    skipped: list[tuple[str, str]] = []
    for project in waiting:
        risk = _project_max_risk(project)
        if risk == "low":
            _approve_project(project, "chase ray approve --all-low-risk")
            approved.append(project.name)
        else:
            skipped.append((project.name, risk))

    state.save_queue(config)
    if approved:
        print("Approved:")
        for name in approved:
            print_green(f"  [approved] {name}")
    if skipped:
        print("Skipped - manual review required:")
        for name, risk in skipped:
            print_yellow(f"  [skipped] {name} ({risk})")
    return 0


def _approve_project(project: Project, source: str) -> None:
    project.approved = True
    if project.status == STATUS_WAITING_APPROVAL:
        project.status = STATUS_PENDING

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if project.approved_at is None:
        project.approved_at = now

    workspace = Path(project.path).expanduser().resolve()
    chase_dir = workspace / ".chase"
    chase_dir.mkdir(parents=True, exist_ok=True)
    approval_file = chase_dir / "approved.json"
    approval_file.write_text(json.dumps({
        "approved": True,
        "approved_at": now,
        "source": source,
    }, indent=2) + "\n", encoding="utf-8")


def _project_max_risk(project: Project) -> str:
    contracts = _project_contracts(project)
    if not contracts:
        return "unknown"
    risks = [estimate_contract_risk(contract) for contract in contracts]
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    return "low"


def _project_contracts(project: Project) -> list[dict]:
    sprints_dir = Path(project.path).expanduser().resolve() / ".chase" / "sprints"
    if not sprints_dir.is_dir():
        return []
    contracts = []
    for path in sorted(sprints_dir.glob("*-contract.md")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            contracts.append(data)
    return contracts


def cmd_status(args) -> int:
    """显示所有项目状态汇总。"""
    state = _state(args)

    if not state.queue_file.exists():
        print_yellow("Ray 未初始化。运行 chase ray init")
        return 1

    config = state.load_queue()
    sync_config(config)
    state.save_queue(config)

    if not config.projects:
        print("队列为空")
        return 0

    # 检查守护进程状态
    pid = state.read_pid()
    if pid and _pid_alive(pid):
        print(bold(f"Ray 运行中 (pid={pid})"))
    else:
        print("Ray 未运行")
    print(f"  max_parallel: {config.max_parallel}")
    print()

    # 表格头
    print(f"  {'名称':<20} {'优先级':<8} {'状态':<18} {'审批':<8} {'依赖':<16} {'Last Event'}")
    print(f"  {'----':<20} {'------':<8} {'----':<18} {'----':<8} {'----':<16} {'----------'}")

    for p in config.projects:
        deps = ",".join(p.depends_on) if p.depends_on else "-"
        color_fn = _STATUS_COLORS.get(p.status, lambda s: s)
        status_str = color_fn(p.status)
        approved = "yes" if p.approved else "no"
        print(f"  {p.name:<20} {p.priority:<8} {status_str:<26} {approved:<8} {deps:<16} {_last_event(p)}")

    print()

    # 统计
    counts: dict[str, int] = {}
    for p in config.projects:
        counts[p.status] = counts.get(p.status, 0) + 1

    parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    print(f"  总计: {len(config.projects)} 项目 | {' | '.join(parts)}")
    return 0


def cmd_sync(args) -> int:
    """同步 Ray 队列状态与各项目 .chase 状态。"""
    state = _state(args)
    if not state.queue_file.exists():
        print_yellow("Ray 未初始化。运行 chase ray init")
        return 1

    config = state.load_queue()
    sync_config(config)
    state.save_queue(config)
    print_green("Ray 状态已同步")
    return 0


def cmd_log(args) -> int:
    """显示某项目的 Ray 审计时间线。"""
    state = _state(args)
    config = state.load_queue()
    project = _find_project(config, args.name)
    if not project:
        print_red(f"项目 '{args.name}' 不存在")
        return 1

    events = _audit_events(project)
    if not events:
        print_yellow("No audit data recorded")
        return 0

    for name, timestamp in events:
        print(f"{name} - {timestamp}")
    return 0


def cmd_inspect(args) -> int:
    """查看项目 plan preview 和 verification cards。"""
    state = _state(args)
    config = state.load_queue()
    project = _find_project(config, args.name)
    if not project:
        print_red(f"项目 '{args.name}' 不存在")
        return 1

    workspace = Path(project.path).expanduser().resolve()
    sprint_filter = getattr(args, "sprint", None)
    if sprint_filter is not None:
        card = workspace / ".chase" / "sprints" / f"{int(sprint_filter):02d}-verification.md"
        if not card.exists():
            print_red(f"Sprint {sprint_filter} verification card not found")
            return 1
        print(_format_markdown(card.read_text(encoding="utf-8")))
        return 0

    plan = workspace / ".chase" / "plan-preview.md"
    if plan.exists():
        print(_format_markdown(plan.read_text(encoding="utf-8")))
    else:
        print_yellow("No plan preview found")

    sprints_dir = workspace / ".chase" / "sprints"
    cards = sorted(sprints_dir.glob("*-verification.md")) if sprints_dir.is_dir() else []
    for card in cards:
        sid = card.name.split("-")[0].lstrip("0") or "0"
        print(bold(f"--- Sprint {sid} ---"))
        print(_format_markdown(card.read_text(encoding="utf-8")))
    return 0


def cmd_pause(args) -> int:
    """暂停某项目。"""
    state = _state(args)
    config = state.load_queue()

    name = args.name
    project = _find_project(config, name)
    if not project:
        print_red(f"项目 '{name}' 不存在")
        return 1

    if project.status != STATUS_RUNNING:
        print_yellow(f"项目 '{name}' 当前状态为 {project.status}，无法暂停")
        return 1

    # 通过 PID 文件找到 Monitor 并暂停
    print_green(f"项目 '{name}' 已标记为暂停")
    project.status = STATUS_PAUSED
    state.save_queue(config)
    return 0


def cmd_resume(args) -> int:
    """恢复某项目。"""
    state = _state(args)
    config = state.load_queue()

    name = args.name
    project = _find_project(config, name)
    if not project:
        print_red(f"项目 '{name}' 不存在")
        return 1

    if project.status != STATUS_PAUSED:
        print_yellow(f"项目 '{name}' 当前状态为 {project.status}，无法恢复")
        return 1

    project.status = STATUS_PENDING
    state.save_queue(config)
    print_green(f"项目 '{name}' 已恢复为 pending")
    return 0


def cmd_priority(args) -> int:
    """调整项目优先级。"""
    state = _state(args)
    config = state.load_queue()

    name = args.name
    project = _find_project(config, name)
    if not project:
        print_red(f"项目 '{name}' 不存在")
        return 1

    old = project.priority
    project.priority = args.level
    state.save_queue(config)
    print_green(f"项目 '{name}' 优先级: {old} → {args.level}")
    return 0


def cmd_stop(args) -> int:
    """优雅停机。"""
    state = _state(args)
    pid = state.read_pid()
    if not pid:
        print_yellow("Ray 未在运行")
        return 1

    if not _pid_alive(pid):
        state.remove_pid()
        print_yellow(f"PID {pid} 已不存在，清理 PID 文件")
        return 0

    import signal as sig
    try:
        os.kill(pid, sig.SIGTERM)
        print_green(f"已发送 SIGTERM 给 Ray (pid={pid})")
    except OSError as e:
        print_red(f"发送信号失败: {e}")
        return 1
    return 0


def cmd_remove(args) -> int:
    """从队列移除项目（不删文件）。"""
    state = _state(args)
    config = state.load_queue()

    name = args.name
    original_len = len(config.projects)
    config.projects = [p for p in config.projects if p.name != name]

    if len(config.projects) == original_len:
        print_red(f"项目 '{name}' 不存在")
        return 1

    state.save_queue(config)
    print_green(f"已移除项目 '{name}'")
    return 0


def cmd_dashboard(args) -> int:
    """启动 Web Dashboard（前台模式）。"""
    state = _state(args)
    port = getattr(args, "port", 8765)
    from chase.ray.dashboard import start_dashboard

    print_green(f"Chase Ray Dashboard: http://localhost:{port}")
    print("按 Ctrl+C 停止")
    try:
        start_dashboard(state, port, background=False)
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


def cmd_launchd(args) -> int:
    """生成 launchd plist 模板。"""
    state = _state(args)
    plist = generate_launchd_template(state)
    dest = state.base / "com.chase.ray.plist"
    dest.write_text(plist, encoding="utf-8")
    print_green(f"已生成 {dest}")
    print()
    print("安装:")
    print(f"  cp {dest} ~/Library/LaunchAgents/")
    print(f"  launchctl load ~/Library/LaunchAgents/com.chase.ray.plist")
    return 0


# --- 辅助函数 ---


def _find_project(config, name: str) -> Project | None:
    for p in config.projects:
        if p.name == name:
            return p
    return None


def _pid_alive(pid: int) -> bool:
    """检查进程是否存活。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- 子命令注册 ---


def register_parser(sub) -> None:
    """在主 CLI 的 subparsers 上注册 ray 子命令。"""
    ray = sub.add_parser("ray", help="多项目常驻编排器")
    ray.add_argument("--cwd", default=None, help="Ray 工作目录（含 .chase-ray/）")
    ray_sub = ray.add_subparsers(dest="ray_command")

    # init
    ray_sub.add_parser("init", help="初始化 Ray 编排环境")

    # start
    p = ray_sub.add_parser("start", help="启动编排循环")
    p.add_argument("--daemon", action="store_true", help="守护进程模式")
    p.add_argument("--dashboard", action="store_true", help="同时启动 Web Dashboard")
    p.add_argument("--dashboard-port", type=int, default=8765, dest="dashboard_port",
                    help="Dashboard 端口（默认 8765）")

    # dispatch
    p = ray_sub.add_parser("dispatch", help="动态派发新项目")
    p.add_argument("name", help="项目名称")
    p.add_argument("path", help="项目路径")
    p.add_argument("--priority", type=int, default=0, help="优先级（数字越小越优先）")
    p.add_argument("--depends-on", default=None, help="依赖项目，逗号分隔")

    # status
    ray_sub.add_parser("status", help="查看所有项目状态汇总")

    # sync
    ray_sub.add_parser("sync", help="同步队列与各项目 .chase 状态")

    # log
    p = ray_sub.add_parser("log", help="显示项目审计时间线")
    p.add_argument("name", help="项目名称")

    # inspect
    p = ray_sub.add_parser("inspect", help="查看项目计划和验收证据")
    p.add_argument("name", help="项目名称")
    p.add_argument("--sprint", type=int, default=None, help="只查看指定 sprint 的验证卡")

    # approve
    p = ray_sub.add_parser("approve", help="审批某项目，允许执行 chase run")
    p.add_argument("name", nargs="?", help="项目名称")
    p.add_argument("--all-low-risk", action="store_true", help="自动审批所有低风险 waiting_approval 项目")

    # pause
    p = ray_sub.add_parser("pause", help="暂停某项目")
    p.add_argument("name", help="项目名称")

    # resume
    p = ray_sub.add_parser("resume", help="恢复某项目")
    p.add_argument("name", help="项目名称")

    # priority
    p = ray_sub.add_parser("priority", help="调整项目优先级")
    p.add_argument("name", help="项目名称")
    p.add_argument("level", type=int, help="新优先级")

    # stop
    ray_sub.add_parser("stop", help="优雅停机")

    # remove
    p = ray_sub.add_parser("remove", help="移除项目")
    p.add_argument("name", help="项目名称")

    # launchd
    ray_sub.add_parser("launchd", help="生成 macOS launchd plist 模板")

    # dashboard
    p = ray_sub.add_parser("dashboard", help="启动 Web Dashboard（前台）")
    p.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")


# 命令分发表
_DISPATCH = {
    "init": cmd_init,
    "start": cmd_start,
    "dispatch": cmd_dispatch,
    "approve": cmd_approve,
    "status": cmd_status,
    "sync": cmd_sync,
    "log": cmd_log,
    "inspect": cmd_inspect,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "priority": cmd_priority,
    "stop": cmd_stop,
    "remove": cmd_remove,
    "launchd": cmd_launchd,
    "dashboard": cmd_dashboard,
}


def handle_ray(args) -> int:
    """ray 子命令入口。"""
    ray_cmd = getattr(args, "ray_command", None)
    if not ray_cmd:
        from chase.cli import main as cli_main

        # 重新打印 ray 帮助
        print("用法: chase ray <command> [options]")
        print()
        print("子命令:")
        print("  init              初始化 Ray 编排环境")
        print("  start [--daemon]  启动编排循环")
        print("  dispatch <path>   动态派发新项目")
        print("  sync              同步队列与各项目 .chase 状态")
        print("  approve <name>    审批项目，允许执行 chase run")
        print("  inspect <name>    查看项目计划和验收证据")
        print("  status            查看项目状态汇总")
        print("  pause <name>      暂停某项目")
        print("  log <name>        显示项目审计时间线")
        print("  resume <name>     恢复某项目")
        print("  priority <name> <N>  调整优先级")
        print("  stop              优雅停机")
        print("  remove <name>     移除项目")
        print("  launchd           生成 launchd plist")
        print("  dashboard [--port N]  启动 Web Dashboard")
        return 1

    handler = _DISPATCH.get(ray_cmd)
    if handler is None:
        print_red(f"未知 ray 子命令: {ray_cmd}")
        return 1

    return handler(args)


_AUDIT_FIELDS = (
    ("planned", "planned_at"),
    ("approved", "approved_at"),
    ("run", "run_at"),
    ("completed", "completed_at"),
    ("needs_review", "needs_review_at"),
)


def _audit_events(project: Project) -> list[tuple[str, str]]:
    events = []
    for name, field in _AUDIT_FIELDS:
        value = getattr(project, field, None)
        if value:
            events.append((name, value))
    events.sort(key=lambda item: item[1])
    return events


def _last_event(project: Project) -> str:
    events = _audit_events(project)
    if not events:
        return "-"
    name, timestamp = events[-1]
    return f"{name} {timestamp}"


def _format_markdown(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            lines.append(bold(line))
        elif line.startswith("Verdict:"):
            lines.append(_format_verdict(line))
        else:
            lines.append(line)
    return "\n".join(lines)


def _format_verdict(line: str) -> str:
    for verdict, color in (("PASS", green), ("FAIL", red), ("ERROR", red)):
        if verdict in line:
            return line.replace(verdict, color(verdict), 1)
    return line
