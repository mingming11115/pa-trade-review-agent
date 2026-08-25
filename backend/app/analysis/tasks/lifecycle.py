from __future__ import annotations

from app.analysis.tasks.models import RunStatus, TaskStatus
from app.core.errors import AppError


_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.pending: {TaskStatus.running},
    TaskStatus.running: {TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled},
    TaskStatus.failed: {TaskStatus.running},
    TaskStatus.cancelled: {TaskStatus.running},
    TaskStatus.completed: set(),
}

_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.queued: {
        RunStatus.running,
        RunStatus.cancel_requested,
        RunStatus.cancelled,
        RunStatus.failed,
        RunStatus.timed_out,
    },
    RunStatus.running: {
        RunStatus.completed,
        RunStatus.completed_with_warnings,
        RunStatus.degraded,
        RunStatus.failed,
        RunStatus.cancel_requested,
        RunStatus.cancelled,
        RunStatus.timed_out,
    },
    RunStatus.cancel_requested: {
        RunStatus.cancelled,
        RunStatus.failed,
        RunStatus.timed_out,
    },
    RunStatus.completed: set(),
    RunStatus.completed_with_warnings: set(),
    RunStatus.degraded: set(),
    RunStatus.failed: set(),
    RunStatus.cancelled: set(),
    RunStatus.timed_out: set(),
}


def transition_task(current: TaskStatus, target: TaskStatus, *, repeatable: bool = False) -> TaskStatus:
    if repeatable and current is TaskStatus.completed and target is TaskStatus.running:
        return target
    if target in _TASK_TRANSITIONS[current]:
        return target
    code = "analysis_task_completed" if current is TaskStatus.completed else "invalid_task_transition"
    raise AppError(code, f"分析任务不能从 {current.value} 变为 {target.value}", 409)


def transition_run(current: RunStatus, target: RunStatus) -> RunStatus:
    if target in _RUN_TRANSITIONS[current]:
        return target
    terminal = not _RUN_TRANSITIONS[current]
    code = "analysis_run_terminal" if terminal else "invalid_run_transition"
    raise AppError(code, f"分析运行不能从 {current.value} 变为 {target.value}", 409)
