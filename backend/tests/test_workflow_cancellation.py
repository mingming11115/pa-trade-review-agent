from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.analysis.execution.manager import AnalysisRunManager, run_streamed_analysis
from app.analysis.workflow.graph import DemoAnalysisWorkflow


@pytest.mark.anyio
@pytest.mark.parametrize("close_generator", [False, True])
async def test_stream_cancellation_stops_inner_analysis(monkeypatch, close_generator: bool) -> None:
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def blocked(_self, _query):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(DemoAnalysisWorkflow, "_ainvoke_with_trail", blocked)
    workflow = DemoAnalysisWorkflow(provider=None)
    stream = workflow.stream(SimpleNamespace())
    first = await anext(stream)
    assert first["type"] == "status"
    await entered.wait()
    operation = asyncio.create_task(stream.aclose() if close_generator else anext(stream))
    if not close_generator:
        await asyncio.sleep(0)
        operation.cancel()
    try:
        await asyncio.wait_for(stopped.wait(), 0.5)
        result = await asyncio.gather(operation, return_exceptions=True)
        assert close_generator or isinstance(result[0], asyncio.CancelledError)
        assert workflow._stream_queue is None
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        await stream.aclose()


@pytest.mark.anyio
async def test_cancel_monitor_failure_stops_work_instead_of_silently_disabling_cancellation() -> None:
    stopped = asyncio.Event()

    async def failing_read(_run_id):
        raise RuntimeError("monitor database unavailable")

    async def blocked():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    manager = AnalysisRunManager(SimpleNamespace(get_run_unscoped=failing_read))
    with pytest.raises(RuntimeError, match="monitor database unavailable"):
        await asyncio.wait_for(manager._execute_workflow(uuid.uuid4(), blocked()), 1)
    assert stopped.is_set()


@pytest.mark.anyio
async def test_cancellation_during_delta_callback_awaits_inner_stream_cleanup(monkeypatch) -> None:
    callback_entered, stopped = asyncio.Event(), asyncio.Event()

    async def blocked(workflow, _query):
        try:
            await workflow._emit_stream_event({"type": "llm_delta", "text": "first"})
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def callback(_event):
        callback_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(DemoAnalysisWorkflow, "_ainvoke_with_trail", blocked)
    operation = asyncio.create_task(run_streamed_analysis([], SimpleNamespace(), callback, uuid.uuid4()))
    await asyncio.wait_for(callback_entered.wait(), 1)
    operation.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert stopped.is_set(), "manager returned before the inner analysis was stopped"
    finally:
        await asyncio.sleep(0)
