"""优先级排序 + 依赖拓扑 + 并发控制。"""

from __future__ import annotations

from chase.ray.config import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_PENDING,
    STATUS_PLANNING,
    STATUS_RUNNING,
    STATUS_WAITING_APPROVAL,
    Project,
    RayConfig,
)


class Scheduler:
    """根据优先级和依赖关系决定哪些项目可以启动。"""

    def __init__(self, config: RayConfig):
        self.config = config

    def _completed_names(self) -> set[str]:
        """已成功完成的项目名集合。"""
        return {
            p.name
            for p in self.config.projects
            if p.status == STATUS_COMPLETED
        }

    def _running_count(self) -> int:
        return sum(1 for p in self.config.projects if p.status in (STATUS_RUNNING, STATUS_PLANNING))

    def _deps_met(self, project: Project, completed: set[str]) -> bool:
        """检查项目依赖是否全部满足。"""
        if not project.depends_on:
            return True
        return all(dep in completed for dep in project.depends_on)

    def _has_cycle(self, project: Project, visiting: set[str], visited: set[str]) -> bool:
        """DFS 检测依赖环。"""
        if project.name in visiting:
            return True
        if project.name in visited:
            return False
        visiting.add(project.name)
        for dep_name in project.depends_on:
            dep = self._find_project(dep_name)
            if dep and self._has_cycle(dep, visiting, visited):
                return True
        visiting.discard(project.name)
        visited.add(project.name)
        return False

    def _find_project(self, name: str) -> Project | None:
        for p in self.config.projects:
            if p.name == name:
                return p
        return None

    def validate(self) -> list[str]:
        """校验队列，返回错误列表。空列表表示合法。"""
        errors: list[str] = []
        names = {p.name for p in self.config.projects}

        # 重名检查
        if len(names) < len(self.config.projects):
            errors.append("存在重复的项目名")

        for p in self.config.projects:
            # 依赖项是否存在
            for dep in p.depends_on:
                if dep not in names:
                    errors.append(f"项目 '{p.name}' 依赖 '{dep}' 不存在")
            # 环检测
            if self._has_cycle(p, set(), set()):
                errors.append(f"项目 '{p.name}' 存在依赖环")
                break

        return errors

    def dispatchable(self) -> list[Project]:
        """返回当前可启动的项目列表（按优先级排序，低数字=高优先级）。"""
        completed = self._completed_names()
        slots = self.config.max_parallel - self._running_count()
        if slots <= 0:
            return []

        candidates: list[Project] = []
        for p in self.config.projects:
            if p.status != STATUS_PENDING:
                continue
            if not self._deps_met(p, completed):
                continue
            candidates.append(p)

        # 按优先级排序（数字越小越优先）
        candidates.sort(key=lambda p: p.priority)
        return candidates[:slots]

    def update_blocked(self) -> None:
        """将依赖未满足的 pending 项目标记为 blocked。"""
        completed = self._completed_names()
        for p in self.config.projects:
            if p.status == STATUS_PENDING and not self._deps_met(p, completed):
                p.status = STATUS_BLOCKED
            elif p.status == STATUS_BLOCKED and self._deps_met(p, completed):
                p.status = STATUS_PENDING
