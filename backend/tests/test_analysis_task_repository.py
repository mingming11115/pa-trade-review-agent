from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.tasks.models import AnalysisTask, AnalysisTaskCreate, AnalysisTaskUpdate, AnalysisRun, RunStatus
from app.analysis.tasks.repository import AnalysisTaskRepository
from app.core.database import Base
from app.core.errors import AppError
from app.core.models import HistoricalQuery


UTC = timezone.utc


@pytest.fixture
async def repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = AnalysisTaskRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield repo
    finally:
        await engine.dispose()


def task_payload(title: str = "ES 区间分析") -> AnalysisTaskCreate:
    return AnalysisTaskCreate(
        kind="analysis",
        title=title,
        config={
            "symbol": "ES",
            "period": "5m",
        },
    )


@pytest.mark.anyio
async def test_models_expose_consolidated_run_shape() -> None:
    assert str(AnalysisRun.__table__.primary_key.columns[0].name) == "analysis_id"
    assert any(constraint.name == "ck_analysis_run_parent_child_shape" for constraint in AnalysisRun.__table__.constraints)
    stage_default = AnalysisRun.__table__.c.stage_runs_json.default.arg  # type: ignore[union-attr]
    assert stage_default(None) == []
    assert AnalysisTask.__table__.c.latest_analysis_id.type.length == 64


@pytest.mark.anyio
async def test_create_task_only_persists_pending_configuration(repository) -> None:
    owner = uuid.uuid4()

    task = await repository.create_task(owner, task_payload())

    assert task.status == "pending"
    assert task.config_json["symbol"] == "ES"
    assert task.analysis_symbol == "ES"
    assert task.analysis_period == "5m"
    assert task.latest_analysis_id is None


@pytest.mark.anyio
async def test_analysis_symbol_period_is_unique_per_owner(repository) -> None:
    owner = uuid.uuid4()
    await repository.create_task(owner, task_payload())

    with pytest.raises(AppError) as caught:
        await repository.create_task(owner, AnalysisTaskCreate(
            kind="analysis",
            title="重复 ES 5m",
            config={"symbol": " es ", "period": "5m"},
        ))

    assert caught.value.code == "analysis_task_symbol_period_conflict"
    assert "ES 5m" in caught.value.message

    other_period = await repository.create_task(owner, AnalysisTaskCreate(
        kind="analysis",
        title="ES 15m",
        config={"symbol": "ES", "period": "15m"},
    ))
    assert other_period.analysis_symbol == "ES"
    assert other_period.analysis_period == "15m"


@pytest.mark.anyio
async def test_ensure_live_analysis_task_reuses_symbol_period_slot(repository) -> None:
    owner = uuid.uuid4()
    first = await repository.ensure_live_analysis_task(owner, symbol="nq", period="1m")
    second = await repository.ensure_live_analysis_task(owner, symbol="NQ", period="1m")

    assert first.id == second.id
    assert first.analysis_symbol == "NQ"
    assert first.analysis_period == "1m"


@pytest.mark.anyio
async def test_owner_cannot_discover_another_users_task(repository) -> None:
    task = await repository.create_task(uuid.uuid4(), task_payload())

    with pytest.raises(AppError) as caught:
        await repository.get_task(uuid.uuid4(), task.id)

    assert caught.value.code == "analysis_task_not_found"
    assert caught.value.status_code == 404


@pytest.mark.anyio
async def test_local_owner_none_is_a_real_tenant(repository) -> None:
    local = await repository.create_task(None, task_payload("local"))
    remote = await repository.create_task(uuid.uuid4(), task_payload("remote"))

    page = await repository.list_tasks(None, limit=50)

    assert [item.id for item in page.items] == [local.id]
    assert remote.id not in [item.id for item in page.items]


@pytest.mark.anyio
async def test_run_creation_assigns_sequence_and_blocks_parallel_run(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())

    run = await repository.create_run(
        owner,
        task.id,
        {"query": {"symbol": "ES", "period": "5m", "analysis_mode": "historical"}},
        run_id=str(uuid.uuid4()),
        sequence=1,
    )

    assert run.sequence == 1
    assert run.status == RunStatus.queued.value
    assert (await repository.get_task(owner, task.id)).latest_analysis_id == run.analysis_id

    with pytest.raises(AppError) as caught:
        await repository.create_run(
            owner,
            task.id,
            {"query": {"symbol": "ES", "period": "5m", "analysis_mode": "historical"}},
            run_id=str(uuid.uuid4()),
            sequence=2,
        )

    assert caught.value.code == "analysis_already_running"


