from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.analysis.execution.runs import get_analysis_llm_transcript
from app.analysis.tasks.models import AnalysisAnnotation, AnalysisRun, RunStatus
from app.core.database import SessionFactory, ensure_schema
from app.core.errors import AppError
from app.core.models import DemoAnalysisResponse


UTC = timezone.utc
logger = logging.getLogger(__name__)


def _owner_clause(user_id: uuid.UUID | None):
    return AnalysisRun.user_id.is_(None) if user_id is None else AnalysisRun.user_id == user_id


class AnalysisHistorySummary(BaseModel):
    run_id: str
    mode: str
    symbol: str
    period: str
    status: str
    direction: str
    favorite: bool
    notes: str
    tags: list[str]
    task_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AnalysisHistoryUpdate(BaseModel):
    favorite: bool | None = None
    notes: str | None = Field(None, max_length=5000)
    tags: list[str] | None = None


def _coerce_run_id(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _has_transcript_text(transcript: Any) -> bool:
    return isinstance(transcript, dict) and any(
        str((transcript.get(stage) or {}).get(field) or "").strip()
        for stage in ("stage1", "stage2")
        for field in ("reasoning", "content")
    )


def _summary_from_parts(record: AnalysisRun, annotation: AnalysisAnnotation | None) -> AnalysisHistorySummary:
    return AnalysisHistorySummary(
        run_id=str(record.id),
        mode=record.mode,
        symbol=record.symbol,
        period=record.period,
        status=record.status,
        direction=record.direction,
        favorite=bool(annotation.favorite) if annotation else False,
        notes=annotation.notes if annotation else "",
        tags=list(annotation.tags or []) if annotation else [],
        task_id=record.task_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def persist_analysis_result(
    result: DemoAnalysisResponse,
    *,
    task_id: uuid.UUID | str | None = None,
) -> None:
    try:
        await ensure_schema()
        run_id = uuid.UUID(str(result.run_id))
        transcript = await get_analysis_llm_transcript(run_id)
        payload = result.model_dump(mode="json")
        payload["llm_transcript"] = transcript
        async with SessionFactory() as session:
            record = await session.get(AnalysisRun, run_id)
            if record is None:
                record = AnalysisRun(id=run_id)
                session.add(record)
            record.task_id = uuid.UUID(str(task_id)) if task_id else record.task_id
            record.result_json = payload
            record.resolved_symbol = result.resolved_symbol
            record.mode = result.query.analysis_mode
            record.symbol = result.query.symbol
            record.period = result.query.period.value
            record.direction = result.analysis.direction.value
            record.terminal_outcome = str(result.stage2.terminal.outcome if result.stage2 else "wait")
            record.status = str(result.status or "completed")
            record.updated_at = datetime.now(UTC)
            await session.commit()
    except Exception as exc:
        logger.warning("analysis history persist failed: %s", type(exc).__name__)


async def list_analysis_history(
    *,
    limit: int = 100,
    symbol: str | None = None,
    period: str | None = None,
    mode: str | None = None,
    favorite: bool | None = None,
    task_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> list[AnalysisHistorySummary]:
    await ensure_schema()
    async with SessionFactory() as session:
        statement = select(AnalysisRun).where(_owner_clause(user_id))
        if task_id:
            statement = statement.where(AnalysisRun.task_id == uuid.UUID(str(task_id)))
        if symbol:
            statement = statement.where(AnalysisRun.symbol == symbol)
        if period:
            statement = statement.where(AnalysisRun.period == period)
        if mode:
            statement = statement.where(AnalysisRun.mode == mode)
        statement = statement.order_by(desc(AnalysisRun.created_at)).limit(limit)
        records = (await session.scalars(statement)).all()
        annotations: dict[uuid.UUID, AnalysisAnnotation] = {}
        if user_id is not None and records:
            run_ids = [record.id for record in records]
            rows = (await session.scalars(
                select(AnalysisAnnotation).where(
                    AnalysisAnnotation.run_id.in_(run_ids),
                    AnalysisAnnotation.user_id == user_id,
                )
            )).all()
            annotations = {row.run_id: row for row in rows}
    if favorite is not None:
        records = [
            record
            for record in records
            if bool(annotations.get(record.id).favorite if annotations.get(record.id) else False) == favorite
        ]
    return [_summary_from_parts(record, annotations.get(record.id)) for record in records]


async def list_history_for_live_task(
    *,
    task_id: str,
    symbol: str,
    period: str,
    limit: int = 200,
    user_id: uuid.UUID | None = None,
) -> list[AnalysisHistorySummary]:
    await ensure_schema()
    task_uuid = uuid.UUID(str(task_id))
    async with SessionFactory() as session:
        statement = (
            select(AnalysisRun)
            .where(
                AnalysisRun.symbol == symbol,
                AnalysisRun.period == period,
                AnalysisRun.task_id == task_uuid,
                _owner_clause(user_id),
            )
            .order_by(desc(AnalysisRun.created_at))
            .limit(limit)
        )
        records = (await session.scalars(statement)).all()
        annotations: dict[uuid.UUID, AnalysisAnnotation] = {}
        if user_id is not None and records:
            run_ids = [record.id for record in records]
            rows = (await session.scalars(
                select(AnalysisAnnotation).where(
                    AnalysisAnnotation.run_id.in_(run_ids),
                    AnalysisAnnotation.user_id == user_id,
                )
            )).all()
            annotations = {row.run_id: row for row in rows}
    return [_summary_from_parts(record, annotations.get(record.id)) for record in records]


async def get_analysis_history(
    run_id: uuid.UUID | str,
    *,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    await ensure_schema()
    async with SessionFactory() as session:
        record = await session.scalar(
            select(AnalysisRun).where(
                AnalysisRun.id == _coerce_run_id(run_id),
                _owner_clause(user_id),
            )
        )
        if record is None:
            raise AppError("analysis_not_found", "分析记录不存在", 404)
        payload = dict(record.result_json or {})
    if not _has_transcript_text(payload.get("llm_transcript")):
        transcript = await get_analysis_llm_transcript(record.id)
        if _has_transcript_text(transcript):
            payload["llm_transcript"] = transcript
    return payload


async def update_analysis_history(
    run_id: uuid.UUID | str,
    update: AnalysisHistoryUpdate,
    *,
    user_id: uuid.UUID | None,
) -> AnalysisHistorySummary:
    await ensure_schema()
    run_uuid = _coerce_run_id(run_id)
    async with SessionFactory() as session:
        record = await session.scalar(
            select(AnalysisRun).where(AnalysisRun.id == run_uuid, _owner_clause(user_id))
        )
        if record is None:
            raise AppError("analysis_not_found", "分析记录不存在", 404)
    async with SessionFactory() as session:
        annotation = await session.scalar(
            select(AnalysisAnnotation).where(
                AnalysisAnnotation.run_id == run_uuid,
                AnalysisAnnotation.user_id == user_id,
            )
        )
        if annotation is None:
            annotation = AnalysisAnnotation(run_id=run_uuid, user_id=user_id)
            session.add(annotation)
        if update.favorite is not None:
            annotation.favorite = update.favorite
        if update.notes is not None:
            annotation.notes = update.notes
        if update.tags is not None:
            annotation.tags = list(dict.fromkeys(tag.strip() for tag in update.tags if tag.strip()))[:20]
        annotation.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(annotation)
    return _summary_from_parts(record, annotation)
