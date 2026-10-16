from datetime import UTC,datetime
from types import SimpleNamespace
import asyncio
import pytest
from sqlalchemy import text
from test_market_archive import archive_db,record


async def setup_collector(archive_db,monkeypatch,provider):
    archive,engine,_=archive_db
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.market import service
    # ORM public tables translated into the disposable archive schema.
    translated=engine.execution_options(schema_translate_map={None:archive.ARCHIVE_SCHEMA})
    factory=async_sessionmaker(translated,expire_on_commit=False)
    monkeypatch.setattr(service,'SessionFactory',factory)
    monkeypatch.setattr(service,'engine',translated)
    import uuid
    monkeypatch.setattr(service,'COLLECTOR_LOCK_ID',uuid.uuid4().int & ((1<<63)-1))
    async with translated.begin() as conn:
        await conn.run_sync(service.CollectionState.__table__.create)
        await conn.run_sync(service.ScheduledJobRun.__table__.create)
    async def no_schema():pass
    async def no_alerts(now):pass
    monkeypatch.setattr(service,'ensure_schema',no_schema)
    monkeypatch.setattr(service,'evaluate_alert_rules',no_alerts)
    settings=SimpleNamespace(collector_symbols=('MES',),collector_lookback_minutes=30,collector_max_catchup_minutes=360,collector_interval_seconds=60,hist_timeout_seconds=1,hist_max_retries=0)
    collector=service.MinuteCollector(settings,lambda:provider)
    return collector,factory,service


