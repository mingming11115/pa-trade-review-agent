from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from contextlib import aclosing
from time import perf_counter
from typing import Any, Awaitable, Callable

from app.analysis.history.snapshots import get_frozen_snapshot
from app.analysis.execution.runs import clear_stage_runs, get_stage_runs
from app.analysis.tasks.models import RunStatus
from app.analysis.tasks.repository import AnalysisTaskRepository
from app.analysis.workflow.graph import run_demo_analysis_workflow, stream_demo_analysis_workflow
from app.knowledge.contracts import PromptSnapshot
from app.knowledge.service import get_or_create_run_prompt_snapshot
from app.core.errors import AppError
from app.core.models import Bar, HistoricalQuery
from app.core.user_context import reset_current_user_id, set_current_user_id
from app.core.logging_context import reset_trace_context, set_trace_context


logger = logging.getLogger(__name__)


async def persist_stage_attempts(
    repository: AnalysisTaskRepository,
    run_id: uuid.UUID,
    stage_runs: list[dict[str, Any]],
    offsets: dict[str, int] | None = None,
) -> dict[str, int]:
    """把工作流内存审计写入当前持久化 Run，并为复盘多交易顺序编号。"""
    next_offsets = dict(offsets or {})
    allowed = {
        "status", "provider", "model", "provider_request_id", "response_model",
        "duration_ms", "prompt_tokens", "completion_tokens", "total_tokens",
        "raw_content", "reasoning_content", "raw_response", "normalized_output",
        "validation_errors", "provider_error", "prompt_metadata",
    }
    for record in sorted(stage_runs, key=lambda item: (str(item.get("stage") or ""), int(item.get("attempt") or 0))):
        stage = str(record.get("stage") or "")
        if not stage:
            continue
        attempt = next_offsets.get(stage, 0) + 1
        next_offsets[stage] = attempt
        await repository.upsert_stage_attempt(
            run_id,
            stage=stage,
            attempt=attempt,
            **{key: record[key] for key in allowed if key in record},
        )
    return next_offsets


class FrozenBarsProvider:
    def __init__(self, bars: list[Bar]):
        self.bars = bars

    async def get_range(self, _query: HistoricalQuery) -> list[Bar]:
        return list(self.bars)


async def run_streamed_analysis(
    bars: list[Bar],
    query: HistoricalQuery,
    on_delta: Callable[[dict[str, Any]], Any],
    run_id: uuid.UUID,
    *,
    prompt_snapshot: PromptSnapshot | None = None,
) -> Any:
    result = None
    options = {"prompt_snapshot": prompt_snapshot} if prompt_snapshot is not None else {}
    async with aclosing(stream_demo_analysis_workflow(FrozenBarsProvider(bars), query, run_id=run_id, **options)) as events:
        async for event in events:
            if event.get("type") == "llm_delta":
                pending = on_delta(event)
                if inspect.isawaitable(pending):
                    await pending
            elif event.get("type") == "result":
                result = event.get("result")
    if result is None:
        raise RuntimeError("streamed analysis completed without a result")
    return result


