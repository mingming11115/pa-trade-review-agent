from datetime import timedelta
from decimal import Decimal

from app.market.trading_hours import in_trading_hours
from app.replay.accounting import apply_fill, mark_to_market
from app.replay.contracts import EngineState, EngineResult, InstrumentSpec, MinuteSnapshot, ReplayFill


def can_market_trade(state: EngineState, symbol: str) -> bool:
    """A revealed quote is usable only in the selected trading window."""
    if state.last_price is None:
        return False
    if state.config.trading_hours == 'ETH':
        return True
    return state.last_price_at is not None and in_trading_hours(state.last_price_at, symbol, 'RTH')


def match_minute(state: EngineState, minute: MinuteSnapshot, instrument: InstrumentSpec) -> EngineResult:
    s = state.model_copy(deep=True)
    result = EngineResult(state=s)
    if (minute.timestamp < s.cursor or not minute.confirmed
            or not in_trading_hours(minute.timestamp, instrument.symbol, s.config.trading_hours)):
        return result
    slip = instrument.tick_size * s.config.slippage_ticks

    def fill(order_id: str, side: str, quantity: int, price: Decimal, reason: str, ambiguous: bool = False) -> None:
        item = ReplayFill(order_id=order_id, side=side, quantity=quantity, price=price,
                          timestamp=minute.timestamp + timedelta(minutes=1),
                          bar_timestamp=minute.timestamp,
                          fee=s.config.fee_per_side * quantity, reason=reason, ambiguous=ambiguous)
        apply_fill(s, item, instrument)
        result.fills.append(item)

    def protect(allow_profit: bool = True, use_open: bool = True) -> bool:
        p = s.position
        if not p.quantity:
            return False
        long = p.quantity > 0
        sl, tp = p.stop_loss, p.take_profit
        stop = sl is not None and (minute.low <= sl if long else minute.high >= sl)
        profit = allow_profit and tp is not None and (minute.high >= tp if long else minute.low <= tp)
        gap_stop = use_open and sl is not None and (minute.open <= sl if long else minute.open >= sl)
        gap_profit = use_open and tp is not None and (minute.open >= tp if long else minute.open <= tp)
        if not (stop or profit):
            return False
        if gap_profit and not gap_stop and allow_profit:
            price, reason = minute.open, 'take_profit'
        elif stop:
            price, reason = (minute.open if gap_stop else sl) + (-slip if long else slip), 'stop_loss'
        else:
            price, reason = tp, 'take_profit'
        fill('protection', 'sell' if long else 'buy', abs(p.quantity), price, reason,
             bool(stop and profit and not gap_stop and not gap_profit))
        for order in s.orders:
            if order.status == 'pending':
                order.status, order.reason = 'cancelled', '保护退出，撤销剩余订单'
        return True

    exited = protect()
    opened = False
    entries_at_open = True
    for order in sorted(s.orders, key=lambda o: not o.reduce_only):
        if exited or order.status != 'pending' or order.created_at > minute.timestamp:
            continue
        buy = order.side == 'buy'
        p = s.position
        if order.reduce_only:
            if not p.quantity or (p.quantity > 0) == buy:
                order.status, order.reason = 'cancelled', '无可平持仓'
                continue
        elif p.quantity and (p.quantity > 0) != buy:
            order.status, order.reason = 'cancelled', '反向开仓前须先平仓'
            continue
        at_open = True
        triggered_intrabar = False
        if order.kind in {'stop_market', 'stop_limit'} and order.triggered_at is None:
            stop = order.stop_price
            if stop is None or not (minute.high >= stop if buy else minute.low <= stop):
                continue
            triggered_intrabar = not (minute.open >= stop if buy else minute.open <= stop)
            order.triggered_at = minute.timestamp
        if order.kind == 'stop_market':
            at_open = not triggered_intrabar
            price = (order.stop_price if triggered_intrabar else minute.open) + (slip if buy else -slip)
        elif order.kind == 'market':
            price = minute.open + (slip if buy else -slip)
        else:
            limit = order.limit_price
            if limit is None:
                continue
            if triggered_intrabar:
                # OHLC cannot prove that a favorable limit touch followed the trigger.
                if not (order.stop_price <= limit if buy else order.stop_price >= limit):
                    continue
                price, at_open = order.stop_price, False
            elif (minute.open <= limit if buy else minute.open >= limit):
                price = minute.open
            elif (minute.low <= limit if buy else minute.high >= limit):
                price, at_open = limit, False
            else:
                continue
        quantity = min(abs(p.quantity), order.quantity) if order.reduce_only else order.quantity
        was_flat = not p.quantity
        fill(order.id, order.side, quantity, price, 'reduce' if order.reduce_only else 'entry')
        order.status = 'filled'
        if was_flat:
            p.stop_loss, p.take_profit = order.stop_loss, order.take_profit
        if not order.reduce_only:
            opened = True
            entries_at_open = entries_at_open and at_open
    if opened and not exited:
        protect(allow_profit=entries_at_open, use_open=entries_at_open)
    s.cursor = minute.timestamp + timedelta(minutes=1)
    s.instrument_id = minute.instrument_id
    s.last_price_at = minute.timestamp
    mark_to_market(s, minute.close, instrument)
    return result
