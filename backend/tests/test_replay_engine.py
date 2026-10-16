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


def test_profit_limit_alert_latches_after_net_equity_target_is_reached():
    s = state(orders=[ReplayOrder(id='entry', side='buy', quantity=1, created_at=T)])
    s.config.profit_target = D('15')
    s.config.fee_per_side = D('1')
    first = match_minute(s, minute('100', '102', '100', '102'), I).state
    assert first.account.equity - first.config.initial_cash == D('19')
    assert first.account.profit_target_reached
    assert not first.account.loss_limit_reached

    later = minute('102', '102', '99', '99').model_copy(update={'timestamp': first.cursor})
    restored = EngineState.model_validate(match_minute(first, later, I).state.model_dump(mode='json'))
    assert restored.account.equity - restored.config.initial_cash == D('-11')
    assert restored.account.profit_target_reached


def test_loss_limit_alert_uses_equity_and_does_not_change_active_trading_state():
    s = state(orders=[ReplayOrder(id='entry', side='buy', quantity=1, created_at=T)])
    s.config.loss_limit = D('10')
    result = match_minute(s, minute('100', '100', '98', '98'), I)
    assert result.state.account.equity - result.state.config.initial_cash == D('-20')
    assert result.state.account.loss_limit_reached
    assert result.state.orders[0].status == 'filled'


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


def test_all_opening_orders_execute_before_new_position_protection():
    s=state(orders=[ReplayOrder(id='a',side='buy',quantity=1,stop_loss='99',created_at=T),
                    ReplayOrder(id='b',side='buy',quantity=1,created_at=T)])
    result=match_minute(s,minute('100','102','98','100'),I)
    assert [(f.reason,f.quantity) for f in result.fills]==[('entry',1),('entry',1),('stop_loss',2)]


def test_short_stop_gap_and_fee_signs():
    s=state(orders=[ReplayOrder(id='short',side='sell',quantity=2,stop_loss='103',take_profit='97',created_at=T)])
    s.config.slippage_ticks=1
    s.config.fee_per_side=D('1')
    s=match_minute(s,minute('100','102','99','101'),I).state
    assert s.position.average_price==D('99.75')
    gap=minute('104','105','96','103').model_copy(update={'timestamp':s.cursor})
    result=match_minute(s,gap,I)
    assert result.fills[0].price==D('104.25')
    assert result.state.account.cash==D('99906')
    assert result.state.position.quantity==0


def test_stop_market_gap_and_sell_mirror():
    for side, stop, opening, expected in [('buy','101','103','103.25'),('sell','99','97','96.75')]:
        s=state(orders=[ReplayOrder(id='stop',side=side,quantity=1,kind='stop_market',stop_price=stop,created_at=T)])
        s.config.slippage_ticks=1
        result=match_minute(s,minute(opening,'104','96',opening),I)
        assert result.fills[0].price==D(expected)


def test_stop_limit_trigger_persists_and_does_not_use_pretrigger_low():
    s=state(orders=[ReplayOrder(id='stop',side='buy',quantity=1,kind='stop_limit',stop_price='101',limit_price='100',created_at=T)])
    result=match_minute(s,minute('99','102','98','101'),I)
    assert result.fills==[]
    restored=EngineState.model_validate(result.state.model_dump(mode='json'))
    assert restored.orders[0].triggered_at is not None
    later=minute('99','100','98','99').model_copy(update={'timestamp':restored.cursor})
    assert match_minute(restored,later,I).fills[0].price==D('99')


def test_stop_limit_gap_never_fills_worse_than_limit():
    s=state(orders=[ReplayOrder(id='stop',side='buy',quantity=1,kind='stop_limit',stop_price='101',limit_price='102',created_at=T)])
    assert match_minute(s,minute('103','104','103','103'),I).fills==[]
    assert match_minute(s,minute('103','104','101','102'),I).fills[0].price==D('102')
