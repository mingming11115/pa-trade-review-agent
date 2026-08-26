from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.analysis.execution.runs import get_analysis_llm_transcript
from app.analysis.tasks.models import AnalysisRun, RunStatus
from app.core.database import SessionFactory, ensure_schema
from app.core.errors import AppError
from app.core.models import DemoAnalysisResponse


UTC = timezone.utc
logger = logging.getLogger(__name__)


class AnalysisHistorySummary(BaseModel):
    analysis_id: str
    mode: str
    symbol: str
    period: str
    status: str
    direction: str
    favorite: bool
    notes: str
    tags: list[str]
    task_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None
    result_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AnalysisHistoryUpdate(BaseModel):
    favorite: bool | None = None
    notes: str | None = Field(None, max_length=5000)
    tags: list[str] | None = None


def _has_transcript_text(transcript: Any) -> bool:
    return isinstance(transcript, dict) and any(
        str((transcript.get(stage) or {}).get(field) or "").strip()
        for stage in ("stage1", "stage2")
        for field in ("reasoning", "content")
    )


def _history_payload_from_run(run: AnalysisRun) -> dict[str, Any]:
    payload = dict(run.result_json or {})
    payload["llm_transcript"] = payload.get("llm_transcript") or {}
    payload.setdefault("favorite", False)
    payload.setdefault("notes", "")
    payload.setdefault("tags", [])
    return payload


async def persist_analysis_result(
    result: DemoAnalysisResponse,
    *,
    task_id: uuid.UUID | str | None = None,
    execution_id: uuid.UUID | str | None = None,
    result_id: uuid.UUID | str | None = None,
) -> None:
    try:
        await ensure_schema()
        transcript = await get_analysis_llm_transcript(result.analysis_id)
        payload = result.model_dump(mode="json")
        payload["llm_transcript"] = transcript
        async with SessionFactory() as session:
            record = await session.get(AnalysisRun, result.analysis_id)
            if record is None:
                record = AnalysisRun(analysis_id=result.analysis_id)
                session.add(record)
            record.task_id = uuid.UUID(str(task_id)) if task_id else record.task_id
            record.result_json = payload
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
) -> list[AnalysisHistorySummary]:
    await ensure_schema()
    async with SessionFactory() as session:
        statement = select(AnalysisRun).where(AnalysisRun.parent_analysis_id.is_(None))
        if task_id:
            statement = statement.where(AnalysisRun.task_id == uuid.UUID(str(task_id)))
        if symbol:
            statement = statement.where(AnalysisRun.symbol == symbol)
        if period:
            statement = statement.where(AnalysisRun.period == period)
        if mode:
            statement = statement.where(AnalysisRun.mode == mode)
        if favorite is not None:
            statement = statement.where(AnalysisRun.result_json["favorite"].as_boolean() == favorite)  # type: ignore[index]
        statement = statement.order_by(desc(AnalysisRun.created_at)).limit(limit)
        records = (await session.scalars(statement)).all()
    summaries: list[AnalysisHistorySummary] = []
    for record in records:
        payload = _history_payload_from_run(record)
        summaries.append(AnalysisHistorySummary(
            analysis_id=record.analysis_id,
            mode=record.mode,
            symbol=record.symbol,
            period=record.period,
            status=record.status,
            direction=record.direction,
            favorite=bool(payload.get("favorite", False)),
            notes=str(payload.get("notes") or ""),
            tags=list(payload.get("tags") or []),
            task_id=record.task_id,
            execution_id=None,
            result_id=None,
            created_at=record.created_at,
            updated_at=record.updated_at,
        ))
    return summaries


async def list_history_for_live_task(
    *,
    task_id: str,
    symbol: str,
    period: str,
    limit: int = 200,
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
                AnalysisRun.parent_analysis_id.is_(None),
            )
            .order_by(desc(AnalysisRun.created_at))
            .limit(limit)
        )
        records = (await session.scalars(statement)).all()
    return [
        AnalysisHistorySummary(
            analysis_id=record.analysis_id,
            mode=record.mode,
            symbol=record.symbol,
            period=record.period,
            status=record.status,
            direction=record.direction,
            favorite=bool((record.result_json or {}).get("favorite", False)),
            notes=str((record.result_json or {}).get("notes") or ""),
            tags=list((record.result_json or {}).get("tags") or []),
            task_id=record.task_id,
            execution_id=None,
            result_id=None,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record in records
    ]


async def get_analysis_history(analysis_id: str) -> dict[str, Any]:
    await ensure_schema()
    async with SessionFactory() as session:
        record = await session.get(AnalysisRun, analysis_id)
        if record is None:
            raise AppError("analysis_not_found", "分析记录不存在", 404)
        payload = _history_payload_from_run(record)
    if not _has_transcript_text(payload.get("llm_transcript")):
        transcript = await get_analysis_llm_transcript(analysis_id)
        if _has_transcript_text(transcript):
            payload["llm_transcript"] = transcript
            async with SessionFactory() as session:
                db_record = await session.get(AnalysisRun, analysis_id)
                if db_record is not None:
                    db_record.result_json = payload
                    db_record.updated_at = datetime.now(UTC)
                    await session.commit()
    return payload


async def backfill_analysis_history_transcripts() -> dict[str, int]:
    await ensure_schema()
    async with SessionFactory() as session:
        rows = list((await session.scalars(select(AnalysisRun).where(AnalysisRun.parent_analysis_id.is_(None)))).all())
    recovered = 0
    for record in rows:
        payload = dict(record.result_json or {})
        if _has_transcript_text(payload.get("llm_transcript")):
            continue
        transcript = await get_analysis_llm_transcript(record.analysis_id)
        if _has_transcript_text(transcript):
            payload["llm_transcript"] = transcript
            async with SessionFactory() as session:
                db_record = await session.get(AnalysisRun, record.analysis_id)
                if db_record is not None:
                    db_record.result_json = payload
                    db_record.updated_at = datetime.now(UTC)
                    await session.commit()
                    recovered += 1
    return {"total": len(rows), "recovered": recovered, "already_present": len(rows) - recovered, "unavailable": 0}


async def update_analysis_history(analysis_id: str, update: AnalysisHistoryUpdate) -> AnalysisHistorySummary:
    await ensure_schema()
    async with SessionFactory() as session:
        record = await session.get(AnalysisRun, analysis_id)
        if record is None:
            raise AppError("analysis_not_found", "分析记录不存在", 404)
        payload = dict(record.result_json or {})
        if update.favorite is not None:
            payload["favorite"] = update.favorite
        if update.notes is not None:
            payload["notes"] = update.notes
        if update.tags is not None:
            payload["tags"] = list(dict.fromkeys(tag.strip() for tag in update.tags if tag.strip()))[:20]
        record.result_json = payload
        record.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(record)
    return AnalysisHistorySummary(
        analysis_id=record.analysis_id,
        mode=record.mode,
        symbol=record.symbol,
        period=record.period,
        status=record.status,
        direction=record.direction,
        favorite=bool(payload.get("favorite", False)),
        notes=str(payload.get("notes") or ""),
        tags=list(payload.get("tags") or []),
        task_id=record.task_id,
        execution_id=None,
        result_id=None,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
