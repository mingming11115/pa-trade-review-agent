"""Replay uses real archived rows without requiring an upstream credential."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError
from app.replay.market import ReplayMarket
from app.replay.contracts import ReplayCommand, SessionCreate
from test_market_archive import archive_db, record
from test_replay_postgres import service_for


async def seed(archive_db, monkeypatch, offsets=(0, 1, 2), start=None):
    import app.replay.market as module
    archive, _, factory = archive_db
    monkeypatch.setattr(module, 'SessionFactory', factory)
    base = record()
    start = start or base.timestamp_utc
    rows = [replace(base, timestamp_utc=start + timedelta(minutes=i),
                    ts_event_ns=int((start + timedelta(minutes=i)).timestamp()) * 1_000_000_000)
            for i in offsets]
    async with factory.begin() as db:
        await archive.write_archive_records(db, rows)
    return start


@pytest.mark.asyncio
async def test_history_reads_existing_rows_without_coverage_metadata_or_provider(archive_db, monkeypatch):
    import app.replay.market as module
    async def forbidden(*args, **kwargs):
        pytest.fail('Existing replay data must not request external backfill')
    monkeypatch.setattr(module, 'backfill_range', forbidden, raising=False)
    start = await seed(archive_db, monkeypatch)
    rows = await ReplayMarket().load('MES', start, start + timedelta(minutes=3))
    assert [row.timestamp for row in rows] == [start + timedelta(minutes=i) for i in range(3)]
    assert str(rows[0].open) == '100.000000001'
    assert await ReplayMarket().load('ES', start, start + timedelta(minutes=3)) == []


@pytest.mark.asyncio
async def test_contiguous_minutes_advance_without_coverage_metadata(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch)
    market = ReplayMarket()
    assert (await market.next_minute('MES', start)).timestamp == start
    rows = await market.load('MES', start, start + timedelta(minutes=3), require_complete=True)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_unknown_middle_gap_is_visible_in_history_but_blocks_matching(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch, offsets=(0, 2))
    market = ReplayMarket()
    assert len(await market.load('MES', start, start + timedelta(minutes=3))) == 2
    with pytest.raises(AppError, match='缺口'):
        await market.load('MES', start, start + timedelta(minutes=3), require_complete=True)
    with pytest.raises(AppError, match='缺口'):
        await market.next_minute('MES', start + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_coverage_proves_a_legitimate_minute_without_trades(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch, offsets=(0, 2))
    archive, _, factory = archive_db
    async with factory.begin() as db:
        await archive.write_archive_coverage(db, 'MES', start, start + timedelta(minutes=3))
    rows = await ReplayMarket().load('MES', start, start + timedelta(minutes=3), require_complete=True)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_only_missing_minutes_need_coverage_evidence(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch, offsets=(0, 2))
    archive, _, factory = archive_db
    async with factory.begin() as db:
        await archive.write_archive_coverage(db, 'MES', start + timedelta(minutes=1), start + timedelta(minutes=2))
    rows = await ReplayMarket().load('MES', start, start + timedelta(minutes=3), require_complete=True)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_coverage_and_regular_closure_together_prove_a_gap(archive_db, monkeypatch):
    # Coverage proves the last trading minute; the remainder is the daily break.
    start = datetime(2026, 9, 30, 20, 59, tzinfo=UTC)
    end = datetime(2026, 9, 30, 22, tzinfo=UTC)
    await seed(archive_db, monkeypatch, offsets=(0,), start=end)
    archive, _, factory = archive_db
    async with factory.begin() as db:
        await archive.write_archive_coverage(db, 'MES', start, start + timedelta(minutes=1))
    assert (await ReplayMarket().next_minute('MES', start)).timestamp == end


@pytest.mark.asyncio
@pytest.mark.parametrize('start,end', [
    (datetime(2026, 9, 25, 21, tzinfo=UTC), datetime(2026, 9, 27, 22, tzinfo=UTC)),
    (datetime(2026, 9, 30, 21, tzinfo=UTC), datetime(2026, 9, 30, 22, tzinfo=UTC)),
    (datetime(2026, 1, 9, 22, tzinfo=UTC), datetime(2026, 1, 11, 23, tzinfo=UTC)),
])
async def test_regular_cme_closure_can_advance_to_archived_reopening(archive_db, monkeypatch, start, end):
    await seed(archive_db, monkeypatch, offsets=(0,), start=end)
    assert (await ReplayMarket().next_minute('MES', start)).timestamp == end


@pytest.mark.asyncio
async def test_archive_end_does_not_request_more_data(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch)
    with pytest.raises(AppError, match='本地'):
        await ReplayMarket().next_minute('MES', start + timedelta(minutes=3))


@pytest.mark.asyncio
async def test_random_start_comes_from_selected_symbol_confirmed_archive(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch)
    archive, _, factory = archive_db
    base = record()
    async with factory.begin() as db:
        await archive.write_archive_records(db, [replace(base, timestamp_utc=start + timedelta(minutes=3),
            ts_event_ns=base.ts_event_ns + 180_000_000_000, confirmed=False)])
    for _ in range(5):
        chosen = await ReplayMarket().random_start('MES')
        assert chosen in {start + timedelta(minutes=1), start + timedelta(minutes=2)}
    with pytest.raises(AppError, match='本地'):
        await ReplayMarket().random_start('ES')


@pytest.mark.asyncio
async def test_selected_and_random_replay_start_and_advance_from_local_archive(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch)
    service = await service_for(archive_db)
    service.market = ReplayMarket()
    selected = await service.create('local', SessionCreate(mode='training', symbol='MES', start=start + timedelta(minutes=1)))
    assert datetime.fromisoformat(selected['state']['cursor']) == start + timedelta(minutes=1)
    assert selected['state']['last_price'] is not None
    stepped = await service.command('local', selected['id'], ReplayCommand(type='advance', period='1m',
        expected_revision=0, operation_id='local-next'))
    assert datetime.fromisoformat(stepped['state']['cursor']) == start + timedelta(minutes=2)
    with pytest.raises(AppError, match='缺口'):
        await service.command('local', selected['id'], ReplayCommand(type='advance', period='5m',
            expected_revision=1, operation_id='incomplete-bucket'))
    assert (await service.get('local', selected['id']))['state'] == stepped['state']
    chosen = await service.create('local', SessionCreate(mode='training', symbol='MES', random_start=True))
    assert datetime.fromisoformat(chosen['state']['cursor']) in {start + timedelta(minutes=1), start + timedelta(minutes=2)}


@pytest.mark.asyncio
async def test_random_start_needs_confirmed_history_and_excludes_future(archive_db, monkeypatch):
    future = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=1)
    await seed(archive_db, monkeypatch, offsets=(0, 1), start=future)
    with pytest.raises(AppError, match='本地'):
        await ReplayMarket().random_start('MES')


@pytest.mark.asyncio
async def test_unconfirmed_local_minute_cannot_be_matched_even_with_coverage(archive_db, monkeypatch):
    start = await seed(archive_db, monkeypatch)
    archive, _, factory = archive_db
    async with factory.begin() as db:
        from sqlalchemy import text
        await db.execute(text(f'UPDATE {archive.table("bars")} SET confirmed=false WHERE timestamp_utc=:t'), {'t': start})
        await archive.write_archive_coverage(db, 'MES', start, start + timedelta(minutes=3))
    with pytest.raises(AppError, match='未确认'):
        await ReplayMarket().load('MES', start, start + timedelta(minutes=3), require_complete=True)
