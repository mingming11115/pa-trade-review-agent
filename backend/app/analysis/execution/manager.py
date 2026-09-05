from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from time import perf_counter
from typing import Any, Callable

from app.analysis.history.snapshots import get_frozen_snapshot
from app.analysis.execution.runs import clear_stage_runs, get_stage_runs
from app.analysis.tasks.models import RunStatus
from app.analysis.tasks.repository import AnalysisTaskRepository
from app.analysis.workflow.graph import run_demo_analysis_workflow, stream_demo_analysis_workflow
from app.core.models import Bar, HistoricalQuery
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
) -> Any:
    result = None
    async for event in stream_demo_analysis_workflow(FrozenBarsProvider(bars), query, run_id=run_id):
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
        task = asyncio.create_task(
            self.run(run_id, trace_id),
            name=f"analysis-{run_id}",
        )
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

    async def run(self, run_id: uuid.UUID, trace_id: str) -> None:
        run = await self.repository.get_run_unscoped(run_id)
        tokens = set_trace_context(
            trace_id,
            task_id=str(run.task_id) if run.task_id else None,
            run_id=str(run.id),
        )
        started_at = perf_counter()
        logger.info("analysis_run_started")
        try:
            await self.repository.mark_run_running(run_id)
            if run.query_json.get("kind") == "review":
                await self._run_review_period(run, run.query_json["items"])
                logger.info(
                    "analysis_run_completed duration_ms=%d",
                    round((perf_counter() - started_at) * 1000),
                )
                return
            snapshot_id = run.query_json.get("snapshot_id")
            snapshot = get_frozen_snapshot(uuid.UUID(str(snapshot_id))) if snapshot_id else None
            if snapshot is None:
                raise RuntimeError("frozen snapshot not found")
            query = HistoricalQuery.model_validate(snapshot.query_json)
            bars = [Bar.model_validate(item) for item in snapshot.bars_json]

            async def forward_delta(event: dict[str, Any]) -> None:
                await self.repository.upsert_stage_attempt(
                    run.id,
                    stage=str(event.get("stage") or "stage1"),
                    attempt=1,
                    status="response_received",
                    raw_content=str(event.get("text") or ""),
                    prompt_metadata={"kind": event.get("kind")},
                )

            clear_stage_runs(str(run.id))
            streamed_result = await run_streamed_analysis(bars, query, forward_delta, run.id)
            await persist_stage_attempts(self.repository, run.id, get_stage_runs(str(run.id)))
            clear_stage_runs(str(run.id))
            from app.core.models import DemoAnalysisResponse

            result = DemoAnalysisResponse.model_validate(streamed_result)
            payload = result.model_dump(mode="json")
            await self.repository.update_run_result(run.id, payload)
            await self.repository.finish_run(run.user_id, run.id, status=RunStatus.completed)
            logger.info(
                "analysis_run_completed duration_ms=%d",
                round((perf_counter() - started_at) * 1000),
            )
        except asyncio.CancelledError:
            await self.repository.finish_run(run.user_id, run.id, status=RunStatus.cancelled)
            logger.warning(
                "analysis_run_cancelled duration_ms=%d",
                round((perf_counter() - started_at) * 1000),
            )
            raise
        except Exception as exc:
            await self.repository.finish_run(run.user_id, run.id, status=RunStatus.failed, failure_code="analysis_failed", failure_message=type(exc).__name__)
            logger.exception(
                "analysis_run_failed duration_ms=%d error_type=%s",
                round((perf_counter() - started_at) * 1000),
                type(exc).__name__,
            )
        finally:
            clear_stage_runs(str(run.id))
            reset_trace_context(tokens)

    async def _run_review_period(self, run, inputs: list[dict[str, Any]]) -> None:
        async def analyze(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
            try:
                query = HistoricalQuery.model_validate(item["query"])
                bars = [Bar.model_validate(bar) for bar in item["bars"]]
                result = await run_demo_analysis_workflow(FrozenBarsProvider(bars), query, run_id=run.id)
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

    async def cancel(self, run_id: uuid.UUID) -> None:
        task = self.tasks.get(run_id)
        if task is not None:
            task.cancel()


DEFAULT_RUN_MANAGER = AnalysisRunManager(AnalysisTaskRepository())
