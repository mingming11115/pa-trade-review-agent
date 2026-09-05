from __future__ import annotations

from app.analysis.tasks.models import RunStatus, TaskStatus
from app.core.errors import AppError


_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.pending: {TaskStatus.running},
    TaskStatus.running: {
        TaskStatus.completed,
        TaskStatus.completed_with_warnings,
        TaskStatus.failed,
        TaskStatus.cancelled,
        TaskStatus.timed_out,
    },
    TaskStatus.failed: set(),
    TaskStatus.cancelled: set(),
    TaskStatus.completed: set(),
    TaskStatus.completed_with_warnings: set(),
    TaskStatus.timed_out: set(),
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


def transition_task(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if target in _TASK_TRANSITIONS[current]:
        return target
    code = "analysis_task_already_executed" if current is not TaskStatus.pending else "invalid_task_transition"
    raise AppError(code, f"分析任务不能从 {current.value} 变为 {target.value}", 409)


def transition_run(current: RunStatus, target: RunStatus) -> RunStatus:
    if target in _RUN_TRANSITIONS[current]:
        return target
    terminal = not _RUN_TRANSITIONS[current]
    code = "analysis_run_terminal" if terminal else "invalid_run_transition"
    raise AppError(code, f"分析运行不能从 {current.value} 变为 {target.value}", 409)
