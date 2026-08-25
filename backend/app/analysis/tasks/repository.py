from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.analysis.tasks.lifecycle import transition_run, transition_task
from app.analysis.tasks.models import (
    AnalysisRun,
    AnalysisTask,
    AnalysisTaskCreate,
    AnalysisTaskUpdate,
    RunStatus,
    StageRunStatus,
    TaskStatus,
    normalize_analysis_symbol,
)
from app.core.database import SessionFactory
from app.core.errors import AppError


UTC = timezone.utc
ACTIVE_RUNS = {
    RunStatus.queued.value,
    RunStatus.running.value,
    RunStatus.cancel_requested.value,
}


@dataclass(slots=True)
class TaskPage:
    items: list[AnalysisTask]
    next_cursor: str | None


def _owner_clause(column, owner_id: uuid.UUID | None):
    return column.is_(None) if owner_id is None else column == owner_id


def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created, row_id = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(created), uuid.UUID(row_id)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AppError("invalid_cursor", "分页游标无效", 400) from exc


class AnalysisTaskRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession] = SessionFactory):
        self.sessions = sessions

    async def find_live_analysis_task(
        self,
        owner_id: uuid.UUID | None,
        symbol: str,
        period: str,
        *,
        exclude_task_id: uuid.UUID | None = None,
    ) -> AnalysisTask | None:
        normalized = normalize_analysis_symbol(symbol)
        async with self.sessions() as session:
            statement = select(AnalysisTask).where(
                _owner_clause(AnalysisTask.user_id, owner_id),
                AnalysisTask.kind == "analysis",
                AnalysisTask.archived_at.is_(None),
                AnalysisTask.analysis_symbol == normalized,
                AnalysisTask.analysis_period == period,
            )
            if exclude_task_id is not None:
                statement = statement.where(AnalysisTask.id != exclude_task_id)
            return await session.scalar(statement)

    async def _assert_live_slot_available(
        self,
        session: AsyncSession,
        owner_id: uuid.UUID | None,
        symbol: str,
        period: str,
        *,
        exclude_task_id: uuid.UUID | None = None,
    ) -> tuple[str, str]:
        normalized = normalize_analysis_symbol(symbol)
        statement = select(AnalysisTask).where(
            _owner_clause(AnalysisTask.user_id, owner_id),
            AnalysisTask.kind == "analysis",
            AnalysisTask.archived_at.is_(None),
            AnalysisTask.analysis_symbol == normalized,
            AnalysisTask.analysis_period == period,
        )
        if exclude_task_id is not None:
            statement = statement.where(AnalysisTask.id != exclude_task_id)
        existing = await session.scalar(statement)
        if existing is not None:
            raise AppError(
                "analysis_task_symbol_period_conflict",
                f"{normalized} {period} 已有实时分析任务，请打开原任务运行。",
                409,
            )
        return normalized, period

    async def create_task(self, owner_id: uuid.UUID | None, payload: AnalysisTaskCreate) -> AnalysisTask:
        config = payload.config.model_dump(mode="json")
        analysis_symbol = None
        analysis_period = None
        async with self.sessions() as session:
            if payload.kind == "analysis":
                analysis_symbol, analysis_period = await self._assert_live_slot_available(
                    session,
                    owner_id,
                    str(config["symbol"]),
                    str(config["period"]),
                )
            record = AnalysisTask(
                user_id=owner_id,
                kind=payload.kind,
                title=payload.title,
                description=payload.description,
                status=TaskStatus.pending.value,
                config_json=config,
                analysis_symbol=analysis_symbol,
                analysis_period=analysis_period,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def ensure_live_analysis_task(
        self,
        owner_id: uuid.UUID | None,
        *,
        symbol: str,
        period: str,
        title: str | None = None,
    ) -> AnalysisTask:
        existing = await self.find_live_analysis_task(owner_id, symbol, period)
        if existing is not None:
            return existing
        normalized = normalize_analysis_symbol(symbol)
        try:
            return await self.create_task(
                owner_id,
                AnalysisTaskCreate(
                    kind="analysis",
                    title=title or f"{normalized} · {period}",
                    description="实时 K 线分析任务",
                    config={"symbol": normalized, "period": period},
                ),
            )
        except AppError as exc:
            if exc.code != "analysis_task_symbol_period_conflict":
                raise
            found = await self.find_live_analysis_task(owner_id, symbol, period)
            if found is None:
                raise
            return found

    async def get_task(self, owner_id: uuid.UUID | None, task_id: uuid.UUID, *, lock: bool = False) -> AnalysisTask:
        statement = select(AnalysisTask).where(
            AnalysisTask.id == task_id,
            _owner_clause(AnalysisTask.user_id, owner_id),
        )
        if lock:
            statement = statement.with_for_update()
        async with self.sessions() as session:
            record = await session.scalar(statement)
        if record is None:
            raise AppError("analysis_task_not_found", "分析任务不存在", 404)
        return record

    async def update_task(self, owner_id: uuid.UUID | None, task_id: uuid.UUID, payload: AnalysisTaskUpdate) -> AnalysisTask:
        async with self.sessions() as session:
            record = await session.scalar(
                select(AnalysisTask).where(
                    AnalysisTask.id == task_id,
                    _owner_clause(AnalysisTask.user_id, owner_id),
                ).with_for_update()
            )
            if record is None:
                raise AppError("analysis_task_not_found", "分析任务不存在", 404)
            attempts = await session.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.task_id == task_id, AnalysisRun.parent_analysis_id.is_(None)))
            if record.status != TaskStatus.pending.value or attempts:
                raise AppError("analysis_task_not_editable", "任务运行后不能编辑", 409)
            if record.version != payload.version:
                raise AppError("analysis_task_version_conflict", "任务已被其他操作更新，请刷新后重试", 409)
            config = payload.validated_config(record.kind)
            config_json = config.model_dump(mode="json")
            if record.kind == "analysis":
                analysis_symbol, analysis_period = await self._assert_live_slot_available(
                    session,
                    owner_id,
                    str(config_json["symbol"]),
                    str(config_json["period"]),
                    exclude_task_id=task_id,
                )
                record.analysis_symbol = analysis_symbol
                record.analysis_period = analysis_period
            record.title = payload.title
            record.description = payload.description
            record.config_json = config_json
            record.version += 1
            record.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return record

    async def list_tasks(
        self,
        owner_id: uuid.UUID | None,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        keyword: str | None = None,
    ) -> TaskPage:
        limit = min(max(limit, 1), 200)
        statement = select(AnalysisTask).where(_owner_clause(AnalysisTask.user_id, owner_id))
        if status:
            statement = statement.where(AnalysisTask.status == status)
        if kind:
            statement = statement.where(AnalysisTask.kind == kind)
        if keyword:
            term = f"%{keyword.strip()}%"
            statement = statement.where(or_(AnalysisTask.title.ilike(term), AnalysisTask.description.ilike(term)))
        if cursor:
            created_at, row_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    AnalysisTask.created_at < created_at,
                    and_(AnalysisTask.created_at == created_at, AnalysisTask.id < row_id),
                )
            )
        statement = statement.order_by(desc(AnalysisTask.created_at), desc(AnalysisTask.id)).limit(limit + 1)
        async with self.sessions() as session:
            records = list((await session.scalars(statement)).all())
        has_more = len(records) > limit
        items = records[:limit]
        next_cursor = _encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
        return TaskPage(items=items, next_cursor=next_cursor)

    async def create_run(
        self,
        owner_id: uuid.UUID | None,
        task_id: uuid.UUID,
        input_json: dict[str, Any],
        *,
        run_id: str,
        sequence: int,
        parent_analysis_id: str | None = None,
        work_key: str | None = None,
        bars_json: list[dict[str, Any]] | None = None,
        bars_hash: str | None = None,
        prompt_versions_json: dict[str, Any] | None = None,
        model_config_json: dict[str, Any] | None = None,
        mode: str = "historical",
        symbol: str = "",
        period: str = "",
    ) -> AnalysisRun:
        async with self.sessions() as session:
            task = await session.scalar(
                select(AnalysisTask)
                .where(AnalysisTask.id == task_id, _owner_clause(AnalysisTask.user_id, owner_id))
                .with_for_update()
            )
            if task is None:
                raise AppError("analysis_task_not_found", "分析任务不存在", 404)
            if parent_analysis_id is None:
                active = await session.scalar(
                    select(func.count())
                    .select_from(AnalysisRun)
                    .where(AnalysisRun.task_id == task_id, AnalysisRun.parent_analysis_id.is_(None), AnalysisRun.status.in_(ACTIVE_RUNS))
                )
                if active:
                    raise AppError("analysis_already_running", "分析任务正在运行", 409)
                previous = await session.scalar(
                    select(AnalysisRun)
                    .where(AnalysisRun.task_id == task_id, AnalysisRun.parent_analysis_id.is_(None))
                    .order_by(desc(AnalysisRun.sequence))
                    .limit(1)
                )
                if previous is not None and sequence <= previous.sequence:
                    sequence = previous.sequence + 1
            else:
                sequence = None
            record = AnalysisRun(
                analysis_id=run_id,
                task_id=task_id,
                user_id=owner_id,
                parent_analysis_id=parent_analysis_id,
                work_key=work_key,
                sequence=sequence,
                status=RunStatus.queued.value,
                input_json=input_json,
                bars_json=bars_json,
                bars_hash=bars_hash,
                prompt_versions_json=prompt_versions_json or {},
                model_config_json=model_config_json or {},
                mode=mode,
                symbol=symbol,
                period=period,
            )
            if parent_analysis_id is None:
                task.status = transition_task(TaskStatus(task.status), TaskStatus.running, repeatable=task.kind in {"analysis", "review"}).value
                task.latest_analysis_id = run_id
                task.updated_at = datetime.now(UTC)
                task.version += 1
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def add_snapshot(self, snapshot: Any) -> Any:
        # Deprecated by the consolidated schema plan; kept only until callers are migrated.
        return snapshot

    async def create_review_children(self, execution: AnalysisRun, inputs: list[dict[str, Any]]) -> list[AnalysisRun]:
        records = [
            AnalysisRun(
                analysis_id=str(item.get("analysis_id") or uuid.uuid4()),
                task_id=execution.task_id,
                user_id=execution.user_id,
                parent_analysis_id=execution.analysis_id,
                work_key=str(item["key"]),
                sequence=None,
                status=RunStatus.queued.value,
                input_json=item,
                mode=execution.mode,
                symbol=str(item.get("resolved_symbol") or execution.symbol),
                period=str(item.get("period") or execution.period),
            )
            for item in inputs
        ]
        async with self.sessions() as session:
            session.add_all(records)
            await session.commit()
            for record in records:
                await session.refresh(record)
        return records

    async def upsert_stage_attempt(
        self,
        analysis_id: str,
        *,
        stage: str,
        attempt: int,
        status: str,
        provider: str = "",
        model: str = "",
        provider_request_id: str | None = None,
        response_model: str | None = None,
        duration_ms: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        raw_content: str = "",
        reasoning_content: str = "",
        raw_response: dict[str, Any] | None = None,
        normalized_output: dict[str, Any] | None = None,
        validation_errors: list[Any] | None = None,
        provider_error: dict[str, Any] | None = None,
        prompt_metadata: dict[str, Any] | None = None,
    ) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, analysis_id)
            if record is None:
                raise AppError("analysis_run_not_found", "分析运行不存在", 404)
            stages = [item for item in (record.stage_runs_json or []) if not (str(item.get("stage")) == stage and int(item.get("attempt") or 0) == attempt)]
            payload = {
                "stage": stage,
                "attempt": attempt,
                "status": status,
                "provider": provider,
                "model": model,
                "provider_request_id": provider_request_id,
                "response_model": response_model,
                "duration_ms": duration_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "raw_content": raw_content,
                "reasoning_content": reasoning_content,
                "raw_response": raw_response,
                "normalized_output": normalized_output,
                "validation_errors": validation_errors,
                "provider_error": provider_error,
                "prompt_metadata": prompt_metadata or {},
                "started_at": datetime.now(UTC).isoformat(),
            }
            stages.append(payload)
            stages.sort(key=lambda item: (str(item.get("stage") or ""), int(item.get("attempt") or 0)))
            record.stage_runs_json = stages
            record.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return record

    async def update_review_child(self, child_id: str, *, status: RunStatus, result: dict[str, Any] | None = None, failure_code: str | None = None, failure_message: str | None = None) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, child_id)
            if record is None:
                raise AppError("analysis_child_not_found", "复盘子任务不存在", 404)
            now = datetime.now(UTC)
            record.status = status.value
            record.result_json = result
            record.failure_code = failure_code
            record.failure_message = failure_message
            if status == RunStatus.running:
                record.started_at = now
            else:
                record.completed_at = now
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return record

    async def review_retry_work_keys(self, owner_id: uuid.UUID | None, task_id: uuid.UUID, before_sequence: int | None = None) -> set[str] | None:
        async with self.sessions() as session:
            statement = select(AnalysisRun).where(
                AnalysisRun.task_id == task_id,
                AnalysisRun.parent_analysis_id.is_(None),
                _owner_clause(AnalysisRun.user_id, owner_id),
            )
            if before_sequence is not None:
                statement = statement.where(AnalysisRun.sequence < before_sequence)
            latest = await session.scalar(statement.order_by(desc(AnalysisRun.sequence)).limit(1))
            if latest is None:
                return None
            child_rows = list((await session.execute(
                select(AnalysisRun.work_key, AnalysisRun.status).where(AnalysisRun.parent_analysis_id == latest.analysis_id)
            )).all())
        if not child_rows:
            return None
        return {key for key, child_status in child_rows if child_status != RunStatus.completed.value}

    async def successful_review_results(self, owner_id: uuid.UUID | None, task_id: uuid.UUID, before_sequence: int) -> dict[str, dict[str, Any]]:
        parent = aliased(AnalysisRun)
        child = aliased(AnalysisRun)
        async with self.sessions() as session:
            rows = list((await session.execute(
                select(child.work_key, child.result_json)
                .select_from(child)
                .join(parent, parent.analysis_id == child.parent_analysis_id)
                .where(
                    parent.task_id == task_id,
                    parent.parent_analysis_id.is_(None),
                    parent.sequence < before_sequence,
                    _owner_clause(parent.user_id, owner_id),
                    child.status == RunStatus.completed.value,
                )
                .order_by(parent.sequence)
            )).all())
        return {key: result for key, result in rows if result is not None}

    async def get_run(self, owner_id: uuid.UUID | None, analysis_id: str) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.scalar(select(AnalysisRun).where(
                AnalysisRun.analysis_id == analysis_id,
                _owner_clause(AnalysisRun.user_id, owner_id),
            ))
        if record is None:
            raise AppError("analysis_run_not_found", "分析运行不存在", 404)
        return record

    async def get_snapshot(self, owner_id: uuid.UUID | None, snapshot_id: uuid.UUID):
        from app.analysis.history.snapshots import get_frozen_snapshot

        snapshot = get_frozen_snapshot(snapshot_id)
        if snapshot is None or snapshot.user_id != owner_id:
            raise AppError("analysis_snapshot_not_found", "分析输入快照不存在", 404)
        return snapshot

    async def get_run_unscoped(self, analysis_id: str) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, analysis_id)
        if record is None:
            raise AppError("analysis_run_not_found", "分析运行不存在", 404)
        return record

    async def mark_run_running(self, analysis_id: str) -> None:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, analysis_id)
            if record is None:
                return
            now = datetime.now(UTC)
            record.status = RunStatus.running.value
            record.started_at = now
            record.heartbeat_at = now
            record.updated_at = now
            await session.commit()

    async def request_run_cancel(self, owner_id: uuid.UUID | None, analysis_id: str) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.scalar(select(AnalysisRun).where(
                AnalysisRun.analysis_id == analysis_id,
                _owner_clause(AnalysisRun.user_id, owner_id),
            ))
            if record is None:
                raise AppError("analysis_run_not_found", "分析运行不存在", 404)
            if record.status in {RunStatus.queued.value, RunStatus.running.value}:
                record.status = RunStatus.cancel_requested.value
                record.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(record)
            return record

    async def update_run_result(self, analysis_id: str, payload: dict[str, Any]) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, analysis_id)
            if record is None:
                raise AppError("analysis_run_not_found", "分析运行不存在", 404)
            record.result_json = payload
            query = payload.get("query") or {}
            stage2 = payload.get("stage2") or {}
            record.mode = str(query.get("analysis_mode") or record.mode)
            record.symbol = str(query.get("symbol") or record.symbol)
            record.period = str(query.get("period") or record.period)
            record.direction = str((payload.get("analysis") or {}).get("direction") or record.direction)
            record.terminal_outcome = str((stage2.get("terminal") or {}).get("outcome") or record.terminal_outcome)
            record.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return record

    async def finish_run(
        self,
        owner_id: uuid.UUID | None,
        analysis_id: str,
        *,
        status: RunStatus,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> AnalysisRun:
        async with self.sessions() as session:
            run = await session.scalar(select(AnalysisRun).where(
                AnalysisRun.analysis_id == analysis_id,
                _owner_clause(AnalysisRun.user_id, owner_id),
            ))
            if run is None:
                raise AppError("analysis_run_not_found", "分析运行不存在", 404)
            task = await session.get(AnalysisTask, run.task_id) if run.task_id is not None else None
            run.status = status.value
            run.failure_code = failure_code
            run.failure_message = failure_message
            run.completed_at = datetime.now(UTC)
            run.updated_at = datetime.now(UTC)
            if task is not None and task.latest_analysis_id == run.analysis_id and run.parent_analysis_id is None:
                if status in {RunStatus.completed, RunStatus.degraded, RunStatus.completed_with_warnings}:
                    task.status = TaskStatus.completed.value
                    if task.kind != "analysis":
                        task.archived_at = datetime.now(UTC)
                elif status == RunStatus.cancelled:
                    task.status = TaskStatus.cancelled.value
                else:
                    task.status = TaskStatus.failed.value
                task.updated_at = datetime.now(UTC)
                task.version += 1
            await session.commit()
            await session.refresh(run)
            return run

    async def list_runs(self, owner_id: uuid.UUID | None, task_id: uuid.UUID) -> list[AnalysisRun]:
        await self.get_task(owner_id, task_id)
        async with self.sessions() as session:
            records = list((await session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.task_id == task_id, _owner_clause(AnalysisRun.user_id, owner_id))
                .order_by(desc(AnalysisRun.created_at), desc(AnalysisRun.analysis_id))
            )).all())
        return records

    async def get_run_detail(self, owner_id: uuid.UUID | None, analysis_id: str) -> dict[str, Any]:
        record = await self.get_run(owner_id, analysis_id)
        payload = dict(record.result_json or {})
        if record.bars_json is not None and "bars" not in payload:
            payload["bars"] = record.bars_json
        from app.analysis.history.service import _has_transcript_text
        from app.analysis.execution.runs import get_analysis_llm_transcript

        stored = payload.get("llm_transcript")
        if _has_transcript_text(stored):
            transcript = stored
        else:
            transcript = await get_analysis_llm_transcript(record.analysis_id)
        payload["llm_transcript"] = transcript
        return payload

    async def create_execution(self, owner_id: uuid.UUID | None, task_id: uuid.UUID, snapshot_id: uuid.UUID) -> AnalysisRun:
        from app.analysis.history.snapshots import get_frozen_snapshot

        snapshot = get_frozen_snapshot(snapshot_id)
        if snapshot is None:
            raise AppError("analysis_snapshot_not_found", "分析输入快照不存在", 404)
        return await self.create_run(
            owner_id,
            task_id,
            {"snapshot_id": str(snapshot.id), "query": snapshot.query_json},
            run_id=str(uuid.uuid4()),
            sequence=1,
            bars_json=snapshot.bars_json,
            bars_hash=snapshot.bars_hash,
            prompt_versions_json=snapshot.prompt_versions_json,
            model_config_json=snapshot.model_config_json,
            mode=str(snapshot.query_json.get("analysis_mode") or "historical"),
            symbol=snapshot.resolved_symbol,
            period=str((snapshot.query_json.get("period") or "")),
        )

    # Backward-compatible aliases during migration window.
    get_execution = get_run
    get_execution_unscoped = get_run_unscoped
    mark_execution_running = mark_run_running
    request_cancel = request_run_cancel
    save_result = update_run_result
    finish_execution = finish_run
    list_executions = list_runs
    get_result = get_run
    get_result_payload = get_run_detail
