from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError
from app.replay.contracts import MinuteSnapshot, SessionCreate
from app.replay.service import ReplayService
from test_replay_repository import replay


WEEK_OPEN = datetime(2026, 9, 27, 22, tzinfo=UTC)
BEIJING_MONDAY_0605 = WEEK_OPEN + timedelta(minutes=5)


def minutes(start: datetime, count: int) -> list[MinuteSnapshot]:
    """Distinct minute OHLC values expose a truncated five-minute candle."""
    return [MinuteSnapshot(
        timestamp=start + timedelta(minutes=offset),
        open=100 + offset % 5, high=102 + offset % 5,
        low=99 + offset % 5, close=101 + offset % 5,
        volume=1, instrument_id=1,
    ) for offset in range(count)]


def weekend_rows(opening_minutes: int = 5) -> list[MinuteSnapshot]:
    return (
        minutes(datetime(2026, 9, 23, 22, tzinfo=UTC), 23 * 60)
        + minutes(datetime(2026, 9, 24, 22, tzinfo=UTC), 23 * 60)
        + minutes(WEEK_OPEN, opening_minutes)
    )


class Archive:
    """Covered minute archive; missing rows are legitimate closed sessions."""

    def __init__(self, rows: list[MinuteSnapshot], fail_on_read: int | None = None) -> None:
        self.rows = rows
        self.reads: list[tuple[datetime, datetime]] = []
        self.fail_on_read = fail_on_read

    async def load(self, symbol: str, start: datetime, end: datetime) -> list[MinuteSnapshot]:
        assert symbol == 'ES'
        self.reads.append((start, end))
        if len(self.reads) == self.fail_on_read:
            raise AppError('provider_timeout', '历史行情读取超时', 504)
        return [row for row in self.rows if start <= row.timestamp < end]


def assert_bounded_reads(archive: Archive, cursor: datetime) -> None:
    assert 1 <= len(archive.reads) <= 20
    assert all(cursor - timedelta(days=30) <= start < end <= cursor
               for start, end in archive.reads)
    # Older reads adjoin the last range: no duplicate backfills or gaps.
    assert all(older_end == newer_start
               for (newer_start, _), (_, older_end) in zip(archive.reads, archive.reads[1:]))


@pytest.mark.anyio
@pytest.mark.parametrize(('limit', 'first_timestamp'), [
    (120, '2026-09-25T11:05:00+00:00'),
    (500, '2026-09-24T02:25:00+00:00'),
])
async def test_chart_history_fills_requested_candles_across_weekend(
    replay: ReplayService, limit: int, first_timestamp: str,
) -> None:
    created = await replay.create('local', SessionCreate(mode='training', start=BEIJING_MONDAY_0605))
    archive = Archive(weekend_rows())
    replay.market = archive
    before = await replay.get('local', created['id'])

    result = await replay.bars('local', created['id'], '5m', None, limit)

    assert len(result['bars']) == limit
    assert result['bars'][0]['timestamp'] == first_timestamp
    assert result['bars'][-1]['timestamp'] == '2026-09-27T22:00:00+00:00'
    assert result['next_before'] == first_timestamp
    assert result['cursor'] == BEIJING_MONDAY_0605.isoformat()
    assert await replay.get('local', created['id']) == before
    assert_bounded_reads(archive, BEIJING_MONDAY_0605)


@pytest.mark.anyio
async def test_weekend_history_keeps_oldest_candle_complete_for_partial_current_bar(
    replay: ReplayService,
) -> None:
    cursor = WEEK_OPEN + timedelta(minutes=7)
    created = await replay.create('local', SessionCreate(mode='training', start=cursor))
    replay.market = Archive(weekend_rows(opening_minutes=7))

    result = await replay.bars('local', created['id'], '5m', None, 120)

    assert len(result['bars']) == 120
    assert result['bars'][0] == dict(
        timestamp='2026-09-25T11:10:00+00:00', open=100, high=106, low=99,
        close=105, volume=5, is_partial=False,
    )
    assert result['bars'][-1] == dict(
        timestamp='2026-09-27T22:05:00+00:00', open=100, high=103, low=99,
        close=102, volume=2, is_partial=True,
    )


