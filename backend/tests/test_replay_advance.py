from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.replay.contracts import MinuteSnapshot, ReplayCommand, SessionCreate
from app.replay.models import ReplayEvent
from test_replay_repository import T, replay


def minute(offset: int, **values: object) -> MinuteSnapshot:
    return MinuteSnapshot.model_validate(dict(timestamp=T + timedelta(minutes=offset),
        open=100, high=102, low=99, close=101, instrument_id=1, **values))


class Archive:
    """A covered archive; absent rows represent minutes without trades."""

    def __init__(self, rows: list[MinuteSnapshot]) -> None:
        self.rows = rows
        self.reads: list[tuple[datetime, datetime]] = []
        self.uncovered = False

    async def next_minute(self, symbol: str, cursor: datetime) -> MinuteSnapshot:
        following = [row for row in self.rows if row.timestamp >= cursor]
        if not following:
            raise AppError('replay_no_market', '无后续行情', 409)
        return min(following, key=lambda row: row.timestamp)

    async def load(self, symbol: str, start: datetime, end: datetime, *, require_complete: bool = False) -> list[MinuteSnapshot]:
        self.reads.append((start, end))
        if self.uncovered:
            raise AppError('replay_market_gap', '行情覆盖尚未完整', 409)
        return [row for row in self.rows if start <= row.timestamp < end]


@pytest.mark.anyio
@pytest.mark.parametrize('period,start,offsets,want_cursor', [
    (None, 0, [0, 1, 2, 3, 4], '2025-01-01T00:01:00Z'),
    ('1m', 0, [0, 1], '2025-01-01T00:01:00Z'),
    ('5m', 0, [0, 1, 2, 3, 4, 5], '2025-01-01T00:05:00Z'),
    ('5m', 2, [2, 3, 4, 5], '2025-01-01T00:05:00Z'),
    ('5m', 5, [5, 6, 7, 8, 9, 10], '2025-01-01T00:10:00Z'),
    ('1h', 45, [45, 46, 59, 60], '2025-01-01T01:00:00Z'),
    ('1d', 1438, [1438, 1439, 1440], '2025-01-02T00:00:00Z'),
    ('5m', 1, [1, 2], '2025-01-01T00:05:00Z'),
    ('5m', 0, [4321, 4322, 4325], '2025-01-04T00:05:00Z'),
])
async def test_advance_completes_one_visible_bucket(replay, period, start, offsets, want_cursor):
    created = await replay.create('local', SessionCreate(mode='training', start=T + timedelta(minutes=start)))
    replay.market = Archive([minute(offset) for offset in offsets])
    fields = dict(period=period) if period else {}
    command = ReplayCommand(type='advance', operation_id='next', expected_revision=0, **fields)
    result = await replay.command('local', created['id'], command)
    assert result['state']['cursor'] == want_cursor
    assert result['revision'] == 1
    events = await replay.repository.events('local', created['id'], 200, 0)
    completed = datetime.fromisoformat(want_cursor)
    used = [row for row in replay.market.rows if row.timestamp < completed]
    assert events['items'][0]['minutes'] == [row.model_dump(mode='json') for row in used]
    assert events['items'][0]['minute'] == used[-1].model_dump(mode='json')
    # One covered range query suffices regardless of the number of minutes.
    assert len(replay.market.reads) <= 1


@pytest.mark.anyio
async def test_next_bar_matches_intrabar_protection_and_keeps_peak_drawdown(replay):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    placed = await replay.command('local', created['id'], ReplayCommand(type='place_order',
        operation_id='buy', expected_revision=0, stop_loss='97'))
    replay.market = Archive([
        minute(0).model_copy(update={'close': Decimal('105'), 'high': Decimal('106')}),
        minute(1).model_copy(update={'open': Decimal('105'), 'high': Decimal('105'), 'low': Decimal('103'), 'close': Decimal('104')}),
        minute(2).model_copy(update={'open': Decimal('104'), 'low': Decimal('96'), 'close': Decimal('98')}),
        minute(3), minute(4),
    ])
    result = await replay.command('local', created['id'], ReplayCommand(type='advance',
        period='5m', operation_id='next', expected_revision=placed['revision']))
    assert result['state']['cursor'] == '2025-01-01T00:05:00Z'
    assert result['state']['position']['quantity'] == 0
    assert Decimal(result['state']['account']['realized_pnl']) == Decimal('-200')
    assert Decimal(result['state']['account']['max_drawdown']) == Decimal('400')
    events = await replay.repository.events('local', created['id'], 200, 0)
    fills = [fill for event in events['items'] for fill in event['fills']]
    assert [(fill['timestamp'], fill['reason'], fill['price']) for fill in fills] == [
        ('2025-01-01T00:00:00Z', 'entry', '101.00'),
        ('2025-01-01T00:03:00Z', 'stop_loss', '97.00'),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['unconfirmed', 'rollover', 'rollover_after_unknown', 'uncovered'])
