from datetime import UTC, datetime, timedelta
import asyncio
import json
import uuid
from dataclasses import replace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import get_settings


@pytest_asyncio.fixture
async def archive_db(monkeypatch):
    from app.market import archive
    name = "test_archive_" + uuid.uuid4().hex
    monkeypatch.setattr(archive, "ARCHIVE_SCHEMA", name)
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(archive, "SessionFactory", factory)
    async with engine.begin() as conn:
        await archive.ensure_archive_schema(conn)
    try:
        yield archive, engine, factory
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        await engine.dispose()


def record():
    from app.market.archive import ArchiveRecord
    return ArchiveRecord(root="MES", continuous_symbol="MES.c.0", instrument_id=123,
        ts_event_ns=1790765400000000000, timestamp_utc=datetime(2026,9,30,10,50,tzinfo=UTC),
        open_raw=100000000001, high_raw=102000000001, low_raw=99000000001,
        close_raw=101000000001, volume=7, publisher_id=1)


@pytest.mark.asyncio
async def test_archive_idempotent_preserves_exact_raw_and_micro_symbol(archive_db):
    archive, engine, factory = archive_db
    async with factory.begin() as session:
        await archive.write_archive_records(session, [record()])
        await archive.write_archive_records(session, [record()])
    async with engine.connect() as conn:
        row = (await conn.execute(text(f"SELECT count(*) AS n, min(open_raw) AS o FROM {archive.ARCHIVE_SCHEMA}.bars"))).one()
    assert tuple(row) == (1, 100000000001)
    bars = await archive.read_archive_bars("MES", "1m", datetime(2026,9,30,10,tzinfo=UTC), datetime(2026,9,30,11,tzinfo=UTC))
    assert len(bars) == 1
    assert bars[0].volume == 7
    assert await archive.read_archive_bars("ES", "1m", datetime(2026,9,30,10,tzinfo=UTC), datetime(2026,9,30,11,tzinfo=UTC)) == []


@pytest.mark.asyncio
async def test_archive_prefers_confirmed_real_contract_without_adding_volumes(archive_db):
    archive, _, factory = archive_db
    async with factory.begin() as session:
        await archive.write_archive_records(session, [replace(record(), instrument_id=0, source="legacy:provider", volume=900), record()])
    bars = await archive.read_archive_bars("MES", "1m", datetime(2026,9,30,10,tzinfo=UTC), datetime(2026,9,30,11,tzinfo=UTC))
    assert len(bars) == 1 and bars[0].volume == 7


