from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from time import perf_counter
from typing import Any, Callable

from app.analysis.execution import events
from app.analysis.history.snapshots import get_frozen_snapshot
from app.analysis.tasks.models import RunStatus
from app.analysis.tasks.repository import AnalysisTaskRepository
from app.analysis.workflow.graph import run_demo_analysis_workflow, stream_demo_analysis_workflow
from app.core.models import Bar, HistoricalQuery
from app.core.logging_context import bind_trace_fields, reset_trace_context, set_trace_context


logger = logging.getLogger(__name__)


def aggregate_review_status(statuses: list[str]) -> RunStatus:
    if statuses and all(status == RunStatus.completed.value for status in statuses):
        return RunStatus.completed
    if any(status == RunStatus.completed.value for status in statuses):
        return RunStatus.completed_with_warnings
    return RunStatus.failed


class FrozenBarsProvider:
    def __init__(self, bars: list[Bar]):
        self.bars = bars

    async def get_range(self, _query: HistoricalQuery) -> list[Bar]:
        return list(self.bars)


async def run_streamed_analysis(
    bars: list[Bar],
    query: HistoricalQuery,
    on_delta: Callable[[dict[str, Any]], Any],
) -> Any:
    result = None
    async for event in stream_demo_analysis_workflow(FrozenBarsProvider(bars), query):
        if event.get("type") == "llm_delta":
            pending = on_delta(event)
            if inspect.isawaitable(pending):
                await pending
        elif event.get("type") == "result":
            result = event.get("result")
    if result is None:
        raise RuntimeError("streamed analysis completed without a result")
    return result


