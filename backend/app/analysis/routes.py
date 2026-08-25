from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.analysis.history.snapshots import FrozenInputSnapshot, create_input_snapshot, create_review_input_snapshot
from app.analysis.tasks.models import (
    AnalysisRunDetailPublic,
    AnalysisRunListItem,
    AnalysisRunPublic,
    AnalysisTaskCreate,
    AnalysisTaskPublic,
    AnalysisTaskUpdate,
    EnsureLiveAnalysisTaskInput,
    RunStatus,
    SnapshotPreviewPublic,
    StartExecutionInput,
)
from app.analysis.tasks.repository import AnalysisTaskRepository
from app.analysis.execution.manager import DEFAULT_EXECUTION_MANAGER, AnalysisExecutionManager
from app.auth.service import UserPublic, current_user, limit_expensive
from app.market.service import LocalFirstMarketProvider
from app.market.provider import MassiveHistoricalProvider
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.database import get_session
from app.core.logging_context import get_trace_id
from app.trades.service import Trade
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


router = APIRouter(prefix="/api/v1")


def get_analysis_task_repository() -> AnalysisTaskRepository:
    return AnalysisTaskRepository()


def get_analysis_execution_manager() -> AnalysisExecutionManager:
    return DEFAULT_EXECUTION_MANAGER


def get_analysis_provider():
    return LocalFirstMarketProvider(MassiveHistoricalProvider(get_settings()))


def _task_public(task: Any) -> AnalysisTaskPublic:
    return AnalysisTaskPublic(
        id=task.id,
        kind=task.kind,
        title=task.title,
        description=task.description,
        status=task.status,
        config=task.config_json,
        latest_analysis_id=task.latest_analysis_id,
        version=task.version,
        created_at=task.created_at,
        updated_at=task.updated_at,
        archived_at=task.archived_at,
    )


class AnalysisTaskPagePublic(BaseModel):
    items: list[AnalysisTaskPublic]
    next_cursor: str | None


