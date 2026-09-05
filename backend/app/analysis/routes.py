from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
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
)
from app.analysis.tasks.repository import AnalysisTaskRepository, RunCreateSpec
from app.analysis.execution.manager import DEFAULT_RUN_MANAGER, AnalysisRunManager
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


def get_analysis_run_manager() -> AnalysisRunManager:
    return DEFAULT_RUN_MANAGER


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
        version=task.version,
        created_at=task.created_at,
        updated_at=task.updated_at,
        archived_at=task.archived_at,
    )


class AnalysisTaskPagePublic(BaseModel):
    items: list[AnalysisTaskPublic]
    next_cursor: str | None


class AnalysisRunStartItem(BaseModel):
    run_id: uuid.UUID
    period: str
    status: RunStatus


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


@router.post("/analysis-tasks/{task_id}/runs", response_model=list[AnalysisRunStartItem], status_code=202)
async def start_analysis_task_run(
    task_id: uuid.UUID,
    user: UserPublic = Depends(limit_expensive),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
    manager: AnalysisRunManager = Depends(get_analysis_run_manager),
    provider=Depends(get_analysis_provider),
    session: AsyncSession = Depends(get_session),
) -> list[AnalysisRunStartItem]:
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
    if task.kind == "review":
        by_period: dict[str, list[dict[str, Any]]] = {}
        for item in snapshot.query_json.get("children", []):
            by_period.setdefault(str(item["period"]), []).append(item)
        specs = [
            RunCreateSpec(
                period=period,
                query_json={"kind": "review", "items": items},
                resolved_symbol="MULTI",
                bars_json=None,
                bars_hash=snapshot.bars_hash,
                mode="trade_review",
                symbol="MULTI",
            )
            for period, items in by_period.items()
        ]
    else:
        period = str(task.config_json["period"])
        specs = [
            RunCreateSpec(
                period=period,
                query_json={"snapshot_id": str(snapshot.id), "query": snapshot.query_json},
                resolved_symbol=snapshot.resolved_symbol,
                bars_json=snapshot.bars_json,
                bars_hash=snapshot.bars_hash,
                mode="historical",
                symbol=snapshot.resolved_symbol,
            )
        ]
    runs = await repository.create_runs_for_task(user.id, task_id, specs)
    trace_id = get_trace_id()
    for run in runs:
        manager.start(run.id, trace_id)
    return [AnalysisRunStartItem(run_id=run.id, period=run.period, status=run.status) for run in runs]


@router.get("/analysis-tasks/{task_id}/runs", response_model=list[AnalysisRunListItem])
async def list_analysis_task_runs(
    task_id: uuid.UUID,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> list[AnalysisRunListItem]:
    runs = await repository.list_runs(user.id, task_id)
    return [
        AnalysisRunListItem(
            run_id=run.id,
            task_id=run.task_id,
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


@router.get("/analysis-runs/{run_id}", response_model=AnalysisRunDetailPublic)
async def get_analysis_run(
    run_id: uuid.UUID,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
) -> AnalysisRunDetailPublic:
    run = await repository.get_run(user.id, run_id)
    payload = await repository.get_run_detail(user.id, run_id)
    return AnalysisRunDetailPublic(
        run_id=run.id,
        task_id=run.task_id,
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


@router.post("/analysis-runs/{run_id}/cancel", response_model=AnalysisRunPublic)
async def cancel_analysis_run(
    run_id: uuid.UUID,
    user: UserPublic = Depends(current_user),
    repository: AnalysisTaskRepository = Depends(get_analysis_task_repository),
    manager: AnalysisRunManager = Depends(get_analysis_run_manager),
) -> AnalysisRunPublic:
    run = await repository.request_run_cancel(user.id, run_id)
    await manager.cancel(run.id)
    return AnalysisRunPublic.model_validate(run)