@pytest.mark.asyncio
async def test_collector_commits_empty_coverage_and_persisted_success(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    class Provider:
        async def get_archive_range(self,symbol,start,end):return ArchiveBatch([],end)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    now=datetime(2026,9,30,11,tzinfo=UTC)
    await collector.run_once(now)
    async with factory() as session:
        state=await session.get(service.CollectionState,'MES')
        runs=(await session.execute(service.select(service.ScheduledJobRun))).scalars().all()
    assert state.covered_until==now and state.status=='no_data'
    assert len(runs)==1 and runs[0].status=='success' and runs[0].completed_at
    await collector.run_once(now)
    async with factory() as session:
        assert len((await session.execute(service.select(service.ScheduledJobRun))).scalars().all())==1


@pytest.mark.asyncio
async def test_collector_data_and_cursor_rollback_on_invalid_write(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    from dataclasses import replace
    class Provider:
        async def get_archive_range(self,symbol,start,end):return ArchiveBatch([replace(record(),volume=-1)],end)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    await collector.run_once(datetime(2026,9,30,11,tzinfo=UTC))
    async with factory() as session:
        state=await session.get(service.CollectionState,'MES')
        runs=(await session.execute(service.select(service.ScheduledJobRun))).scalars().all()
    assert state.covered_until is None and state.status=='failed'
    assert runs[0].status=='failed'
    archive,engine,_=archive_db
    async with engine.connect() as conn:
        assert (await conn.execute(text(f"SELECT count(*) FROM {archive.table('bars')}"))).scalar_one()==0


@pytest.mark.asyncio
async def test_collector_unpublished_and_cancellation_do_not_advance(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    class Provider:
        async def get_archive_range(self,symbol,start,end):return ArchiveBatch([],None)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    await collector.run_once(datetime(2026,9,30,11,tzinfo=UTC))
    async with factory() as session:
        state=await session.get(service.CollectionState,'MES')
        assert state.covered_until is None and state.status=='waiting_for_provider'
    async def cancel(*args):raise asyncio.CancelledError()
    monkeypatch.setattr(collector.provider_factory(),'get_archive_range',cancel)
    with pytest.raises(asyncio.CancelledError):
        await collector.run_once(datetime(2026,9,30,11,1,tzinfo=UTC))
    async with factory() as session:
        runs=(await session.execute(service.select(service.ScheduledJobRun).order_by(service.ScheduledJobRun.scheduled_for))).scalars().all()
    assert runs[-1].status=='cancelled' and runs[-1].completed_at


@pytest.mark.asyncio
async def test_round_collects_all_five_targets_once_and_shares_trace(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    from app.core.logging_context import get_trace_fields
    from app.core.errors import ProviderError
    calls=[]
    alerts=[]
    class Provider:
        async def get_archive_range(self,symbol,start,end):
            calls.append((symbol,get_trace_fields()))
            if symbol=='ES':raise ProviderError('provider_auth_failed','rejected',502)
            return ArchiveBatch([],end)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    collector.settings.collector_symbols=('ES','NQ','MES','MNQ','MGC')
    async def alert(now):alerts.append(now)
    monkeypatch.setattr(service,'evaluate_alert_rules',alert)
    now=datetime(2026,9,30,11,tzinfo=UTC)
    await collector.run_once(now)
    await collector.run_once(now)
    assert [c[0] for c in calls]==['ES','NQ','MES','MNQ','MGC']
    assert len(alerts)==1
    assert len({c[1]['trace_id'] for c in calls})==1
    assert len({c[1]['run_id'] for c in calls})==5
    assert all(c[1]['trace_id']!='-' and c[1]['run_id']!='-' for c in calls)


@pytest.mark.asyncio
async def test_db_lease_skips_concurrent_worker_and_recovers_abandoned_run(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    class Provider:
        async def get_archive_range(self,symbol,start,end):return ArchiveBatch([],end)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    archive,engine,_=archive_db
    async with engine.begin() as lease:
        await lease.execute(text('SELECT pg_advisory_xact_lock(CAST(:lock_id AS bigint))'),{'lock_id':service.COLLECTOR_LOCK_ID})
        await collector.run_once(datetime(2026,9,30,11,tzinfo=UTC))
        async with factory() as session:
            assert (await session.execute(service.select(service.ScheduledJobRun))).scalars().all()==[]
    import uuid
    async with factory.begin() as session:
        session.add(service.ScheduledJobRun(id=uuid.uuid4(),job_key='market-minute:MES',scheduled_for=datetime(2026,9,30,10,tzinfo=UTC),status='running',started_at=datetime(2026,9,30,10,tzinfo=UTC)))
    await collector.run_once(datetime(2026,9,30,11,tzinfo=UTC))
    async with factory() as session:
        runs=(await session.execute(service.select(service.ScheduledJobRun).order_by(service.ScheduledJobRun.scheduled_for))).scalars().all()
    assert [run.status for run in runs]==['interrupted','success']
    assert all(run.completed_at for run in runs)


@pytest.mark.asyncio
async def test_snapshot_cursor_limits_catchup_and_timeout_preserves_it(archive_db,monkeypatch):
    from app.core.errors import ProviderError
    from app.market.archive import ArchiveBatch
    windows=[]
    class Provider:
        async def get_archive_range(self,symbol,start,end):
            windows.append((start,end))
            raise ProviderError('provider_timeout','timeout',504)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    archive,engine,_=archive_db
    async with engine.begin() as c:
        await c.execute(text(f"INSERT INTO {archive.table('metadata')} VALUES ('end_exclusive','2026-09-30T15:30:00Z')"))
    await collector.run_once(datetime(2026,10,1,12,tzinfo=UTC))
    assert windows==[(datetime(2026,9,30,15,30,tzinfo=UTC),datetime(2026,9,30,21,30,tzinfo=UTC))]
    async with factory() as session:
        state=await session.get(service.CollectionState,'MES')
        runs=(await session.execute(service.select(service.ScheduledJobRun))).scalars().all()
    assert state.covered_until is None and state.last_error=='provider_timeout'
    assert runs[0].status=='timed_out' and runs[0].completed_at


@pytest.mark.asyncio
async def test_preexisting_collection_watermark_catches_up_instead_of_skipping(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    windows=[]
    class Provider:
        async def get_archive_range(self,symbol,start,end):
            windows.append((start,end))
            return ArchiveBatch([],end)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    collector.settings.collector_symbols=('ES',)
    async with factory.begin() as session:
        session.add(service.CollectionState(symbol='ES',latest_closed_at=datetime(2026,9,29,10,tzinfo=UTC),consecutive_failures=0))
    await collector.run_once(datetime(2026,9,30,11,tzinfo=UTC))
    assert windows==[(datetime(2026,9,29,10,tzinfo=UTC),datetime(2026,9,29,16,tzinfo=UTC))]


@pytest.mark.asyncio
async def test_old_provisional_trade_replay_remains_deduplicated_during_outage(archive_db,monkeypatch):
    from app.market.archive import ArchiveBatch
    from app.market.live import LiveTrade,persist_live_trade
    class Provider:
        async def get_archive_range(self,symbol,start,end):return ArchiveBatch([],None)
    collector,factory,service=await setup_collector(archive_db,monkeypatch,Provider())
    monkeypatch.setattr('app.market.live.SessionFactory',factory)
    trade=LiveTrade(symbol='MES',timestamp=datetime(2026,9,28,10,50,tzinfo=UTC),price=100,size=2,event_key='replayed-old')
    await persist_live_trade(trade)
    await collector.run_once(datetime(2026,9,30,11,tzinfo=UTC))
    await persist_live_trade(trade)
    archive,_,_=archive_db
    bars=await archive.read_archive_bars('MES','1m',datetime(2026,9,28,10,tzinfo=UTC),datetime(2026,9,28,11,tzinfo=UTC))
    assert len(bars)==1 and bars[0].volume==2