@pytest.mark.anyio
async def test_history_observer_excludes_unconfirmed_and_unfinished_minutes(
    replay: ReplayService,
) -> None:
    tip = WEEK_OPEN + timedelta(minutes=10)
    observer = WEEK_OPEN + timedelta(minutes=7, seconds=30)
    created = await replay.create('local', SessionCreate(mode='training', start=tip))
    rows = weekend_rows(opening_minutes=10)
    # 22:06 is not confirmed; 22:07 is not yet complete at the observer cursor.
    rows[-4] = rows[-4].model_copy(update={'confirmed': False})
    rows += [minutes(datetime(2026, 9, 26, 12, tzinfo=UTC), 1)[0].model_copy(
        update={'confirmed': False},
    )]
    archive = Archive(rows)
    replay.market = archive
    before = await replay.get('local', created['id'])

    result = await replay.bars('local', created['id'], '5m', observer, 120)

    assert len(result['bars']) == 120
    assert result['bars'][-1] == dict(
        timestamp='2026-09-27T22:05:00+00:00', open=100, high=102, low=99,
        close=101, volume=1, is_partial=True,
    )
    assert all(not bar['timestamp'].startswith('2026-09-26') for bar in result['bars'])
    assert result['cursor'] == observer.isoformat()
    assert await replay.get('local', created['id']) == before
    assert_bounded_reads(archive, observer)


@pytest.mark.anyio
async def test_sufficient_continuous_history_uses_one_read_and_clamps_to_session_tip(
    replay: ReplayService,
) -> None:
    tip = datetime(2026, 9, 25, 20, tzinfo=UTC)
    created = await replay.create('local', SessionCreate(mode='training', start=tip))
    archive = Archive(minutes(tip - timedelta(hours=11), 12 * 60))
    replay.market = archive

    result = await replay.bars('local', created['id'], '5m', tip + timedelta(hours=1), 120)

    assert len(result['bars']) == 120
    assert result['bars'][0]['timestamp'] == '2026-09-25T10:00:00+00:00'
    assert result['bars'][-1]['timestamp'] == '2026-09-25T19:55:00+00:00'
    assert result['cursor'] == tip.isoformat()
    assert len(archive.reads) == 1
    assert_bounded_reads(archive, tip)


@pytest.mark.anyio
async def test_empty_chart_history_searches_bounded_thirty_days_then_returns_empty(
    replay: ReplayService,
) -> None:
    created = await replay.create('local', SessionCreate(mode='training', start=BEIJING_MONDAY_0605))
    archive = Archive([])
    replay.market = archive
    before = await replay.get('local', created['id'])

    result = await replay.bars('local', created['id'], '5m', None, 120)

    assert result['bars'] == []
    assert result['next_before'] is None
    assert min(start for start, _ in archive.reads) == BEIJING_MONDAY_0605 - timedelta(days=30)
    assert_bounded_reads(archive, BEIJING_MONDAY_0605)
    assert await replay.get('local', created['id']) == before


@pytest.mark.anyio
async def test_thirty_day_boundary_does_not_return_a_truncated_oldest_daily_candle(
    replay: ReplayService,
) -> None:
    cursor = WEEK_OPEN + timedelta(minutes=7)
    created = await replay.create('local', SessionCreate(mode='training', start=cursor))
    archive = Archive(
        minutes(datetime(2026, 8, 20, tzinfo=UTC), 1)
        + minutes(datetime(2026, 8, 28, 23, tzinfo=UTC), 1)
        + minutes(datetime(2026, 8, 29, tzinfo=UTC), 5)
    )
    replay.market = archive

    result = await replay.bars('local', created['id'], '1d', None, 120)

    assert result['bars'] == [dict(
        timestamp='2026-08-29T00:00:00+00:00', open=100, high=106, low=99,
        close=105, volume=5, is_partial=False,
    )]
    assert len(archive.reads) == 1
    assert_bounded_reads(archive, cursor)


@pytest.mark.anyio
async def test_older_history_failure_propagates_instead_of_returning_one_candle(
    replay: ReplayService,
) -> None:
    created = await replay.create('local', SessionCreate(mode='training', start=BEIJING_MONDAY_0605))
    archive = Archive(weekend_rows(), fail_on_read=2)
    replay.market = archive
    before = await replay.get('local', created['id'])

    with pytest.raises(AppError) as error:
        await replay.bars('local', created['id'], '5m', None, 120)

    assert error.value.code == 'provider_timeout'
    assert await replay.get('local', created['id']) == before
