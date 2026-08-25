from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.errors import AppError


def test_completed_task_cannot_run_again() -> None:
    from app.analysis.tasks.lifecycle import transition_task
    from app.analysis.tasks.models import TaskStatus

    with pytest.raises(AppError) as caught:
        transition_task(TaskStatus.completed, TaskStatus.running)

    assert caught.value.code == "analysis_task_completed"


def test_completed_analysis_task_can_run_again() -> None:
    from app.analysis.tasks.lifecycle import transition_task
    from app.analysis.tasks.models import TaskStatus

    assert transition_task(TaskStatus.completed, TaskStatus.running, repeatable=True) is TaskStatus.running


def test_completed_review_task_remains_terminal() -> None:
    from app.analysis.tasks.lifecycle import transition_task
    from app.analysis.tasks.models import TaskStatus

    with pytest.raises(AppError):
        transition_task(TaskStatus.completed, TaskStatus.running, repeatable=False)


def test_failed_and_cancelled_tasks_can_retry() -> None:
    from app.analysis.tasks.lifecycle import transition_task
    from app.analysis.tasks.models import TaskStatus

    assert transition_task(TaskStatus.failed, TaskStatus.running) is TaskStatus.running
    assert transition_task(TaskStatus.cancelled, TaskStatus.running) is TaskStatus.running


def test_review_task_requires_selected_trades() -> None:
    from app.analysis.tasks.models import AnalysisTaskCreate

    with pytest.raises(ValidationError):
        AnalysisTaskCreate(
            kind="review",
            title="复盘",
            config={
                "selected_trade_ids": [],
                "periods": ["5m"],
            },
        )


def test_analysis_task_only_accepts_symbol_and_period() -> None:
    from app.analysis.tasks.models import AnalysisTaskCreate

    task = AnalysisTaskCreate(kind="analysis", title="最新行情", config={"symbol": "ES", "period": "5m"})
    assert task.config.model_dump(mode="json") == {"symbol": "ES", "period": "5m"}

    with pytest.raises(ValidationError):
        AnalysisTaskCreate(
            kind="analysis",
            title="最新行情",
            config={
                "symbol": "ES",
                "period": "5m",
                "analysis_mode": "historical",
                "start": "2026-08-12T01:00:00Z",
                "end": "2026-08-12T02:00:00Z",
            },
        )


def test_run_terminal_states_are_immutable() -> None:
    from app.analysis.tasks.lifecycle import transition_run
    from app.analysis.tasks.models import RunStatus

    for current in (
        RunStatus.completed,
        RunStatus.completed_with_warnings,
        RunStatus.degraded,
        RunStatus.failed,
        RunStatus.cancelled,
        RunStatus.timed_out,
    ):
        with pytest.raises(AppError) as caught:
            transition_run(current, RunStatus.running)
        assert caught.value.code == "analysis_run_terminal"


def test_run_cancel_transition_is_explicit() -> None:
    from app.analysis.tasks.lifecycle import transition_run
    from app.analysis.tasks.models import RunStatus

    assert transition_run(
        RunStatus.running,
        RunStatus.cancel_requested,
    ) is RunStatus.cancel_requested
    assert transition_run(
        RunStatus.cancel_requested,
        RunStatus.cancelled,
    ) is RunStatus.cancelled

    with pytest.raises(AppError):
        transition_run(RunStatus.queued, RunStatus.completed)
