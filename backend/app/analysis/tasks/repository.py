from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.analysis.tasks.lifecycle import transition_run
from app.analysis.tasks.models import (
    AnalysisAnnotation,
    AnalysisRun,
    AnalysisStageAttempt,
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


@dataclass(frozen=True, slots=True)
class RunCreateSpec:
    period: str
    query_json: dict[str, Any]
    resolved_symbol: str
    bars_json: list[dict[str, Any]] | None
    bars_hash: str | None
    mode: str
    symbol: str


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
            attempts = await session.scalar(
                select(func.count()).select_from(AnalysisRun).where(AnalysisRun.task_id == task_id)
            )
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

    async def create_runs_for_task(
        self,
        owner_id: uuid.UUID | None,
        task_id: uuid.UUID,
        specs: list[RunCreateSpec],
    ) -> list[AnalysisRun]:
        periods = [spec.period.strip() for spec in specs]
        if not periods or any(not period for period in periods):
            raise AppError("analysis_run_period_required", "运行周期不能为空", 422)
        if len(periods) != len(set(periods)):
            raise AppError("analysis_run_period_duplicate", "同一任务的运行周期不能重复", 409)

        async with self.sessions() as session:
            task = await session.scalar(
                select(AnalysisTask)
                .where(AnalysisTask.id == task_id, _owner_clause(AnalysisTask.user_id, owner_id))
                .with_for_update()
            )
            if task is None:
                raise AppError("analysis_task_not_found", "分析任务不存在", 404)
            existing = await session.scalar(
                select(func.count()).select_from(AnalysisRun).where(AnalysisRun.task_id == task_id)
            )
            if task.status != TaskStatus.pending.value or existing:
                raise AppError("analysis_task_already_executed", "分析任务只能执行一次", 409)

            records = [
                AnalysisRun(
                    task_id=task_id,
                    user_id=owner_id,
                    status=RunStatus.queued.value,
                    query_json=spec.query_json,
                    resolved_symbol=spec.resolved_symbol,
                    bars_json=spec.bars_json,
                    bars_hash=spec.bars_hash,
                    mode=spec.mode,
                    symbol=spec.symbol,
                    period=spec.period,
                )
                for spec in specs
            ]
            try:
                session.add_all(records)
                task.status = TaskStatus.running.value
                task.updated_at = datetime.now(UTC)
                task.version += 1
                await session.commit()
                for record in records:
                    await session.refresh(record)
            except IntegrityError as exc:
                await session.rollback()
                raise AppError("analysis_task_already_executed", "分析任务只能执行一次", 409) from exc
            return records

    async def aggregate_task_status(self, task_id: uuid.UUID) -> TaskStatus:
        async with self.sessions() as session:
            task = await session.get(AnalysisTask, task_id, with_for_update=True)
            if task is None:
                raise AppError("analysis_task_not_found", "分析任务不存在", 404)
            statuses = list(
                (await session.scalars(select(AnalysisRun.status).where(AnalysisRun.task_id == task_id))).all()
            )
            if not statuses or any(status in ACTIVE_RUNS for status in statuses):
                target = TaskStatus.running
            elif all(status in {RunStatus.completed.value, RunStatus.degraded.value, RunStatus.completed_with_warnings.value} for status in statuses):
                target = TaskStatus.completed
            elif any(status in {RunStatus.completed.value, RunStatus.degraded.value, RunStatus.completed_with_warnings.value} for status in statuses):
                target = TaskStatus.completed_with_warnings
            elif all(status == RunStatus.cancelled.value for status in statuses):
                target = TaskStatus.cancelled
            elif all(status == RunStatus.timed_out.value for status in statuses):
                target = TaskStatus.timed_out
            else:
                target = TaskStatus.failed
            task.status = target.value
            task.updated_at = datetime.now(UTC)
            await session.commit()
            return target

    async def list_stage_attempts(self, run_id: uuid.UUID) -> list[AnalysisStageAttempt]:
        async with self.sessions() as session:
            return list((await session.scalars(
                select(AnalysisStageAttempt)
                .where(AnalysisStageAttempt.run_id == run_id)
                .order_by(AnalysisStageAttempt.stage, AnalysisStageAttempt.attempt)
            )).all())

    async def upsert_stage_attempt(
        self,
        run_id: uuid.UUID,
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
    ) -> AnalysisStageAttempt:
        async with self.sessions() as session:
            record = await session.scalar(
                select(AnalysisStageAttempt).where(
                    AnalysisStageAttempt.run_id == run_id,
                    AnalysisStageAttempt.stage == stage,
                    AnalysisStageAttempt.attempt == attempt,
                )
            )
            now = datetime.now(UTC)
            fields = {
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
                "updated_at": now,
            }
            if record is None:
                record = AnalysisStageAttempt(
                    run_id=run_id,
                    stage=stage,
                    attempt=attempt,
                    started_at=now,
                    **fields,
                )
                session.add(record)
            else:
                for key, value in fields.items():
                    setattr(record, key, value)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_annotation(self, owner_id: uuid.UUID | None, run_id: uuid.UUID) -> AnalysisAnnotation | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(AnalysisAnnotation).where(
                    AnalysisAnnotation.run_id == run_id,
                    AnalysisAnnotation.user_id == owner_id,
                )
            )

    async def upsert_annotation(
        self,
        owner_id: uuid.UUID | None,
        run_id: uuid.UUID,
        *,
        favorite: bool | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
    ) -> AnalysisAnnotation:
        async with self.sessions() as session:
            record = await session.scalar(
                select(AnalysisAnnotation).where(
                    AnalysisAnnotation.run_id == run_id,
                    AnalysisAnnotation.user_id == owner_id,
                )
            )
            now = datetime.now(UTC)
            if record is None:
                record = AnalysisAnnotation(
                    run_id=run_id,
                    user_id=owner_id,
                    favorite=bool(favorite),
                    notes=notes or "",
                    tags=tags or [],
                )
                session.add(record)
            else:
                if favorite is not None:
                    record.favorite = favorite
                if notes is not None:
                    record.notes = notes
                if tags is not None:
                    record.tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))[:20]
                record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return record

    async def get_run(self, owner_id: uuid.UUID | None, run_id: uuid.UUID) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.scalar(select(AnalysisRun).where(
                AnalysisRun.id == run_id,
                _owner_clause(AnalysisRun.user_id, owner_id),
            ))
        if record is None:
            raise AppError("analysis_run_not_found", "分析运行不存在", 404)
        return record

    async def get_run_unscoped(self, run_id: uuid.UUID) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, run_id)
        if record is None:
            raise AppError("analysis_run_not_found", "分析运行不存在", 404)
        return record

    async def mark_run_running(self, run_id: uuid.UUID) -> None:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, run_id)
            if record is None:
                return
            now = datetime.now(UTC)
            record.status = RunStatus.running.value
            record.started_at = now
            record.heartbeat_at = now
            record.updated_at = now
            await session.commit()

    async def request_run_cancel(self, owner_id: uuid.UUID | None, run_id: uuid.UUID) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.scalar(select(AnalysisRun).where(
                AnalysisRun.id == run_id,
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

    async def update_run_result(self, run_id: uuid.UUID, payload: dict[str, Any]) -> AnalysisRun:
        async with self.sessions() as session:
            record = await session.get(AnalysisRun, run_id)
            if record is None:
                raise AppError("analysis_run_not_found", "分析运行不存在", 404)
            record.result_json = payload
            query = payload.get("query") or {}
            stage2 = payload.get("stage2") or {}
            record.mode = str(query.get("analysis_mode") or record.mode)
            record.symbol = str(query.get("symbol") or record.symbol)
            record.period = str(query.get("period") or record.period)
            record.resolved_symbol = str(payload.get("resolved_symbol") or record.resolved_symbol)
            record.direction = str((payload.get("analysis") or {}).get("direction") or record.direction)
            record.terminal_outcome = str((stage2.get("terminal") or {}).get("outcome") or record.terminal_outcome)
            record.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(record)
            return record

    async def finish_run(
        self,
        owner_id: uuid.UUID | None,
        run_id: uuid.UUID,
        *,
        status: RunStatus,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> AnalysisRun:
        async with self.sessions() as session:
            run = await session.scalar(select(AnalysisRun).where(
                AnalysisRun.id == run_id,
                _owner_clause(AnalysisRun.user_id, owner_id),
            ))
            if run is None:
                raise AppError("analysis_run_not_found", "分析运行不存在", 404)
            run.status = status.value
            run.failure_code = failure_code
            run.failure_message = failure_message
            run.completed_at = datetime.now(UTC)
            run.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(run)
            task_id = run.task_id
        await self.aggregate_task_status(task_id)
        return run

    async def list_runs(self, owner_id: uuid.UUID | None, task_id: uuid.UUID) -> list[AnalysisRun]:
        await self.get_task(owner_id, task_id)
        async with self.sessions() as session:
            records = list((await session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.task_id == task_id, _owner_clause(AnalysisRun.user_id, owner_id))
                .order_by(desc(AnalysisRun.created_at), desc(AnalysisRun.id))
            )).all())
        return records

    async def get_run_detail(self, owner_id: uuid.UUID | None, run_id: uuid.UUID) -> dict[str, Any]:
        record = await self.get_run(owner_id, run_id)
        payload = dict(record.result_json or {})
        if record.bars_json is not None and "bars" not in payload:
            payload["bars"] = record.bars_json
        from app.analysis.history.service import _has_transcript_text
        from app.analysis.execution.runs import get_analysis_llm_transcript

        stored = payload.get("llm_transcript")
        if _has_transcript_text(stored):
            transcript = stored
        else:
            transcript = await get_analysis_llm_transcript(record.id)
        payload["llm_transcript"] = transcript
        return payload