class AnalysisExecutionManager:
    def __init__(self, repository: AnalysisTaskRepository):
        self.repository = repository
        self.tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    def start(self, execution_id: str, trace_id: str) -> None:
        task = asyncio.create_task(
            self.run(execution_id, trace_id),
            name=f"analysis-{execution_id}",
        )
        self.tasks[uuid.UUID(str(execution_id))] = task
        task.add_done_callback(lambda _: self.tasks.pop(uuid.UUID(str(execution_id)), None))

    async def run(self, execution_id: str, trace_id: str) -> None:
        execution = await self.repository.get_run_unscoped(execution_id)
        tokens = set_trace_context(
            trace_id,
            task_id=str(execution.task_id) if execution.task_id else None,
            analysis_id=str(execution.analysis_id),
            execution_id=str(execution_id),
        )
        started_at = perf_counter()
        logger.info("analysis_run_started")
        try:
            await self.repository.mark_run_running(execution_id)
            snapshot_id = execution.input_json.get("snapshot_id")
            snapshot = get_frozen_snapshot(uuid.UUID(str(snapshot_id))) if snapshot_id else None
            if snapshot is None:
                raise RuntimeError("frozen snapshot not found")
            if snapshot.query_json.get("kind") == "review":
                await self._run_review(execution, snapshot.query_json["children"])
                logger.info(
                    "analysis_run_completed duration_ms=%d",
                    round((perf_counter() - started_at) * 1000),
                )
                return
            query = HistoricalQuery.model_validate(snapshot.query_json)
            bars = [Bar.model_validate(item) for item in snapshot.bars_json]

            async def forward_delta(event: dict[str, Any]) -> None:
                await self.repository.upsert_stage_attempt(
                    execution.analysis_id,
                    stage=str(event.get("stage") or "stage1"),
                    attempt=1,
                    status="response_received",
                    raw_content=str(event.get("text") or ""),
                    prompt_metadata={"kind": event.get("kind")},
                )

            streamed_result = await run_streamed_analysis(bars, query, forward_delta)
            from app.core.models import DemoAnalysisResponse

            result = DemoAnalysisResponse.model_validate(streamed_result)
            payload = result.model_dump(mode="json")
            await self.repository.update_run_result(execution.analysis_id, payload)
            await self.repository.finish_run(execution.user_id, execution.analysis_id, status=RunStatus.completed)
            await events.append_event(execution_id, "result", "complete", "分析完成", {"result": payload}, terminal=True)
            logger.info(
                "analysis_run_completed duration_ms=%d",
                round((perf_counter() - started_at) * 1000),
            )
        except asyncio.CancelledError:
            await self.repository.finish_run(execution.user_id, execution.analysis_id, status=RunStatus.cancelled)
            await events.append_event(execution_id, "cancelled", "complete", "分析已取消", terminal=True)
            logger.warning(
                "analysis_run_cancelled duration_ms=%d",
                round((perf_counter() - started_at) * 1000),
            )
            raise
        except Exception as exc:
            await self.repository.finish_run(execution.user_id, execution.analysis_id, status=RunStatus.failed, failure_code="analysis_failed", failure_message=type(exc).__name__)
            await events.append_event(execution_id, "error", "complete", "分析失败", {"code": "analysis_failed"}, terminal=True)
            logger.exception(
                "analysis_run_failed duration_ms=%d error_type=%s",
                round((perf_counter() - started_at) * 1000),
                type(exc).__name__,
            )
        finally:
            reset_trace_context(tokens)

    async def _run_review(self, execution, inputs: list[dict[str, Any]]) -> None:
        prior_successes = await self.repository.successful_review_results(execution.user_id, execution.task_id, execution.sequence) if execution.sequence and execution.sequence > 1 else {}
        retry_keys = await self.repository.review_retry_work_keys(execution.user_id, execution.task_id, execution.sequence) if execution.sequence and execution.sequence > 1 else None
        if retry_keys is not None:
            inputs = [item for item in inputs if item["key"] in retry_keys]
        children = await self.repository.create_review_children(execution, inputs)
        semaphore = asyncio.Semaphore(3)
        completed = 0
        lock = asyncio.Lock()

        async def run_child(child) -> tuple[str, dict[str, Any] | None]:
            nonlocal completed
            async with semaphore:
                with bind_trace_fields(analysis_id=str(child.analysis_id)):
                    try:
                        await self.repository.update_review_child(child.analysis_id, status=RunStatus.running)
                        query = HistoricalQuery.model_validate(child.input_json["query"])
                        bars = [Bar.model_validate(item) for item in child.input_json["bars"]]
                        result = await run_demo_analysis_workflow(FrozenBarsProvider(bars), query)
                        payload = result.model_dump(mode="json")
                        await self.repository.update_review_child(child.analysis_id, status=RunStatus.completed, result=payload)
                        child_status, child_result = RunStatus.completed.value, payload
                    except asyncio.CancelledError:
                        await self.repository.update_review_child(child.analysis_id, status=RunStatus.cancelled)
                        raise
                    except Exception as exc:
                        await self.repository.update_review_child(child.analysis_id, status=RunStatus.failed, failure_code="review_child_failed", failure_message=type(exc).__name__)
                        child_status, child_result = RunStatus.failed.value, None
                async with lock:
                    completed += 1
                    await events.append_event(execution.analysis_id, "progress", "review", f"复盘进度 {completed}/{len(children)}", {"completed": completed, "total": len(children)})
                return child_status, child_result

        outcomes = await asyncio.gather(*(run_child(child) for child in children))
        statuses = [*[RunStatus.completed.value] * len(prior_successes), *(status for status, _ in outcomes)]
        parent_status = aggregate_review_status(statuses)
        successful = [*prior_successes.values(), *(result for _, result in outcomes if result is not None)]
        payload = {"query": {"analysis_mode": "trade_review", "symbol": "MULTI", "period": "multi"}, "review_children": successful, "review_result": [item for result in successful for item in result.get("review_result", [])], "status": parent_status.value}
        await self.repository.update_run_result(execution.analysis_id, payload)
        await self.repository.finish_run(execution.user_id, execution.analysis_id, status=parent_status)
        await events.append_event(execution.analysis_id, "result", "complete", "复盘完成" if parent_status == RunStatus.completed else "复盘部分完成", {"result": payload, "completed": len(successful), "total": len(children)}, terminal=True)

    async def cancel(self, execution_id: str) -> None:
        task = self.tasks.get(uuid.UUID(str(execution_id)))
        if task is not None:
            task.cancel()


DEFAULT_EXECUTION_MANAGER = AnalysisExecutionManager(AnalysisTaskRepository())