@pytest.mark.anyio
async def test_update_task_rejects_any_prior_run(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    await repository.create_run(
        owner,
        task.id,
        {"query": {"symbol": "ES", "period": "5m", "analysis_mode": "historical"}},
        run_id=str(uuid.uuid4()),
        sequence=1,
    )

    with pytest.raises(AppError) as caught:
        await repository.update_task(owner, task.id, AnalysisTaskUpdate(version=task.version + 1, title="不能修改", description="", config=task.config_json))

    assert caught.value.code == "analysis_task_not_editable"


@pytest.mark.anyio
async def test_run_stage_attempts_are_idempotent_by_stage_and_attempt(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    run = await repository.create_run(
        owner,
        task.id,
        {"query": {"symbol": "ES", "period": "5m", "analysis_mode": "historical"}},
        run_id=str(uuid.uuid4()),
        sequence=1,
    )

    await repository.upsert_stage_attempt(run.analysis_id, stage="stage1", attempt=1, status="response_received", raw_content="a")
    await repository.upsert_stage_attempt(run.analysis_id, stage="stage1", attempt=1, status="validated", normalized_output={"ok": True})

    stored = await repository.get_run(owner, run.analysis_id)
    assert len(stored.stage_runs_json) == 1
    assert stored.stage_runs_json[0]["status"] == "validated"


@pytest.mark.anyio
async def test_run_finish_updates_task_status_and_latest_analysis_id(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    run = await repository.create_run(
        owner,
        task.id,
        {"query": {"symbol": "ES", "period": "5m", "analysis_mode": "historical"}},
        run_id=str(uuid.uuid4()),
        sequence=1,
    )

    await repository.finish_run(owner, run.analysis_id, status=RunStatus.completed)

    stored_task = await repository.get_task(owner, task.id)
    assert stored_task.status == "completed"
    assert stored_task.latest_analysis_id == run.analysis_id


@pytest.mark.anyio
async def test_review_retry_selects_only_previous_unsuccessful_children(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, AnalysisTaskCreate(kind="review", title="复盘", config={"selected_trade_ids": ["trade-1"], "periods": ["5m", "1h"]}))
    parent = await repository.create_run(
        owner,
        task.id,
        {"kind": "review", "children": []},
        run_id=str(uuid.uuid4()),
        sequence=1,
        mode="trade_review",
        symbol="MULTI",
        period="multi",
    )
    children = await repository.create_review_children(parent, [
        {"key": "trade-1:5m", "trade_id": "trade-1", "period": "5m"},
        {"key": "trade-1:1h", "trade_id": "trade-1", "period": "1h"},
    ])
    await repository.update_review_child(children[0].analysis_id, status=RunStatus.completed, result={"ok": True})
    await repository.update_review_child(children[1].analysis_id, status=RunStatus.failed)
    await repository.finish_run(owner, parent.analysis_id, status=RunStatus.completed_with_warnings)

    retry_keys = await repository.review_retry_work_keys(owner, task.id)

    assert retry_keys == {"trade-1:1h"}


@pytest.mark.anyio
async def test_successful_review_results_picks_latest_success_per_work_key(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, AnalysisTaskCreate(kind="review", title="复盘", config={"selected_trade_ids": ["trade-1"], "periods": ["5m"]}))

    first = await repository.create_run(owner, task.id, {"kind": "review"}, run_id=str(uuid.uuid4()), sequence=1, mode="trade_review", symbol="MULTI", period="multi")
    first_child = await repository.create_review_children(first, [{"key": "trade-1:5m", "trade_id": "trade-1", "period": "5m"}])
    await repository.update_review_child(first_child[0].analysis_id, status=RunStatus.completed, result={"seq": 1})
    await repository.finish_run(owner, first.analysis_id, status=RunStatus.completed)

    second = await repository.create_run(owner, task.id, {"kind": "review"}, run_id=str(uuid.uuid4()), sequence=2, mode="trade_review", symbol="MULTI", period="multi")
    second_child = await repository.create_review_children(second, [{"key": "trade-1:5m", "trade_id": "trade-1", "period": "5m"}])
    await repository.update_review_child(second_child[0].analysis_id, status=RunStatus.completed, result={"seq": 2})
    await repository.finish_run(owner, second.analysis_id, status=RunStatus.completed)

    latest = await repository.successful_review_results(owner, task.id, before_sequence=3)

    assert latest == {"trade-1:5m": {"seq": 2}}
