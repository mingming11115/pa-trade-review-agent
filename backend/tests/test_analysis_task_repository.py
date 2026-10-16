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
async def repository(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = AnalysisTaskRepository(async_sessionmaker(engine, expire_on_commit=False))
    from app.knowledge.repository import KnowledgeRepository
    from app.knowledge.service import KnowledgeService
    from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
    knowledge = KnowledgeService(KnowledgeRepository(repo.sessions))
    await knowledge.import_default_prompts(PROMPT_DIR)
    monkeypatch.setattr("app.analysis.execution.manager.get_or_create_run_prompt_snapshot", knowledge.get_or_create_run_prompt_snapshot)
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

@pytest.mark.anyio
async def test_terminal_run_cannot_restart_or_be_overwritten(repository) -> None:
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    await repository.mark_run_running(run.id)
    await repository.finish_run(None, run.id, status=RunStatus.completed)
    await repository.mark_run_running(run.id)
    await repository.finish_run(None, run.id, status=RunStatus.cancelled)
    assert (await repository.get_run(None, run.id)).status == 'completed'

@pytest.mark.anyio
async def test_queued_run_cannot_finish_successfully(repository) -> None:
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    with pytest.raises(AppError):
        await repository.finish_run(None, run.id, status=RunStatus.completed)
    assert (await repository.get_run(None, run.id)).status == 'queued'

@pytest.mark.anyio
@pytest.mark.parametrize('status', [RunStatus.degraded, RunStatus.completed_with_warnings])
async def test_warning_run_propagates_to_task(repository, status) -> None:
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    await repository.mark_run_running(run.id)
    await repository.finish_run(None, run.id, status=status)
    assert (await repository.get_task(None, task.id)).status == 'completed_with_warnings'

@pytest.mark.anyio
async def test_cancel_before_worker_starts_persists_terminal(repository) -> None:
    from app.analysis.execution.manager import AnalysisRunManager
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    manager = AnalysisRunManager(repository)
    manager.start(run.id, 'cancel-early')
    await repository.request_run_cancel(None, run.id)
    await manager.cancel(run.id)
    assert (await repository.get_run(None, run.id)).status == 'cancelled'

@pytest.mark.anyio
async def test_other_manager_cancel_stops_running_work(repository, monkeypatch) -> None:
    import asyncio
    from app.analysis.execution.manager import AnalysisRunManager
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    async with repository.sessions() as session:
        stored = await session.get(AnalysisRun, run.id)
        stored.query_json = {'kind': 'review', 'items': [{'query': {}, 'bars': []}]}
        await session.commit()
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    worker = AnalysisRunManager(repository)
    monkeypatch.setattr('app.analysis.execution.manager.HistoricalQuery.model_validate', lambda value: value)
    monkeypatch.setattr('app.analysis.execution.manager.run_demo_analysis_workflow', blocked)
    worker.start(run.id, 'remote-cancel')
    pending = worker.tasks[run.id]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await repository.request_run_cancel(None, run.id)
        await AnalysisRunManager(repository).cancel(run.id)
        await asyncio.wait_for(stopped.wait(), 2)
        await asyncio.gather(pending, return_exceptions=True)
        assert (await repository.get_run(None, run.id)).status == 'cancelled'
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

@pytest.mark.anyio
async def test_completed_result_cannot_be_replaced(repository) -> None:
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    await repository.mark_run_running(run.id)
    await repository.update_run_result(run.id, {'answer': 'original'})
    await repository.finish_run(None, run.id, status=RunStatus.completed)
    await repository.update_run_result(run.id, {'answer': 'replacement'})
    assert (await repository.get_run(None, run.id)).result_json == {'answer': 'original'}

@pytest.mark.anyio
async def test_only_one_worker_can_claim_run(repository) -> None:
    import asyncio
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    results = await asyncio.gather(repository.mark_run_running(run.id), repository.mark_run_running(run.id))
    assert sorted(results) == [False, True]

@pytest.mark.anyio
async def test_cancel_and_complete_race_always_settles_terminal(repository) -> None:
    import asyncio
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    await repository.mark_run_running(run.id)
    await asyncio.gather(repository.request_run_cancel(None, run.id), repository.finish_run(None, run.id, status=RunStatus.completed))
    assert (await repository.get_run(None, run.id)).status in {'completed', 'cancelled'}

@pytest.mark.anyio
async def test_cancellation_during_initial_read_persists_terminal(repository, monkeypatch) -> None:
    import asyncio
    from app.analysis.execution.manager import AnalysisRunManager
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    original = repository.get_run_unscoped
    first = True
    async def interrupted_read(run_id):
        nonlocal first
        if first:
            first = False
            raise asyncio.CancelledError
        return await original(run_id)
    monkeypatch.setattr(repository, 'get_run_unscoped', interrupted_read)
    with pytest.raises(asyncio.CancelledError):
        await AnalysisRunManager(repository).run(run.id, 'cancel-read')
    assert (await repository.get_run(None, run.id)).status == 'cancelled'

@pytest.mark.anyio
async def test_finish_rejects_nonterminal_target(repository) -> None:
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    with pytest.raises(AppError):
        await repository.finish_run(None, run.id, status=RunStatus.running)
    stored = await repository.get_run(None, run.id)
    assert stored.status == 'queued'
    assert stored.completed_at is None

@pytest.mark.anyio
async def test_cancel_during_result_persistence_settles_without_interrupting_write(repository, monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from app.analysis.execution.manager import AnalysisRunManager
    task = await repository.create_task(None, task_payload())
    run = (await repository.create_runs_for_task(None, task.id, [run_spec('5m')]))[0]
    async with repository.sessions() as session:
        stored = await session.get(AnalysisRun, run.id)
        stored.query_json = {'kind': 'review', 'items': [{'query': {}, 'bars': []}]}
        await session.commit()
    entered, release, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = repository.update_run_result
    async def slow_write(run_id, payload):
        entered.set()
        try:
            await release.wait()
            return await original(run_id, payload)
        except asyncio.CancelledError:
            interrupted.set()
            raise
    async def workflow(*args, **kwargs):
        return SimpleNamespace(model_dump=lambda **kwargs: {'review_result': []})
    monkeypatch.setattr(repository, 'update_run_result', slow_write)
    monkeypatch.setattr('app.analysis.execution.manager.HistoricalQuery.model_validate', lambda value: value)
    monkeypatch.setattr('app.analysis.execution.manager.run_demo_analysis_workflow', workflow)
    manager = AnalysisRunManager(repository)
    manager.start(run.id, 'persist-cancel')
    pending = manager.tasks[run.id]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await repository.request_run_cancel(None, run.id)
        await AnalysisRunManager(repository).cancel(run.id)
        # Allow at least one cancellation polling cycle during the held write.
        await asyncio.sleep(0.35)
        assert not interrupted.is_set()
        release.set()
        await asyncio.wait_for(pending, 2)
        assert (await repository.get_run(None, run.id)).status == 'cancelled'
        assert (await repository.get_task(None, task.id)).status == 'cancelled'
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
