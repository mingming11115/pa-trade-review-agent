from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.replay.contracts import EngineState, MinuteSnapshot, ReplayOrder, TrainingConfig, InstrumentSpec
from app.replay.engine import match_minute

T = datetime(2025, 1, 1, tzinfo=UTC)
I = InstrumentSpec(symbol='TEST', tick_size='0.25', multiplier='10')


def minute(o='100', h='102', l='99', c='101'):
    return MinuteSnapshot(timestamp=T, open=o, high=h, low=l, close=c)


def state(**kwargs):
    return EngineState(cursor=T, config=TrainingConfig(), **kwargs)


def test_market_next_open_and_slippage():
    s = state(orders=[ReplayOrder(id='a', side='buy', quantity=2, created_at=T)])
    s.config.slippage_ticks = 1
    result = match_minute(s, minute(), I)
    assert result.fills[0].price == D('100.25')
    assert result.state.position.quantity == 2
    assert s.position.quantity == 0
    assert match_minute(result.state, minute(), I).fills == []


def test_limit_improvement_and_wait():
    s = state(orders=[ReplayOrder(id='a', side='buy', quantity=1, kind='limit', limit_price='100', created_at=T)])
    assert match_minute(s, minute('99'), I).fills[0].price == D('99')
    assert match_minute(s, minute('102', '103', '101', '102'), I).fills == []


def test_double_touch_stop_and_gap():
    s = state()
    s.position.quantity = 1
    s.position.average_price = D('100')
    s.position.stop_loss = D('99')
    s.position.take_profit = D('103')
    result = match_minute(s, minute('101', '104', '98', '102'), I)
    assert result.fills[0].price == D('99')
    assert result.fills[0].ambiguous
    assert result.state.position.quantity == 0
    assert match_minute(s, minute('98', '104', '97', '102'), I).fills[0].price == D('98')


def test_intrabar_entry_does_not_assume_take_profit_after_entry():
    s = state(orders=[ReplayOrder(id='a', side='buy', quantity=1, kind='limit', limit_price='100', take_profit='103', created_at=T)])
    result = match_minute(s, minute('102', '104', '99', '101'), I)
    assert len(result.fills) == 1
    assert result.state.position.quantity == 1


def test_partial_close_accounts_fees_and_no_reverse():
    s = state(orders=[ReplayOrder(id='a', side='buy', quantity=2, created_at=T)])
    s.config.fee_per_side = D('1')
    s = match_minute(s, minute(), I).state
    s.orders.append(ReplayOrder(id='b', side='sell', quantity=2, reduce_only=True, created_at=s.cursor))
    m = minute('102', '103', '101', '102').model_copy(update={'timestamp': T + timedelta(minutes=1)})
    result = match_minute(s, m, I)
    assert result.state.account.cash == D('100036')
    assert result.state.account.wins == 1
    assert result.state.account.fees == D('4')
    assert result.state.position.quantity == 0


def test_fractional_average_survives_persistence():
    s = state(orders=[ReplayOrder(id='a', side='buy', quantity=2, created_at=T)])
    s = match_minute(s, minute(), I).state
    s.orders.append(ReplayOrder(id='b', side='buy', quantity=1, created_at=s.cursor))
    m = minute('101','102','100','101').model_copy(update={'timestamp':s.cursor})
    result = match_minute(s,m,I)
    restored = EngineState.model_validate(result.state.model_dump(mode='json'))
    assert restored.position.quantity==3
    assert restored.position.average_price > D('100.333333333')
