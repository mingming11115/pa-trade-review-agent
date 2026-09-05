from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace

import anyio
import pytest

from app.analysis.execution.manager import AnalysisRunManager, run_streamed_analysis
from app.core.logging_context import TraceContextFilter, get_trace_fields, get_trace_id, set_trace_context


class FailingRunRepository:
    def __init__(self, run_id: uuid.UUID) -> None:
        self.run = SimpleNamespace(
            id=run_id,
            task_id=uuid.uuid4(),
            user_id=None,
            query_json={},
        )
        self.observed: list[tuple[str, dict[str, str]]] = []

    async def get_run_unscoped(self, _run_id: uuid.UUID):
        return self.run

    async def mark_run_running(self, _run_id: uuid.UUID) -> None:
        self.observed.append(("running", get_trace_fields()))

    async def finish_run(self, _user_id, _run_id: uuid.UUID, **_kwargs) -> None:
        self.observed.append(("failed", get_trace_fields()))


def test_streamed_analysis_injects_persisted_run_id(monkeypatch) -> None:
    run_id = uuid.uuid4()
    observed: list[uuid.UUID | None] = []

    async def fake_stream(_provider, _query, *, run_id=None):
        observed.append(run_id)
        yield {"type": "result", "result": {"run_id": str(run_id)}}

    monkeypatch.setattr("app.analysis.execution.manager.stream_demo_analysis_workflow", fake_stream)

    result = anyio.run(run_streamed_analysis, [], SimpleNamespace(), lambda _event: None, run_id)

    assert observed == [run_id]
    assert result["run_id"] == str(run_id)


def test_manager_start_forwards_explicit_trace_id(monkeypatch) -> None:
    run_id = uuid.uuid4()
    manager = AnalysisRunManager(FailingRunRepository(run_id))
    captured: list[tuple[uuid.UUID, str]] = []

    async def fake_run(received_run_id: uuid.UUID, trace_id: str) -> None:
        captured.append((received_run_id, trace_id))

    monkeypatch.setattr(manager, "run", fake_run)

    async def exercise() -> None:
        manager.start(run_id, "run-trace-1")
        await manager.tasks[run_id]
        await asyncio.sleep(0)

    anyio.run(exercise)

    assert captured == [(run_id, "run-trace-1")]


def test_failed_run_binds_trace_and_business_ids_then_restores_context(
    monkeypatch,
    caplog,
) -> None:
    run_id = uuid.uuid4()
    repository = FailingRunRepository(run_id)
    manager = AnalysisRunManager(repository)

    caplog.handler.addFilter(TraceContextFilter())
    caplog.set_level(logging.INFO, logger="app.analysis.execution.manager")

    anyio.run(manager.run, run_id, "run-trace-2")

    assert [name for name, _ in repository.observed] == ["running", "failed"]
    for _, fields in repository.observed:
        assert fields["trace_id"] == "run-trace-2"
        assert fields["task_id"] == str(repository.run.task_id)
        assert fields["run_id"] == str(run_id)
    assert any(
        record.getMessage().startswith("analysis_run_failed")
        and record.trace_id == "run-trace-2"
        for record in caplog.records
    )
    assert get_trace_id() == "-"


def test_cancelled_run_keeps_trace_context_until_terminal_updates(monkeypatch) -> None:
    run_id = uuid.uuid4()
    repository = FailingRunRepository(run_id)
    manager = AnalysisRunManager(repository)

    async def cancel_during_start(_run_id: uuid.UUID) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(repository, "mark_run_running", cancel_during_start)

    with pytest.raises(asyncio.CancelledError):
        anyio.run(manager.run, run_id, "cancel-trace")

    assert [name for name, _ in repository.observed] == ["failed"]
    assert all(fields["trace_id"] == "cancel-trace" for _, fields in repository.observed)
    assert get_trace_id() == "-"