class AnalysisRunManager:
    def __init__(self, repository: AnalysisTaskRepository):
        self.repository = repository
        self.tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    def start(self, run_id: uuid.UUID, trace_id: str) -> None:
        if run_id in self.tasks:
            return
        task = asyncio.create_task(
            self.run(run_id, trace_id),
            name=f"analysis-{run_id}",
        )
        self.tasks[run_id] = task
        def finished(completed: asyncio.Task[None]) -> None:
            if self.tasks.get(run_id) is completed:
                self.tasks.pop(run_id, None)
            if not completed.cancelled() and completed.exception() is not None:
                logger.error("analysis_run_background_failed", exc_info=completed.exception())
        task.add_done_callback(finished)

    async def run(self, run_id: uuid.UUID, trace_id: str) -> None:
        run = None
        user_token = None
        tokens = set_trace_context(trace_id, run_id=str(run_id))
        started_at = perf_counter()
        try:
            run = await self.repository.get_run_unscoped(run_id)
            user_token = set_current_user_id(run.user_id)
            reset_trace_context(tokens)
            tokens = set_trace_context(trace_id, task_id=str(run.task_id), run_id=str(run_id))
            logger.info("analysis_run_started")
            claimed = await self.repository.mark_run_running(run_id)
            if claimed is False:
                current = await self.repository.get_run_unscoped(run_id)
                if current.status == RunStatus.cancel_requested.value:
                    await self.repository.finish_run(current.user_id, run_id, status=RunStatus.cancelled)
                return
            prompt_snapshot = await self._execute_workflow(
                run.id, get_or_create_run_prompt_snapshot(run.id, run.user_id)
            )
            if run.query_json.get("kind") == "review":
                await self._run_review_period(run, run.query_json["items"], prompt_snapshot=prompt_snapshot)
                finished = await self.repository.get_run_unscoped(run_id)
                logger.info(
                    "analysis_run_finished status=%s duration_ms=%d",
                    finished.status,
                    round((perf_counter() - started_at) * 1000),
                )
                return
            snapshot_id = run.query_json.get("snapshot_id")
            snapshot = get_frozen_snapshot(uuid.UUID(str(snapshot_id))) if snapshot_id else None
            if run.query_json.get("kind") == "replay_review":
                query = HistoricalQuery.model_validate(run.query_json["query"])
                bars = [Bar.model_validate(item) for item in run.bars_json or []]
            else:
                if snapshot is None:
                    raise RuntimeError("frozen snapshot not found")
                query = HistoricalQuery.model_validate(snapshot.query_json)
                bars = [Bar.model_validate(item) for item in snapshot.bars_json]

            async def forward_delta(event: dict[str, Any]) -> None:
                await self._persist_delta(self.repository.upsert_stage_attempt(
                    run.id,
                    stage=str(event.get("stage") or "stage1"),
                    attempt=1,
                    status="response_received",
                    raw_content=str(event.get("text") or ""),
                    prompt_metadata={"kind": event.get("kind")},
                ))

            clear_stage_runs(str(run.id))
            streamed_result = await self._execute_workflow(run.id, run_streamed_analysis(bars, query, forward_delta, run.id, prompt_snapshot=prompt_snapshot))
            await persist_stage_attempts(self.repository, run.id, get_stage_runs(str(run.id)))
            clear_stage_runs(str(run.id))
            from app.core.models import DemoAnalysisResponse

            result = DemoAnalysisResponse.model_validate(streamed_result)
            payload = result.model_dump(mode="json")
            await self.repository.update_run_result(run.id, payload)
            finished = await self.repository.finish_run(run.user_id, run.id, status=RunStatus.completed)
            logger.info(
                "analysis_run_finished status=%s duration_ms=%d",
                finished.status,
                round((perf_counter() - started_at) * 1000),
            )
        except asyncio.CancelledError:
            if run is None:
                run = await self.repository.get_run_unscoped(run_id)
            await self.repository.finish_run(run.user_id, run.id, status=RunStatus.cancelled)
            logger.warning(
                "analysis_run_cancelled duration_ms=%d",
                round((perf_counter() - started_at) * 1000),
            )
            raise
        except Exception as exc:
            if run is None:
                run = await self.repository.get_run_unscoped(run_id)
            await self.repository.finish_run(
                run.user_id, run.id, status=RunStatus.failed,
                failure_code=exc.code if isinstance(exc, AppError) else "analysis_failed",
                failure_message=exc.message if isinstance(exc, AppError) else type(exc).__name__,
            )
            logger.exception(
                "analysis_run_failed duration_ms=%d error_type=%s",
                round((perf_counter() - started_at) * 1000),
                type(exc).__name__,
            )
        finally:
            clear_stage_runs(str(run_id))
            if user_token is not None:
                reset_current_user_id(user_token)
            reset_trace_context(tokens)

    async def _run_review_period(self, run, inputs: list[dict[str, Any]], *, prompt_snapshot: PromptSnapshot | None = None) -> None:
        async def analyze(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            try:
                query = HistoricalQuery.model_validate(item["query"])
                bars = [Bar.model_validate(bar) for bar in item["bars"]]
                options = {"prompt_snapshot": prompt_snapshot} if prompt_snapshot is not None else {}
                result = await self._execute_workflow(run.id, run_demo_analysis_workflow(FrozenBarsProvider(bars), query, run_id=run.id, **options))
                return RunStatus.completed.value, result.model_dump(mode="json")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("review_item_failed")
                return RunStatus.failed.value, None

        outcomes: list[tuple[str, dict[str, Any] | None]] = []
        attempt_offsets: dict[str, int] = {}
        for item in inputs:
            clear_stage_runs(str(run.id))
            outcomes.append(await analyze(item))
            attempt_offsets = await persist_stage_attempts(
                self.repository,
                run.id,
                get_stage_runs(str(run.id)),
                attempt_offsets,
            )
        clear_stage_runs(str(run.id))
        successes = [result for _, result in outcomes if result is not None]
        if successes and len(successes) == len(outcomes):
            status = RunStatus.completed
        elif successes:
            status = RunStatus.completed_with_warnings
        else:
            status = RunStatus.failed
        payload = {
            "run_id": str(run.id),
            "query": {"analysis_mode": "trade_review", "symbol": "MULTI", "period": run.period},
            "review_result": [item for result in successes for item in result.get("review_result", [])],
            "status": status.value,
        }
        await self.repository.update_run_result(run.id, payload)
        await self.repository.finish_run(run.user_id, run.id, status=status)

    async def _persist_delta(self, operation: Awaitable[Any]) -> Any:
        # Let an in-flight database transaction settle before cancellation handling.
        pending = asyncio.ensure_future(operation)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await pending
            raise

    async def _execute_workflow(self, run_id: uuid.UUID, operation: Awaitable[Any]) -> Any:
        worker = asyncio.ensure_future(operation)
        monitor = asyncio.create_task(self._monitor_cancel(run_id, worker))
        try:
            done, _ = await asyncio.wait({worker, monitor}, return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                # Losing the cancellation observer is an execution failure, not
                # permission to keep consuming model resources unmonitored.
                await monitor
            return await worker
        finally:
            monitor.cancel()
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, monitor, return_exceptions=True)

    async def _monitor_cancel(self, run_id: uuid.UUID, worker: asyncio.Task[Any]) -> None:
        while True:
            await asyncio.sleep(0.25)
            run = await self.repository.get_run_unscoped(run_id)
            if run.status == RunStatus.cancel_requested.value:
                if not worker.cancelling():
                    worker.cancel()
                return
            if run.status != RunStatus.running.value:
                return

    async def cancel(self, run_id: uuid.UUID) -> None:
        run = await self.repository.get_run_unscoped(run_id)
        if run.status != RunStatus.cancel_requested.value:
            return
        task = self.tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
            # A Task cancelled before its coroutine starts never enters its handler.
            await self.repository.finish_run(run.user_id, run_id, status=RunStatus.cancelled)


DEFAULT_RUN_MANAGER = AnalysisRunManager(AnalysisTaskRepository())