@router.post(
    "/analysis-tasks",
    response_model=AnalysisTaskPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_analysis_task(
    payload: AnalysisTaskCreate,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> AnalysisTaskPublic:
    return _task_public(await repository.create_task(user.id, payload))


@router.post(
    "/analysis-tasks/ensure-live",
    response_model=AnalysisTaskPublic,
)
async def ensure_live_analysis_task(
    payload: EnsureLiveAnalysisTaskInput,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> AnalysisTaskPublic:
    return _task_public(
        await repository.ensure_live_analysis_task(
            user.id,
            symbol=payload.symbol,
            period=payload.period.value,
            title=payload.title,
        )
    )


@router.get("/analysis-tasks", response_model=AnalysisTaskPagePublic)
async def list_analysis_tasks(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None),
    task_status: str | None = Query(None, alias="status"),
    kind: str | None = Query(None),
    keyword: str | None = Query(None, max_length=200),
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> AnalysisTaskPagePublic:
    page = await repository.list_tasks(
        user.id,
        limit=limit,
        cursor=cursor,
        status=task_status,
        kind=kind,
        keyword=keyword,
    )
    return AnalysisTaskPagePublic(
        items=[_task_public(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/analysis-tasks/{task_id}", response_model=AnalysisTaskPublic)
async def get_analysis_task(task_id: uuid.UUID, user: UserPublic = Depends(current_user), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository)) -> AnalysisTaskPublic:
    return _task_public(await repository.get_task(user.id, task_id))


@router.patch("/analysis-tasks/{task_id}", response_model=AnalysisTaskPublic)
async def update_analysis_task(task_id: uuid.UUID, payload: AnalysisTaskUpdate, user: UserPublic = Depends(current_user), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository)) -> AnalysisTaskPublic:
    return _task_public(await repository.update_task(user.id, task_id, payload))


@router.post("/analysis-tasks/{task_id}/preview", response_model=SnapshotPreviewPublic)
async def preview_analysis_task(task_id: uuid.UUID, user: UserPublic = Depends(limit_expensive), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository), provider=Depends(get_analysis_provider), session: AsyncSession = Depends(get_session)) -> SnapshotPreviewPublic:
    task = await repository.get_task(user.id, task_id)
    if task.kind == "review":
        selected_ids = [uuid.UUID(value) for value in task.config_json["selected_trade_ids"]]
        trades = list((await session.scalars(select(Trade).where(Trade.id.in_(selected_ids)))).all())
        found = {str(trade.id) for trade in trades}
        if found != set(task.config_json["selected_trade_ids"]):
            raise AppError("review_trade_not_found", "部分复盘交易不存在", 404)
        snapshot: FrozenInputSnapshot = await create_review_input_snapshot(user.id, task, trades, provider, repository)
    else:
        snapshot = await create_input_snapshot(user.id, task, provider, repository)
    return SnapshotPreviewPublic(
        snapshot_id=snapshot.id,
        confirmation_id=snapshot.confirmation_id,
        expires_at=snapshot.expires_at,
        resolved_symbol=snapshot.resolved_symbol,
        bars_hash=snapshot.bars_hash,
        bar_count=len(snapshot.bars_json),
    )


@router.post("/analysis-tasks/{task_id}/runs", response_model=AnalysisRunPublic, status_code=202)
async def start_analysis_task_run(
    task_id: uuid.UUID,
    user: UserPublic = Depends(limit_expensive),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
    manager: AnalysisExecutionManager = Depends(get_analysis_execution_manager),
    provider=Depends(get_analysis_provider),
    session: AsyncSession = Depends(get_session),
) -> AnalysisRunPublic:
    task = await repository.get_task(user.id, task_id)
    if task.kind == "review":
        selected_ids = [uuid.UUID(value) for value in task.config_json["selected_trade_ids"]]
        trades = list((await session.scalars(select(Trade).where(Trade.id.in_(selected_ids)))).all())
        found = {str(trade.id) for trade in trades}
        if found != set(task.config_json["selected_trade_ids"]):
            raise AppError("review_trade_not_found", "部分复盘交易不存在", 404)
        snapshot: FrozenInputSnapshot = await create_review_input_snapshot(user.id, task, trades, provider, repository)
    else:
        snapshot = await create_input_snapshot(user.id, task, provider, repository)
    run_id = str(uuid.uuid4())
    run = await repository.create_run(
        user.id,
        task_id,
        {"snapshot_id": str(snapshot.id), "query": snapshot.query_json},
        run_id=run_id,
        sequence=1,
        bars_json=snapshot.bars_json,
        bars_hash=snapshot.bars_hash,
        prompt_versions_json=snapshot.prompt_versions_json,
        model_config_json=snapshot.model_config_json,
        mode="historical",
        symbol=snapshot.resolved_symbol,
        period=str((task.config_json or {}).get("period") or ""),
    )
    manager.start(run.analysis_id, get_trace_id())
    return AnalysisRunPublic.model_validate(run)


@router.get("/analysis-tasks/{task_id}/runs", response_model=list[AnalysisRunListItem])
async def list_analysis_task_runs(
    task_id: uuid.UUID,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> list[AnalysisRunListItem]:
    from app.analysis.history.service import list_history_for_live_task

    task = await repository.get_task(user.id, task_id)
    runs = await repository.list_runs(user.id, task_id)
    items: list[AnalysisRunListItem] = [
        AnalysisRunListItem(
            analysis_id=run.analysis_id,
            task_id=run.task_id,
            parent_analysis_id=run.parent_analysis_id,
            work_key=run.work_key,
            sequence=run.sequence,
            status=str(run.status),
            created_at=run.created_at,
            completed_at=run.completed_at,
            direction=run.direction,
            terminal_outcome=run.terminal_outcome,
            symbol=run.symbol,
            period=run.period,
        )
        for run in runs
    ]
    seen_analysis_ids = {item.analysis_id for item in items}
    symbol = task.analysis_symbol or str((task.config_json or {}).get("symbol") or "")
    period = task.analysis_period or str((task.config_json or {}).get("period") or "")
    if task.kind == "analysis" and symbol and period:
        history_rows = await list_history_for_live_task(
            task_id=str(task.id),
            symbol=symbol.strip().upper(),
            period=period,
        )
        for index, history in enumerate(history_rows, start=1):
            if history.analysis_id and history.analysis_id in seen_analysis_ids:
                continue
            items.append(
                AnalysisRunListItem(
                    analysis_id=history.analysis_id,
                    task_id=task.id,
                    parent_analysis_id=None,
                    work_key=None,
                    sequence=-(index),
                    status=history.status,
                    created_at=history.created_at,
                    completed_at=history.updated_at,
                    direction=history.direction,
                    terminal_outcome=None,
                    symbol=history.symbol,
                    period=history.period,
                )
            )
    items.sort(key=lambda item: item.created_at, reverse=True)
    total = len(items)
    for offset, item in enumerate(items):
        item.sequence = total - offset
    return items


@router.get("/analysis-runs/{analysis_id}", response_model=AnalysisRunDetailPublic)
async def get_analysis_run(
    analysis_id: str,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> AnalysisRunDetailPublic:
    run = await repository.get_run(user.id, analysis_id)
    payload = await repository.get_run_detail(user.id, analysis_id)
    return AnalysisRunDetailPublic(
        analysis_id=run.analysis_id,
        task_id=run.task_id,
        parent_analysis_id=run.parent_analysis_id,
        work_key=run.work_key,
        sequence=run.sequence,
        status=str(run.status),
        mode=run.mode,
        symbol=run.symbol,
        period=run.period,
        direction=run.direction,
        terminal_outcome=run.terminal_outcome,
        created_at=run.created_at,
        updated_at=run.updated_at,
        result=payload,
    )


@router.post("/analysis-runs/{analysis_id}/cancel", response_model=AnalysisRunPublic)
async def cancel_analysis_run(
    analysis_id: str,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
    manager: AnalysisExecutionManager = Depends(get_analysis_execution_manager),
) -> AnalysisRunPublic:
    run = await repository.request_run_cancel(user.id, analysis_id)
    await manager.cancel(run.analysis_id)
    return AnalysisRunPublic.model_validate(run)


# Backward-compatible aliases for in-flight callers during migration.
AnalysisExecutionPublic = AnalysisRunPublic
AnalysisExecutionListItem = AnalysisRunListItem
AnalysisResultDetailPublic = AnalysisRunDetailPublic
AnalysisResultPublic = AnalysisRunDetailPublic


@router.post("/analysis-tasks/{task_id}/executions", response_model=AnalysisExecutionPublic, status_code=202)
async def start_analysis_task(task_id: uuid.UUID, payload: StartExecutionInput, user: UserPublic = Depends(limit_expensive), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository), manager: AnalysisExecutionManager = Depends(get_analysis_execution_manager)) -> AnalysisExecutionPublic:
    from app.analysis.history.snapshots import FrozenInputSnapshot as Snapshot
    snapshot: Snapshot = await repository.get_snapshot(user.id, payload.snapshot_id)
    expires = snapshot.expires_at if snapshot.expires_at.tzinfo else snapshot.expires_at.replace(tzinfo=timezone.utc)
    if snapshot.task_id != task_id or snapshot.confirmation_id != payload.confirmation_id or expires <= datetime.now(timezone.utc):
        raise AppError("analysis_snapshot_invalid", "分析输入快照无效或已过期", 409)
    execution = await repository.create_execution(user.id, task_id, snapshot.id)
    manager.start(str(execution.id), get_trace_id())
    return AnalysisExecutionPublic.model_validate(execution)


@router.get("/analysis-tasks/{task_id}/executions", response_model=list[AnalysisExecutionListItem])
async def list_analysis_task_executions(
    task_id: uuid.UUID,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> list[AnalysisExecutionListItem]:
    from app.analysis.history.service import list_history_for_live_task

    task = await repository.get_task(user.id, task_id)
    rows = await repository.list_executions(user.id, task_id)
    items: list[AnalysisExecutionListItem] = [
        AnalysisExecutionListItem(
            id=execution.id,
            task_id=execution.task_id,
            sequence=execution.sequence,
            status=str(execution.status),
            created_at=execution.created_at,
            completed_at=execution.completed_at,
            result_id=result.id if result else None,
            direction=result.direction if result else None,
            symbol=result.symbol if result else None,
            period=result.period if result else None,
            source="execution",
        )
        for execution, result in rows
    ]
    seen_result_ids = {str(item.result_id) for item in items if item.result_id}
    seen_execution_ids = {str(item.id) for item in items}
    symbol = task.analysis_symbol or str((task.config_json or {}).get("symbol") or "")
    period = task.analysis_period or str((task.config_json or {}).get("period") or "")
    if task.kind == "analysis" and symbol and period:
        history_rows = await list_history_for_live_task(
            task_id=str(task.id),
            symbol=symbol.strip().upper(),
            period=period,
        )
        for index, history in enumerate(history_rows, start=1):
            if history.result_id and str(history.result_id) in seen_result_ids:
                continue
            if history.execution_id and str(history.execution_id) in seen_execution_ids:
                continue
            items.append(
                AnalysisExecutionListItem(
                    id=history.execution_id or history.analysis_id,
                    task_id=task.id,
                    sequence=-(index),
                    status=history.status,
                    created_at=history.created_at,
                    completed_at=history.updated_at,
                    result_id=str(history.result_id) if history.result_id else None,
                    analysis_id=history.analysis_id,
                    direction=history.direction,
                    symbol=history.symbol,
                    period=history.period,
                    source="history",
                )
            )
    items.sort(key=lambda item: item.created_at, reverse=True)
    total = len(items)
    for offset, item in enumerate(items):
        item.sequence = total - offset
    return items


@router.get("/analysis-results/{result_id}", response_model=AnalysisResultDetailPublic)
async def get_analysis_result(
    result_id: uuid.UUID,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> AnalysisResultDetailPublic:
    record = await repository.get_result(user.id, result_id)
    payload = await repository.get_result_payload(user.id, result_id)
    return AnalysisResultDetailPublic(
        id=record.id,
        task_id=record.task_id,
        execution_id=record.execution_id,
        mode=record.mode,
        symbol=record.symbol,
        period=record.period,
        direction=record.direction,
        terminal_outcome=record.terminal_outcome,
        status=record.status,
        favorite=record.favorite,
        notes=record.notes,
        tags=record.tags,
        created_at=record.created_at,
        updated_at=record.updated_at,
        result=payload,
    )


@router.get("/analysis-executions/{execution_id}", response_model=AnalysisExecutionPublic)
async def get_analysis_execution(execution_id: uuid.UUID, user: UserPublic = Depends(current_user), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository)) -> AnalysisExecutionPublic:
    return AnalysisExecutionPublic.model_validate(await repository.get_execution(user.id, execution_id))


@router.post("/analysis-executions/{execution_id}/cancel", response_model=AnalysisExecutionPublic)
async def cancel_analysis_execution(execution_id: uuid.UUID, user: UserPublic = Depends(current_user), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository), manager: AnalysisExecutionManager = Depends(get_analysis_execution_manager)) -> AnalysisExecutionPublic:
    execution = await repository.request_cancel(user.id, execution_id)
    await manager.cancel(execution_id)
    return AnalysisExecutionPublic.model_validate(execution)


@router.get("/analysis-executions/{execution_id}/events")
async def analysis_execution_events(execution_id: uuid.UUID, after_sequence: int = Query(0, ge=0), user: UserPublic = Depends(current_user), repository: AnalysisTaskRepository = Depends(get_analysis_task_repository)):
    from app.analysis.execution import events
    await repository.get_execution(user.id, execution_id)
    async def stream():
        cursor = after_sequence
        while True:
            batch = await events.list_events(execution_id, cursor)
            for event in batch:
                cursor = event.sequence
                yield json.dumps({"sequence": event.sequence, "type": event.type, "stage": event.stage, "message": event.message, "payload": event.payload, "terminal": event.terminal}, ensure_ascii=False) + "\n"
                if event.terminal:
                    return
            execution = await repository.get_execution(user.id, execution_id)
            if execution.status in {"completed", "completed_with_warnings", "degraded", "failed", "cancelled", "timed_out"} and not batch:
                return
            await asyncio.sleep(0.2)
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
