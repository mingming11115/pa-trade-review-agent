from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace

import anyio
import pytest

from app.analysis.execution.manager import AnalysisExecutionManager
from app.core.logging_context import TraceContextFilter, get_trace_fields, get_trace_id, set_trace_context


class FailingRunRepository:
    def __init__(self, execution_id: str) -> None:
        self.execution = SimpleNamespace(
            analysis_id=execution_id,
            task_id=uuid.uuid4(),
            user_id=None,
            input_json={},
        )
        self.observed: list[tuple[str, dict[str, str]]] = []

    async def get_run_unscoped(self, _execution_id: str):
        return self.execution

    async def mark_run_running(self, _execution_id: str) -> None:
        self.observed.append(("running", get_trace_fields()))

    async def finish_run(self, _user_id, _execution_id: str, **_kwargs) -> None:
        self.observed.append(("failed", get_trace_fields()))


class ReviewRepository:
    def __init__(self, parent_id: str, child_id: str) -> None:
        self.parent_id = parent_id
        self.child = SimpleNamespace(analysis_id=child_id, input_json={"query": {}, "bars": []})

    async def successful_review_results(self, *_args): return {}
    async def review_retry_work_keys(self, *_args): return None
    async def create_review_children(self, *_args): return [self.child]
    async def update_review_child(self, *_args, **_kwargs): return None
    async def update_run_result(self, *_args, **_kwargs): return None
    async def finish_run(self, *_args, **_kwargs): return None


def test_manager_start_forwards_explicit_trace_id(monkeypatch) -> None:
    execution_id = str(uuid.uuid4())
    manager = AnalysisExecutionManager(FailingRunRepository(execution_id))
    captured: list[tuple[str, str]] = []

    async def fake_run(received_execution_id: str, trace_id: str) -> None:
        captured.append((received_execution_id, trace_id))

    monkeypatch.setattr(manager, "run", fake_run)

    async def exercise() -> None:
        manager.start(execution_id, "run-trace-1")
        await manager.tasks[uuid.UUID(execution_id)]
        await asyncio.sleep(0)

    anyio.run(exercise)

    assert captured == [(execution_id, "run-trace-1")]


def test_failed_run_binds_trace_and_business_ids_then_restores_context(
    monkeypatch,
    caplog,
) -> None:
    execution_id = str(uuid.uuid4())
    repository = FailingRunRepository(execution_id)
    manager = AnalysisExecutionManager(repository)

    async def fake_append_event(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("app.analysis.execution.manager.events.append_event", fake_append_event)
    caplog.handler.addFilter(TraceContextFilter())
    caplog.set_level(logging.INFO, logger="app.analysis.execution.manager")

    anyio.run(manager.run, execution_id, "run-trace-2")

    assert [name for name, _ in repository.observed] == ["running", "failed"]
    for _, fields in repository.observed:
        assert fields["trace_id"] == "run-trace-2"
        assert fields["task_id"] == str(repository.execution.task_id)
        assert fields["analysis_id"] == execution_id
        assert fields["execution_id"] == execution_id
    assert any(
        record.getMessage().startswith("analysis_run_failed")
        and record.trace_id == "run-trace-2"
        for record in caplog.records
    )
    assert get_trace_id() == "-"


def test_review_child_temporarily_binds_its_analysis_id(monkeypatch) -> None:
    parent_id = "parent-analysis"
    child_id = "child-analysis"
    repository = ReviewRepository(parent_id, child_id)
    manager = AnalysisExecutionManager(repository)
    observed: list[str] = []

    async def fake_workflow(*_args, **_kwargs):
        observed.append(get_trace_fields()["analysis_id"])
        return SimpleNamespace(model_dump=lambda **_kwargs: {"review_result": []})

    async def fake_append_event(*_args, **_kwargs): return None
    monkeypatch.setattr("app.analysis.execution.manager.HistoricalQuery.model_validate", lambda _value: SimpleNamespace())
    monkeypatch.setattr("app.analysis.execution.manager.run_demo_analysis_workflow", fake_workflow)
    monkeypatch.setattr("app.analysis.execution.manager.events.append_event", fake_append_event)
    execution = SimpleNamespace(user_id=None, task_id=uuid.uuid4(), sequence=1, analysis_id=parent_id)

    async def exercise() -> None:
        tokens = set_trace_context("review-trace", analysis_id=parent_id)
        try:
            await manager._run_review(execution, [{"key": "child"}])
            observed.append(get_trace_fields()["analysis_id"])
        finally:
            from app.core.logging_context import reset_trace_context
            reset_trace_context(tokens)

    anyio.run(exercise)

    assert observed == [child_id, parent_id]


def test_cancelled_run_keeps_trace_context_until_terminal_updates(monkeypatch) -> None:
    execution_id = str(uuid.uuid4())
    repository = FailingRunRepository(execution_id)
    manager = AnalysisExecutionManager(repository)

    async def cancel_during_start(_execution_id: str) -> None:
        raise asyncio.CancelledError

    async def fake_append_event(*_args, **_kwargs) -> None:
        repository.observed.append(("event", get_trace_fields()))

    monkeypatch.setattr(repository, "mark_run_running", cancel_during_start)
    monkeypatch.setattr("app.analysis.execution.manager.events.append_event", fake_append_event)

    with pytest.raises(asyncio.CancelledError):
        anyio.run(manager.run, execution_id, "cancel-trace")

    assert [name for name, _ in repository.observed] == ["failed", "event"]
    assert all(fields["trace_id"] == "cancel-trace" for _, fields in repository.observed)
    assert get_trace_id() == "-"