@pytest.mark.asyncio
async def test_archive_write_rollback_does_not_leave_rows(archive_db):
    archive, engine, factory = archive_db
    with pytest.raises(RuntimeError):
        async with factory.begin() as session:
            await archive.write_archive_records(session, [record()])
            raise RuntimeError("interrupted")
    async with engine.connect() as conn:
        assert (await conn.execute(text(f"SELECT count(*) FROM {archive.ARCHIVE_SCHEMA}.bars"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_legacy_hour_is_preserved_when_minutes_cannot_reconstruct_it(archive_db):
    archive,engine,factory=archive_db
    async with engine.begin() as c:
        await c.execute(text(f"CREATE TABLE {archive.table('legacy_bars_backup')} (symbol text,period text,opened_at timestamptz,open float,high float,low float,close float,volume float)"))
        await c.execute(text(f"INSERT INTO {archive.table('legacy_bars_backup')} VALUES ('MES','1h','2026-09-30 10:00Z',110,112,109,111,60)"))
    async with factory.begin() as session:
        await archive.write_archive_records(session,[record()])
    bars=await archive.read_archive_bars('MES','1h',datetime(2026,9,30,10,tzinfo=UTC),datetime(2026,9,30,11,tzinfo=UTC))
    assert len(bars)==1 and bars[0].close==111 and bars[0].volume==60


@pytest.mark.asyncio
async def test_normal_hour_query_includes_backup_only_history(archive_db):
    archive,engine,_=archive_db
    async with engine.begin() as c:
        await c.execute(text(f"CREATE TABLE {archive.table('legacy_bars_backup')} (symbol text,period text,opened_at timestamptz,open float,high float,low float,close float,volume float)"))
        await c.execute(text(f"INSERT INTO {archive.table('legacy_bars_backup')} VALUES ('ES','1h','2026-09-29 10:00Z',110,112,109,111,60)"))
    from app.market.service import query_market_range
    result=await query_market_range('ES','1h',datetime(2026,9,29,10,tzinfo=UTC),datetime(2026,9,29,11,tzinfo=UTC),now=datetime(2026,9,30,11,tzinfo=UTC))
    assert len(result.bars)==1 and result.bars[0].close==111 and result.bars[0].volume==60


@pytest.mark.asyncio
async def test_initial_coverage_uses_migrated_minutes_without_inventing_contract(archive_db):
    archive,_,factory=archive_db
    async with factory.begin() as session:
        await archive.write_archive_records(session,[replace(record(),root='ES',instrument_id=0,source='legacy:provider')])
        covered=await archive.initial_coverage(session,'ES')
    assert covered==datetime(2026,9,30,10,51,tzinfo=UTC)


async def write_metadata(archive, factory, values: dict[str, str]) -> None:
    async with factory.begin() as session:
        await session.execute(
            text(f"INSERT INTO {archive.table('metadata')} (key,value) VALUES (:key,:value)"),
            [{"key": key, "value": value} for key, value in values.items()],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["MES", "MNQ", "MGC"])
async def test_completed_legacy_archive_replays_empty_closed_session_without_upstream(archive_db, monkeypatch, symbol):
    from app.core.models import HistoricalQuery, Period
    from app.market.service import LocalFirstMarketProvider

    archive, _, factory = archive_db
    await write_metadata(archive, factory, {
        "start": "2023-01-01T00:00:00Z", "end_exclusive": "2026-09-30T15:30:00Z", "status": "complete",
    })

    async def no_schema() -> None:
        pass

    class UpstreamMustNotRun:
        async def get_archive_range(self, *args):
            pytest.fail("A proven closed session must not be downloaded again")

    monkeypatch.setattr("app.market.service.ensure_schema", no_schema)
    query = HistoricalQuery(symbol=symbol, period=Period.minute_1,
        start=datetime(2026, 8, 8, 10, tzinfo=UTC), end=datetime(2026, 8, 8, 11, tzinfo=UTC))
    assert await LocalFirstMarketProvider(UpstreamMustNotRun()).get_range(query) == []


@pytest.mark.asyncio
async def test_symbol_backfill_coverage_skips_sparse_local_history(archive_db, monkeypatch):
    from app.core.models import HistoricalQuery, Period
    from app.market.service import LocalFirstMarketProvider

    archive, _, factory = archive_db
    await write_metadata(archive, factory, {"backfill_2023:ES": json.dumps({
        "start": "2023-01-01T00:00:00Z", "covered_until": "2026-09-30T15:30:00Z",
        "target": "2026-10-01T00:00:00Z", "run": "running",
    })})
    async with factory.begin() as session:
        await archive.write_archive_records(session, [replace(record(), root="ES")])

    async def no_schema() -> None:
        pass

    class UpstreamMustNotRun:
        async def get_archive_range(self, *args):
            pytest.fail("The committed backfill prefix must be served locally")

    monkeypatch.setattr("app.market.service.ensure_schema", no_schema)
    query = HistoricalQuery(symbol="ES", period=Period.minute_1,
        start=datetime(2026, 9, 30, 10, tzinfo=UTC), end=datetime(2026, 9, 30, 11, tzinfo=UTC))
    bars = await LocalFirstMarketProvider(UpstreamMustNotRun()).get_range(query)
    assert [bar.close for bar in bars] == [101.000000001]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,start,end,metadata", [
    ("ES", "2026-08-08T10:00:00Z", "2026-08-08T11:00:00Z", {
        "start": "2023-01-01T00:00:00Z", "end_exclusive": "2026-09-30T15:30:00Z", "status": "complete"}),
    ("MES", "2022-08-08T10:00:00Z", "2022-08-08T11:00:00Z", {
        "start": "2023-01-01T00:00:00Z", "end_exclusive": "2026-09-30T15:30:00Z", "status": "complete"}),
    ("MES", "2026-08-08T10:00:00Z", "2026-08-08T11:00:00Z", {
        "start": "2023-01-01T00:00:00Z", "end_exclusive": "2026-09-30T15:30:00Z", "status": "running"}),
    ("ES", "2026-09-30T16:00:00Z", "2026-09-30T17:00:00Z", {
        "backfill_2023:ES": json.dumps({"start": "2023-01-01T00:00:00Z", "covered_until": "2026-09-30T15:30:00Z", "target": "2026-10-01T00:00:00Z"})}),
])
async def test_archive_coverage_does_not_invent_coverage(archive_db, symbol, start, end, metadata):
    archive, _, factory = archive_db
    await write_metadata(archive, factory, metadata)
    assert not await archive.archive_range_is_covered(symbol, datetime.fromisoformat(start), datetime.fromisoformat(end))


@pytest.mark.asyncio
async def test_latest_bar_does_not_prove_earlier_history(archive_db):
    archive, _, factory = archive_db
    async with factory.begin() as session:
        await archive.write_archive_records(session, [record()])
    assert not await archive.archive_range_is_covered("MES", datetime(2026, 9, 29, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC))


@pytest.mark.asyncio
async def test_coverage_ranges_merge_adjacent_windows_but_preserve_holes(archive_db):
    archive, _, factory = archive_db
    stamps = [datetime(2026, 9, 30, hour, tzinfo=UTC) for hour in range(6)]
    async with factory.begin() as session:
        await archive.write_archive_coverage(session, "ES", stamps[0], stamps[1])
        await archive.write_archive_coverage(session, "ES", stamps[1], stamps[2])
        await archive.write_archive_coverage(session, "ES", stamps[3], stamps[4])
    assert await archive.archive_range_is_covered("ES", stamps[0], stamps[2])
    assert not await archive.archive_range_is_covered("ES", stamps[0], stamps[4])
    assert not await archive.archive_range_is_covered("MES", stamps[0], stamps[2])


@pytest.mark.asyncio
async def test_coverage_rollback_does_not_leave_proof_without_bars(archive_db):
    archive, _, factory = archive_db
    start, end = datetime(2026, 9, 30, 10, tzinfo=UTC), datetime(2026, 9, 30, 11, tzinfo=UTC)
    with pytest.raises(RuntimeError):
        async with factory.begin() as session:
            await archive.write_archive_records(session, [record()])
            await archive.write_archive_coverage(session, "MES", start, end)
            raise RuntimeError("interrupted")
    assert not await archive.archive_range_is_covered("MES", start, end)
    assert await archive.read_archive_bars("MES", "1m", start, end) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [True, False])