async def test_invalid_middle_minute_rolls_back_entire_bar(replay, failure):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    before = await replay.command('local', created['id'], ReplayCommand(type='place_order',
        operation_id='buy', expected_revision=0))
    rows = [minute(offset) for offset in range(5)]
    if failure == 'unconfirmed':
        rows[2] = rows[2].model_copy(update={'confirmed': False})
    elif failure.startswith('rollover'):
        rows[2:] = [row.model_copy(update={'instrument_id': 2}) for row in rows[2:]]
        if failure == 'rollover_after_unknown':
            rows[1] = rows[1].model_copy(update={'instrument_id': 0})
    replay.market = Archive(rows)
    replay.market.uncovered = failure == 'uncovered'
    with pytest.raises(AppError) as error:
        await replay.command('local', created['id'], ReplayCommand(type='advance', period='5m',
            operation_id='next', expected_revision=1))
    assert error.value.code == ('replay_rollover' if failure.startswith('rollover') else 'replay_market_gap')
    after = await replay.get('local', created['id'])
    assert after['state'] == before['state']
    assert after['revision'] == before['revision']
    events = await replay.repository.events('local', created['id'], 200, 0)
    assert sum(len(event['fills']) for event in events['items'] if event['type']=='place_order') == 1
    assert all(event['fills'] == [] for event in events['items'] if event['type']!='place_order')


@pytest.mark.anyio
async def test_next_bar_stops_at_session_end_and_rejects_later_minutes(replay):
    created = await replay.create('local', SessionCreate(mode='training', start=T, end=T + timedelta(minutes=3)))
    replay.market = Archive([minute(offset) for offset in range(6)])
    result = await replay.command('local', created['id'], ReplayCommand(type='advance', period='5m',
        operation_id='next', expected_revision=0))
    assert result['state']['cursor'] == '2025-01-01T00:03:00Z'
    assert replay.market.reads == [(T, T + timedelta(minutes=3))]
    with pytest.raises(AppError, match='结束'):
        await replay.command('local', created['id'], ReplayCommand(type='advance', period='5m',
            operation_id='after-end', expected_revision=1))
    assert (await replay.get('local', created['id']))['revision'] == 1


@pytest.mark.anyio
async def test_next_bar_cannot_jump_over_end_to_next_session(replay):
    created = await replay.create('local', SessionCreate(mode='training', start=T, end=T + timedelta(minutes=3)))
    replay.market = Archive([minute(10)])
    with pytest.raises(AppError, match='结束'):
        await replay.command('local', created['id'], ReplayCommand(type='advance', period='5m',
            operation_id='after-end', expected_revision=0))
    assert (await replay.get('local', created['id']))['state'] == created['state']


@pytest.mark.anyio
async def test_next_bar_never_reveals_current_unfinished_minute(replay, monkeypatch):
    import app.replay.service as module

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 1, 1, 0, 3, 30, tzinfo=UTC)

    created = await replay.create('local', SessionCreate(mode='training', start=T))
    replay.market = Archive([minute(offset) for offset in range(6)])
    monkeypatch.setattr(module, 'datetime', Clock)
    result = await replay.command('local', created['id'], ReplayCommand(type='advance', period='5m',
        operation_id='next', expected_revision=0))
    assert result['state']['cursor'] == '2025-01-01T00:03:00Z'
    assert replay.market.reads == [(T, T + timedelta(minutes=3))]
    with pytest.raises(AppError):
        await replay.command('local', created['id'], ReplayCommand(type='advance', period='5m',
            operation_id='future', expected_revision=1))
    assert (await replay.get('local', created['id']))['revision'] == 1


@pytest.mark.anyio
async def test_period_is_part_of_idempotent_command(replay):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    replay.market = Archive([minute(offset) for offset in range(10)])
    command = ReplayCommand(type='advance', period='5m', operation_id='next', expected_revision=0)
    result = await replay.command('local', created['id'], command)
    assert await replay.command('local', created['id'], command) == result
    with pytest.raises(AppError) as error:
        await replay.command('local', created['id'], command.model_copy(update={'period': '1m'}))
    assert error.value.code == 'replay_operation_conflict'
    assert (await replay.get('local', created['id']))['revision'] == 1


@pytest.mark.anyio
@pytest.mark.parametrize('command_type', ['advance', 'place_order'])
async def test_legacy_event_without_period_can_be_retried_in_both_repository_paths(replay, command_type):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    command = ReplayCommand(type=command_type, operation_id='legacy', expected_revision=0)
    result = await replay.command('local', created['id'], command)
    async with replay.repository.factory.begin() as db:
        event = await db.scalar(select(ReplayEvent).where(ReplayEvent.session_id == created['id'], ReplayEvent.operation_id == 'legacy'))
        event.payload = {key: value for key, value in event.payload.items() if key != 'period'}
    assert await replay.command('local', created['id'], command) == result

    def unexpected_change(current):
        pytest.fail('An idempotent retry must not execute the command again')

    assert await replay.repository.transact('local', created['id'], command, unexpected_change) == result
