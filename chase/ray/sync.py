"""Synchronize Ray queue state from per-project .chase state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from chase.ray.config import (
    STATUS_COMPLETED,
    STATUS_NEEDS_REVIEW,
    STATUS_PENDING,
    STATUS_PLANNING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    Project,
    RayConfig,
)


def sync_config(config: RayConfig) -> None:
    """Update projects in-place from their workspace .chase state."""
    for project in config.projects:
        sync_project(project)


def sync_project(project: Project) -> None:
    """Sync one Ray project from its Chase workspace state."""
    if project.status == STATUS_RUNNING:
        _stamp(project, "run_at")
        return
    if project.status == STATUS_PLANNING:
        return

    workspace = Path(project.path).expanduser()
    chase_dir = workspace / ".chase"
    sprints_dir = chase_dir / "sprints"

    approved = _read_approved(chase_dir / "approved.json")
    if approved:
        was_approved = project.approved
        project.approved = True
        if not was_approved:
            _stamp(project, "approved_at")

    contracts = sorted(sprints_dir.glob("*-contract.md")) if sprints_dir.is_dir() else []
    evals = sorted(sprints_dir.glob("*-eval.json")) if sprints_dir.is_dir() else []
    if contracts and len(evals) >= len(contracts):
        verdicts = [_read_verdict(path) for path in evals]
        if verdicts and all(verdict == "PASS" for verdict in verdicts):
            project.status = STATUS_COMPLETED
            if not project.approved:
                _stamp(project, "approved_at")
            project.approved = True
            _stamp(project, "completed_at")
            return
        if any(verdict in {"FAIL", "ERROR"} for verdict in verdicts):
            project.status = STATUS_NEEDS_REVIEW
            if not project.approved:
                _stamp(project, "approved_at")
            project.approved = True
            _stamp(project, "needs_review_at")
            return

    has_plan = (chase_dir / "plan-preview.md").exists()
    if has_plan and not project.approved:
        project.status = STATUS_WAITING_APPROVAL
        _stamp(project, "planned_at")
        return

    if project.status == STATUS_WAITING_APPROVAL and project.approved:
        project.status = STATUS_PENDING


def _read_approved(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("approved"))
    except (json.JSONDecodeError, OSError):
        return False


def _read_verdict(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("verdict", "")).upper()
    except (json.JSONDecodeError, OSError):
        return ""


def _stamp(project: Project, field: str) -> None:
    if getattr(project, field) is None:
        setattr(project, field, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