async def test_backfill_commits_only_published_coverage_even_without_trades(archive_db, monkeypatch, published):
    from app.core.models import HistoricalQuery, Period
    from app.market.service import backfill_range

    archive, _, factory = archive_db
    start, end = datetime(2026, 8, 8, 10, tzinfo=UTC), datetime(2026, 8, 8, 11, tzinfo=UTC)

    class EmptyProvider:
        async def get_archive_range(self, *args):
            return archive.ArchiveBatch([], end if published else None)

    monkeypatch.setattr("app.market.service.SessionFactory", factory)
    query = HistoricalQuery(symbol="ES", period=Period.minute_1, start=start, end=end)
    await backfill_range(EmptyProvider(), query)
    assert await archive.archive_range_is_covered("ES", start, end) is published


@pytest.mark.asyncio
async def test_backfill_total_timeout_cancels_upstream_and_preserves_missing_coverage(archive_db, monkeypatch):
    from app.core.errors import ProviderError
    from app.core.models import HistoricalQuery, Period
    from app.market import service

    archive, _, factory = archive_db
    cancelled = asyncio.Event()

    class SlowProvider:
        async def get_archive_range(self, *args):
            try:
                await asyncio.sleep(1)
                return archive.ArchiveBatch([], None)
            finally:
                cancelled.set()

    monkeypatch.setattr(service, "HISTORICAL_BACKFILL_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(service, "SessionFactory", factory)
    start, end = datetime(2026, 8, 8, 10, tzinfo=UTC), datetime(2026, 8, 8, 11, tzinfo=UTC)
    query = HistoricalQuery(symbol="ES", period=Period.minute_1, start=start, end=end)
    with pytest.raises(ProviderError) as exc:
        await service.backfill_range(SlowProvider(), query)
    assert exc.value.code == "provider_timeout"
    assert cancelled.is_set()
    assert not await archive.archive_range_is_covered("ES", start, end)


@pytest.mark.asyncio
async def test_failed_backfill_can_retry_and_reuse_successful_empty_coverage(archive_db, monkeypatch):
    from app.core.errors import ProviderError
    from app.core.models import HistoricalQuery, Period
    from app.market import service

    archive, _, factory = archive_db
    start, end = datetime(2026, 8, 8, 10, tzinfo=UTC), datetime(2026, 8, 8, 11, tzinfo=UTC)
    calls = []

    class InitiallyUnavailableProvider:
        async def get_archive_range(self, symbol, requested_start, requested_end):
            calls.append((symbol, requested_start, requested_end))
            if len(calls) == 1:
                raise ProviderError("provider_unavailable", "Temporary failure", 502)
            if len(calls) > 2:
                pytest.fail("A successful empty range must be reused on retry")
            return archive.ArchiveBatch([], end)

    async def no_schema() -> None:
        pass

    monkeypatch.setattr(service, "SessionFactory", factory)
    monkeypatch.setattr(service, "ensure_schema", no_schema)
    query = HistoricalQuery(symbol="ES", period=Period.minute_1, start=start, end=end)
    provider = service.LocalFirstMarketProvider(InitiallyUnavailableProvider())
    with pytest.raises(ProviderError):
        await provider.get_range(query)
    assert not await archive.archive_range_is_covered("ES", start, end)
    assert await provider.get_range(query) == []
    assert await provider.get_range(query) == []
    assert calls == [("ES", start, end), ("ES", start, end)]


@pytest.mark.asyncio
async def test_coverage_unions_legacy_symbol_and_request_ranges(archive_db):
    archive, _, factory = archive_db
    await write_metadata(archive, factory, {
        "start": "2023-01-01T00:00:00Z", "end_exclusive": "2026-09-30T15:30:00Z", "status": "complete",
        "backfill_2023:MES": json.dumps({"start": "2026-09-30T15:30:00Z", "covered_until": "2026-09-30T16:00:00Z"}),
    })
    async with factory.begin() as session:
        await archive.write_archive_coverage(session, "MES", datetime(2026, 9, 30, 16, tzinfo=UTC), datetime(2026, 9, 30, 17, tzinfo=UTC))
    assert await archive.archive_range_is_covered("MES", datetime(2023, 1, 1, tzinfo=UTC), datetime(2026, 9, 30, 17, tzinfo=UTC))
    assert not await archive.archive_range_is_covered("MES", datetime(2023, 1, 1, tzinfo=UTC), datetime(2026, 9, 30, 17, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_coverage_compacts_ranges_without_claiming_evicted_gaps(archive_db, monkeypatch):
    archive, _, factory = archive_db
    monkeypatch.setattr(archive, "MAX_COVERAGE_RANGES", 2, raising=False)
    start = datetime(2026, 9, 30, tzinfo=UTC)
    async with factory.begin() as session:
        for hour in (0, 2, 4):
            await archive.write_archive_coverage(session, "ES", start + timedelta(hours=hour), start + timedelta(hours=hour + 1))
    assert not await archive.archive_range_is_covered("ES", start, start + timedelta(hours=1))
    assert await archive.archive_range_is_covered("ES", start + timedelta(hours=2), start + timedelta(hours=3))
    assert await archive.archive_range_is_covered("ES", start + timedelta(hours=4), start + timedelta(hours=5))
    assert not await archive.archive_range_is_covered("ES", start + timedelta(hours=2), start + timedelta(hours=5))


@pytest.mark.asyncio
async def test_partial_published_backfill_does_not_mark_unpublished_suffix(archive_db, monkeypatch):
    from app.core.models import HistoricalQuery, Period
    from app.market import service

    archive, _, factory = archive_db
    start, end = datetime(2026, 8, 8, 10, tzinfo=UTC), datetime(2026, 8, 8, 11, tzinfo=UTC)
    midpoint = datetime(2026, 8, 8, 10, 30, tzinfo=UTC)

    class PartialProvider:
        async def get_archive_range(self, *args):
            return archive.ArchiveBatch([], midpoint)

    monkeypatch.setattr(service, "SessionFactory", factory)
    result = await service.backfill_range(PartialProvider(), HistoricalQuery(symbol="ES", period=Period.minute_1, start=start, end=end))
    assert not result.coverage.complete
    assert await archive.archive_range_is_covered("ES", start, midpoint)
    assert not await archive.archive_range_is_covered("ES", start, end)


@pytest.mark.asyncio
@pytest.mark.parametrize("period,source_minute", [("1m", 30), ("15m", 30), ("1h", 0), ("4h", 0), ("1d", 0)])
async def test_unaligned_backfill_covers_source_bucket_and_is_reused(archive_db, monkeypatch, period, source_minute):
    from app.core.models import HistoricalQuery, Period
    from app.market import service

    archive, _, factory = archive_db
    start = datetime(2026, 8, 8, 10, 30, 15, tzinfo=UTC)
    end = datetime(2026, 8, 9, 11, tzinfo=UTC)
    source_start = datetime(2026, 8, 8, 10, source_minute, tzinfo=UTC)
    calls = []

    class EmptyPublishedProvider:
        async def get_archive_range(self, symbol, requested_start, requested_end):
            calls.append((symbol, requested_start, requested_end))
            return archive.ArchiveBatch([], end)

    async def no_schema() -> None:
        pass

    monkeypatch.setattr(service, "SessionFactory", factory)
    monkeypatch.setattr(service, "ensure_schema", no_schema)
    query = HistoricalQuery(symbol="ES", period=Period(period), start=start, end=end)
    provider = service.LocalFirstMarketProvider(EmptyPublishedProvider())
    assert await provider.get_range(query) == []
    assert await archive.archive_range_is_covered("ES", source_start, end)
    assert await provider.get_range(query) == []
    assert calls == [("ES", source_start, end)]


@pytest.mark.asyncio
async def test_backfill_commit_after_bar_snapshot_does_not_make_empty_result_complete(archive_db, monkeypatch):
    from app.market import service

    archive, _, factory = archive_db
    start = datetime(2026, 9, 30, 10, tzinfo=UTC)
    end = datetime(2026, 9, 30, 11, tzinfo=UTC)
    original_read = service.read_stored_bars
    committed = False

    async def read_then_complete_concurrent_backfill(symbol, period, requested_start, requested_end):
        nonlocal committed
        snapshot = await original_read(symbol, period, requested_start, requested_end)
        if not committed:
            async with factory.begin() as session:
                await archive.write_archive_records(session, [record()])
                await archive.write_archive_coverage(session, "MES", start, end)
            committed = True
        return snapshot

    monkeypatch.setattr(service, "read_stored_bars", read_then_complete_concurrent_backfill)
    result = await service.query_market_range("MES", "1m", start, end, now=end)
    assert result.bars == []
    assert not result.coverage.complete
    assert result.coverage.missing_buckets

    refreshed = await service.query_market_range("MES", "1m", start, end, now=end)
    assert [bar.close for bar in refreshed.bars] == [101.000000001]
    assert refreshed.coverage.complete
