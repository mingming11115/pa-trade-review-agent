from datetime import datetime, timezone
from decimal import Decimal
import pytest
from app.core.models import Bar
from app.market.trading_hours import aggregate_session_bars, in_trading_hours
from app.replay.contracts import EngineState, TrainingConfig
from app.replay.engine import can_market_trade


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize('day,opened', [('2026-01-05', '14:30'), ('2026-07-06', '13:30')])
def test_rth_open_is_local_and_excludes_night(day, opened):
    start = dt(f'{day}T{opened}:00')
    assert in_trading_hours(start, 'ES', 'RTH')
    assert not in_trading_hours(start.replace(minute=29), 'ES', 'RTH')
    assert in_trading_hours(start.replace(minute=29), 'ES', 'ETH')


def test_rth_filters_before_aggregation_and_keeps_open_anchor():
    bars = [Bar(timestamp=dt('2026-07-06T13:29:00'), open=100, high=999, low=1, close=100),
            Bar(timestamp=dt('2026-07-06T13:30:00'), open=110, high=120, low=105, close=115)]
    result = aggregate_session_bars(bars, '1h', 'ES', 'RTH')
    assert len(result) == 1
    assert result[0].timestamp == dt('2026-07-06T13:30:00')
    assert result[0].high == 120


def test_rth_quote_requires_an_eligible_source_minute():
    state = EngineState(cursor=dt('2026-07-06T13:30:00'), config=TrainingConfig(trading_hours='RTH'),
                        last_price=Decimal('100'), last_price_at=dt('2026-07-06T13:29:00'))
    assert not can_market_trade(state, 'ES')
    state.last_price_at = dt('2026-07-06T13:30:00')
    assert can_market_trade(state, 'ES')


def test_night_minutes_do_not_match_orders_or_revalue_positions():
    from app.replay.contracts import MinuteSnapshot, ReplayOrder
    from app.replay.engine import match_minute
    from app.replay.instruments import get_instrument
    state = EngineState(cursor=dt('2026-07-06T20:15:00'), config=TrainingConfig(trading_hours='RTH'),
                        last_price=Decimal('100'), last_price_at=dt('2026-07-06T20:14:00'))
    state.orders.append(ReplayOrder(id='night', side='buy', kind='limit', quantity=1,
                                   limit_price=Decimal('101'), created_at=state.cursor))
    minute = MinuteSnapshot(timestamp=dt('2026-07-06T22:00:00'), open=100, high=120, low=80, close=110)
    result = match_minute(state, minute, get_instrument('ES'))
    assert result.fills == []
    assert result.state == state


@pytest.mark.asyncio
async def test_rth_next_minute_checks_empty_selected_windows_before_skipping(monkeypatch):
    from app.core.errors import AppError
    from app.replay.market import ReplayMarket
    from app.replay.contracts import MinuteSnapshot
    market = ReplayMarket()
    first = dt('2026-07-06T13:30:00')
    row = MinuteSnapshot(timestamp=dt('2026-07-06T13:31:00'), open=100, high=101, low=99, close=100)
    checks = []
    async def load(symbol, start, end):
        return [row] if start <= row.timestamp < end else []
    async def complete(symbol, start, end, rows):
        checks.append((start, end))
        if start == first and end > start:
            raise AppError('replay_market_gap', 'unproven day-session gap', 409)
    monkeypatch.setattr(market, 'load', load)
    monkeypatch.setattr(market, '_assert_complete', complete)
    with pytest.raises(AppError, match='unproven'):
        await market.next_minute('ES', first, trading_hours='RTH')
    assert checks == [(first, dt('2026-07-06T13:31:00'))]


@pytest.mark.asyncio
async def test_rth_next_minute_skips_weekend_without_querying_night(monkeypatch):
    from app.replay.market import ReplayMarket
    from app.replay.contracts import MinuteSnapshot
    market = ReplayMarket()
    row = MinuteSnapshot(timestamp=dt('2026-07-06T13:30:00'), open=100, high=101, low=99, close=100)
    reads = []
    async def load(symbol, start, end):
        reads.append((start, end))
        return [row]
    async def complete(*args):
        pass
    monkeypatch.setattr(market, 'load', load)
    monkeypatch.setattr(market, '_assert_complete', complete)
    result = await market.next_minute('ES', dt('2026-07-03T20:15:00'), trading_hours='RTH')
    assert result.timestamp == row.timestamp
    assert reads == [(row.timestamp, dt('2026-07-06T13:31:00'))]


from test_replay_repository import replay


@pytest.mark.anyio
async def test_rth_configuration_and_quote_survive_save_and_resume(replay):
    from app.replay.contracts import SessionCreate, ReplayCommand, MinuteSnapshot
    from app.replay.saved import SavedReplays
    class Market:
        async def load(self, symbol, start, end, **kwargs):
            row = MinuteSnapshot(timestamp=dt('2026-07-06T13:30:00'), open=100, high=101, low=99, close=100)
            return [row] if start <= row.timestamp < end else []
        async def next_minute(self, symbol, cursor, **kwargs):
            return MinuteSnapshot(timestamp=cursor, open=100, high=101, low=99, close=100)
    replay.market = Market()
    created = await replay.create('local', SessionCreate(mode='training', start=dt('2026-07-06T13:31:00'),
                                                       config=TrainingConfig(trading_hours='RTH')))
    switched = await replay.command('local', created['id'], ReplayCommand(
        type='set_trading_hours', trading_hours='ETH', operation_id='hours-eth', expected_revision=0))
    restored = await replay.command('local', created['id'], ReplayCommand(
        type='set_trading_hours', trading_hours='RTH', operation_id='hours-rth', expected_revision=1))
    assert switched['state']['cursor'] == restored['state']['cursor'] == created['state']['cursor']
    assert restored['state']['last_price_at'] == created['state']['last_price_at']
    saved = SavedReplays(replay.repository)
    version = await saved.save('local', created['id'], 'RTH restoration', 'save-rth', 2)
    resumed = await saved.resume('local', version['id'], 'resume-rth')
    assert resumed['state']['config']['trading_hours'] == 'RTH'
    assert resumed['state']['last_price_at'] == created['state']['last_price_at']
    assert resumed['can_market_trade'] is True
