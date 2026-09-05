from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.tasks.models import AnalysisRun, AnalysisTaskCreate, AnalysisTaskUpdate, RunStatus
from app.analysis.tasks.repository import AnalysisTaskRepository, RunCreateSpec
from app.core.database import Base
from app.core.errors import AppError


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
    return AnalysisTaskCreate(kind="analysis", title=title, config={"symbol": "ES", "period": "5m"})


def run_spec(period: str, *, trades: list[dict] | None = None) -> RunCreateSpec:
    return RunCreateSpec(
        period=period,
        query_json={"query": {"symbol": "ES", "period": period}, "trades": trades or []},
        resolved_symbol="ES",
        bars_json=None,
        bars_hash=None,
        mode="trade_review" if trades else "historical",
        symbol="ES",
    )


@pytest.mark.anyio
async def test_create_task_only_persists_pending_configuration(repository) -> None:
    task = await repository.create_task(uuid.uuid4(), task_payload())
    assert task.status == "pending"
    assert task.config_json == {"symbol": "ES", "period": "5m"}


@pytest.mark.anyio
async def test_analysis_symbol_period_is_unique_per_owner(repository) -> None:
    owner = uuid.uuid4()
    await repository.create_task(owner, task_payload())
    with pytest.raises(AppError) as caught:
        await repository.create_task(owner, task_payload("重复"))
    assert caught.value.code == "analysis_task_symbol_period_conflict"


@pytest.mark.anyio
async def test_owner_cannot_discover_another_users_task(repository) -> None:
    task = await repository.create_task(uuid.uuid4(), task_payload())
    with pytest.raises(AppError) as caught:
        await repository.get_task(uuid.uuid4(), task.id)
    assert caught.value.code == "analysis_task_not_found"


@pytest.mark.anyio
async def test_create_runs_for_normal_task_is_one_shot(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    runs = await repository.create_runs_for_task(owner, task.id, [run_spec("5m")])
    assert len(runs) == 1
    assert runs[0].period == "5m"
    assert runs[0].status == RunStatus.queued.value
    assert (await repository.get_task(owner, task.id)).status == "running"

    with pytest.raises(AppError) as caught:
        await repository.create_runs_for_task(owner, task.id, [run_spec("5m")])
    assert caught.value.code == "analysis_task_already_executed"
    assert len(await repository.list_runs(owner, task.id)) == 1


@pytest.mark.anyio
async def test_review_task_creates_one_flat_run_per_period_with_all_trades(repository) -> None:
    owner = uuid.uuid4()
    trades = [{"id": "trade-1"}, {"id": "trade-2"}]
    task = await repository.create_task(
        owner,
        AnalysisTaskCreate(
            kind="review",
            title="复盘",
            config={"selected_trade_ids": ["trade-1", "trade-2"], "periods": ["5m", "1h"]},
        ),
    )
    runs = await repository.create_runs_for_task(
        owner, task.id, [run_spec("5m", trades=trades), run_spec("1h", trades=trades)]
    )
    assert {run.period for run in runs} == {"5m", "1h"}
    assert all(run.query_json["trades"] == trades for run in runs)
    assert all(not hasattr(run, "parent_run_id") for run in runs)


@pytest.mark.anyio
async def test_create_runs_rejects_duplicate_periods(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    with pytest.raises(AppError) as caught:
        await repository.create_runs_for_task(owner, task.id, [run_spec("5m"), run_spec("5m")])
    assert caught.value.code == "analysis_run_period_duplicate"


@pytest.mark.anyio
async def test_update_task_rejects_any_prior_run(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    await repository.create_runs_for_task(owner, task.id, [run_spec("5m")])
    with pytest.raises(AppError) as caught:
        await repository.update_task(
            owner,
            task.id,
            AnalysisTaskUpdate(version=task.version + 1, title="不能修改", config=task.config_json),
        )
    assert caught.value.code == "analysis_task_not_editable"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["completed", "completed"], "completed"),
        (["completed", "failed"], "completed_with_warnings"),
        (["failed", "failed"], "failed"),
        (["cancelled", "cancelled"], "cancelled"),
        (["timed_out", "timed_out"], "timed_out"),
    ],
)
async def test_aggregate_task_status(repository, statuses, expected) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(
        owner,
        AnalysisTaskCreate(kind="review", title=str(uuid.uuid4()), config={"selected_trade_ids": ["t"], "periods": ["5m", "1h"]}),
    )
    runs = await repository.create_runs_for_task(owner, task.id, [run_spec("5m"), run_spec("1h")])
    async with repository.sessions() as session:
        stored = list((await session.scalars(select(AnalysisRun).where(AnalysisRun.task_id == task.id))).all())
        for run, status in zip(stored, statuses, strict=True):
            run.status = status
        await session.commit()
    assert (await repository.aggregate_task_status(task.id)).value == expected
    assert (await repository.get_task(owner, task.id)).status == expected


@pytest.mark.anyio
async def test_run_stage_attempts_are_idempotent(repository) -> None:
    owner = uuid.uuid4()
    task = await repository.create_task(owner, task_payload())
    run = (await repository.create_runs_for_task(owner, task.id, [run_spec("5m")]))[0]
    await repository.upsert_stage_attempt(run.id, stage="stage1", attempt=1, status="response_received")
    await repository.upsert_stage_attempt(run.id, stage="stage1", attempt=1, status="validated")
    attempts = await repository.list_stage_attempts(run.id)
    assert [(item.stage, item.attempt, item.status) for item in attempts] == [("stage1", 1, "validated")]
