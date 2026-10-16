from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import time
from typing import Any
import uuid
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.core.logging_context import get_trace_id
from app.market.service import PERIOD_MINUTES, floor_bucket
from app.replay.accounting import apply_fill, mark_to_market
from app.replay.contracts import AccountState, EngineState, InstrumentSpec, MinuteSnapshot, ReplayCommand, ReplayFill, ReplayOrder, SessionCreate
from app.replay.engine import match_minute
from app.replay.history import build_trade_events, historical_state
from app.replay.instruments import get_instrument
from app.replay.market import ReplayMarket, visible_bars
from app.replay.models import ReplaySession
from app.replay.repository import ReplayRepository

logger = logging.getLogger(__name__)


def reject(message: str) -> None:
    raise AppError('replay_invalid_command', message, 422)


class ReplayService:
    def __init__(self, repository: ReplayRepository | None = None, market: ReplayMarket | None = None):
        self.repository = repository or ReplayRepository()
        self.market = market or ReplayMarket()

    async def create(self, scope: str, payload: SessionCreate) -> dict[str, Any]:
        from app.trades.service import Trade, TradeResponse
        snapshots = []
        end = payload.end
        if payload.mode == 'historical':
            if not payload.trade_ids:
                reject('请选择要回放的交易')
            try:
                ids = {uuid.UUID(value) for value in payload.trade_ids}
            except ValueError:
                reject('交易标识无效')
            async with self.repository.factory() as db:
                trades = list(await db.scalars(select(Trade).where(Trade.id.in_(ids))))
            if len(trades) != len(ids):
                reject('部分交易已不存在，请重新选择')
            if any(t.symbol_root != payload.symbol for t in trades):
                reject('请选择同一品种交易')
            snapshots = [TradeResponse.model_validate(t, from_attributes=True).model_dump(mode='json') for t in trades]
            for snapshot in snapshots:
                for field in ('entered_at', 'exited_at'):
                    value = datetime.fromisoformat(snapshot[field])
                    snapshot[field] = (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()
            start = payload.start or min(datetime.fromisoformat(t['entered_at']) for t in snapshots)-timedelta(minutes=60)
            end = end or max(datetime.fromisoformat(t['exited_at']) for t in snapshots)+timedelta(minutes=60)
        else:
            start = payload.start
            if payload.random_start:
                start = await self.market.random_start(payload.symbol)
        if start is None or start > datetime.now(UTC) or (end and end <= start):
            reject('请选择有效历史时间范围')
        start = start.astimezone(UTC).replace(second=0, microsecond=0)
        bars = await self.market.load(payload.symbol, start-timedelta(minutes=120), start)
        bars = [bar for bar in bars if bar.confirmed and bar.timestamp+timedelta(minutes=1)<=start]
        if not bars:
            # A closed-session start remains usable if a subsequent minute exists.
            first = await self.market.next_minute(payload.symbol, start)
            start = first.timestamp
        state = EngineState(cursor=start, config=payload.config,
                            account=AccountState(cash=payload.config.initial_cash, equity=payload.config.initial_cash, peak_equity=payload.config.initial_cash),
                            last_price=bars[-1].close if bars else None, instrument_id=bars[-1].instrument_id if bars else 0)
        row = ReplaySession(scope=scope, mode=payload.mode, symbol=payload.symbol, state=state.model_dump(mode='json'),
                            config=dict(instrument=get_instrument(payload.symbol).model_dump(mode='json'), rules_version=1,
                                        start=start.isoformat(), end=end.isoformat() if end else None))
        return await self.repository.create(row, snapshots)

    async def get(self, scope: str, session_id: str) -> dict[str, Any]:
        return await self.repository.get(scope, session_id)

    async def bars(self, scope: str, session_id: str, period: str, cursor: datetime | None, limit: int) -> dict[str, Any]:
        view = await self.get(scope, session_id)
        tip = datetime.fromisoformat(view['state']['cursor'])
        cursor = min(cursor, tip) if cursor else tip
        minutes = PERIOD_MINUTES[period]
        # Count actual candles across closed sessions, within the existing 30-day bound.
        cutoff = cursor - timedelta(days=30)
        earliest = floor_bucket(cutoff, minutes)
        if earliest < cutoff:
            earliest += timedelta(minutes=minutes)
        end = cursor
        span = timedelta(minutes=minutes * (limit + 1))
        data: list[MinuteSnapshot] = []
        bars: list[dict[str, Any]] = []
        while end > earliest:
            # Aligned adjoining windows preserve complete historical candle OHLC.
            start = max(floor_bucket(end - span, minutes), earliest)
            data.extend(await self.market.load(view['symbol'], start, end))
            bars = visible_bars(data, cursor, period, limit)
            if len(bars) >= limit:
                break
            end = start
            span = max(span * 2, timedelta(days=1))
        return dict(bars=bars, cursor=cursor.isoformat(), source='continuous', next_before=bars[0]['timestamp'] if bars else None)

    async def history(self, scope: str, session_id: str, cursor: datetime | None = None) -> dict[str, Any]:
        view = await self.get(scope, session_id)
        tip = datetime.fromisoformat(view['state']['cursor'])
        cursor = min(cursor, tip) if cursor else tip
        snapshots = await self.repository.snapshots(scope, session_id)
        return historical_state(build_trade_events(snapshots, view['symbol']), cursor, view['state']['last_price'], view['symbol'])

    async def _advance_minutes(self, view: dict[str, Any], period: str) -> tuple[list[MinuteSnapshot], datetime]:
        cursor = datetime.fromisoformat(view['state']['cursor'])
        latest = datetime.now(UTC).replace(second=0, microsecond=0)
        end = datetime.fromisoformat(view['config']['end']) if view['config']['end'] else None
        if end is not None:
            end = end.astimezone(UTC).replace(second=0, microsecond=0)
            latest = min(latest, end)
            if cursor >= end:
                reject('已到回放结束时间')
        if cursor >= latest:
            raise AppError('replay_no_market', '暂无后续已完成分钟行情，请稍后重试', 409)
        first = await self.market.next_minute(view['symbol'], cursor)
        if first.timestamp + timedelta(minutes=1) > latest:
            if end is not None and first.timestamp + timedelta(minutes=1) > end:
                reject('已到回放结束时间')
            raise AppError('replay_no_market', '暂无后续已完成分钟行情，请稍后重试', 409)
        if first.timestamp < cursor or not first.confirmed:
            raise AppError('replay_market_gap', '下一根行情未确认，已暂停；请稍后重试', 409)

        target = min(floor_bucket(first.timestamp, PERIOD_MINUTES[period]) + timedelta(minutes=PERIOD_MINUTES[period]), latest)
        minutes = [first]
        if target > first.timestamp + timedelta(minutes=1):
            data = await self.market.load(view['symbol'], first.timestamp, target, require_complete=True)
            # Matching must prove missing minutes; chart history may show sparse data.
            # Prefer its snapshot if the first minute was refreshed since next_minute.
            by_time = {first.timestamp: first}
            by_time.update({row.timestamp: row for row in data if first.timestamp <= row.timestamp < target})
            minutes = sorted(by_time.values(), key=lambda row: row.timestamp)
        if any(not row.confirmed for row in minutes):
            raise AppError('replay_market_gap', '下一根行情包含未确认分钟，已暂停；请稍后重试', 409)
        return minutes, target

    async def command(self, scope: str, session_id: str, payload: ReplayCommand) -> dict[str, Any]:
        started = time.monotonic()
        fields = dict(trace_id=get_trace_id(), session_id=session_id, operation_id=payload.operation_id, revision=payload.expected_revision, period=payload.period)
        try:
            prior = await self.repository.previous(scope, session_id, payload)
            if prior is not None:
                return prior
            view = await self.get(scope, session_id)
            if view['revision'] != payload.expected_revision:
                prior = await self.repository.previous(scope, session_id, payload)
                if prior is not None:
                    return prior
                raise AppError('replay_revision_conflict', '场次已更新，请刷新后操作', 409)
            minute = None
            minutes = []
            target = payload.cursor
            if payload.type == 'advance':
                minutes, target = await self._advance_minutes(view, payload.period)
                minute = minutes[-1]
            if payload.type == 'seek':
                if target is not None:
                    target = target.astimezone(UTC).replace(second=0, microsecond=0)
                if view['mode'] != 'historical':
                    reject('训练回看不修改成交游标，请使用图表回看')
                if payload.target:
                    events = build_trade_events(await self.repository.snapshots(scope, session_id), view['symbol'])
                    cursor = datetime.fromisoformat(view['state']['cursor'])
                    times = sorted({datetime.fromisoformat(e['timestamp']).replace(second=0, microsecond=0)+timedelta(minutes=1) for e in events})
                    candidates = [t for t in times if (t > cursor if payload.target == 'next' else t < cursor)]
                    if not candidates:
                        reject('没有更多成交事件')
                    target = candidates[0] if payload.target=='next' else candidates[-1]
                if target is None or target < datetime.fromisoformat(view['config']['start']) or target > datetime.now(UTC):
                    reject('定位时间超出回放范围')
                if view['config']['end'] and target > datetime.fromisoformat(view['config']['end']):
                    reject('定位时间超出回放结束时间')
                data = await self.market.load(view['symbol'], target-timedelta(minutes=120), target)
                data = [bar for bar in data if bar.confirmed and bar.timestamp+timedelta(minutes=1)<=target]
                minute = data[-1] if data else None

            def change(current: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
                if current['status'] != 'active':
                    reject('已结束场次不能修改')
                state = EngineState.model_validate(current['state'])
                instrument = InstrumentSpec.model_validate(current['config']['instrument'])
                fills = []
                if payload.type == 'advance':
                    known_instrument_id = state.instrument_id
                    for row in minutes:
                        if current['mode']=='training' and known_instrument_id and row.instrument_id and known_instrument_id != row.instrument_id:
                            raise AppError('replay_rollover', '检测到连续合约换月，已暂停；请放弃本场并重新开始', 409)
                        known_instrument_id = row.instrument_id or known_instrument_id
                        matched = match_minute(state, row, instrument)
                        state = matched.state
                        fills.extend(f.model_dump(mode='json') for f in matched.fills)
                    # Covered empty minutes still complete the visible chart bucket.
                    state.cursor = target
                    state.instrument_id = known_instrument_id
                elif payload.type == 'seek':
                    state.cursor = target
                    state.last_price = minute.close if minute else None
                elif payload.type in {'finish', 'abandon'}:
                    if payload.type=='finish' and state.position.quantity:
                        reject('请先平仓再结束训练')
                    current['status'] = 'completed' if payload.type=='finish' else 'abandoned'
                    for order in state.orders:
                        if order.status == 'pending':
                            order.status, order.reason = 'cancelled', '场次已结束'
                else:
                    if current['mode'] != 'training':
                        reject('真实交易回放不允许下单')
                    fills.extend(fill.model_dump(mode='json') for fill in self._edit_order(state, payload, instrument))
                current['state'] = state.model_dump(mode='json')
                return current, fills, dict(fills=fills, minute=minute.model_dump(mode='json') if minute else None,
                                           minutes=[row.model_dump(mode='json') for row in minutes])

            result = await self.repository.transact(scope, session_id, payload, change)
            logger.info('replay_command_completed', extra={**fields, 'event':'replay_command_completed', 'duration_ms':(time.monotonic()-started)*1000})
            return result
        except Exception as exc:
            logger.exception('replay_command_failed', extra={**fields, 'event':'replay_command_failed', 'duration_ms':(time.monotonic()-started)*1000})
            try:
                await self.repository.record_failure(scope, session_id, payload.operation_id,
                                                     exc.code if isinstance(exc, AppError) else 'internal_error', get_trace_id())
            except SQLAlchemyError:
                logger.exception('replay_failure_persistence_failed', extra=fields)
            raise

    def _edit_order(self, state: EngineState, cmd: ReplayCommand, instrument: InstrumentSpec) -> list[ReplayFill]:
        for price in [cmd.limit_price, cmd.stop_price, cmd.stop_loss, cmd.take_profit]:
            if price is not None and price % instrument.tick_size:
                reject(f'价格须符合最小变动 {instrument.tick_size}')
        p = state.position
        if cmd.type == 'cancel_order':
            order = next((o for o in state.orders if o.id==cmd.order_id and o.status=='pending'), None)
            if order is None:
                reject('订单不存在或已成交')
            order.status, order.reason = 'cancelled', '用户撤单'
            return []
        if cmd.type == 'update_protection':
            if not p.quantity or state.last_price is None:
                reject('当前无持仓')
            self._validate_protection(p.quantity > 0, state.last_price, cmd.stop_loss, cmd.take_profit)
            p.stop_loss, p.take_profit = cmd.stop_loss, cmd.take_profit
            return []
        if cmd.reduce_only:
            if not p.quantity or (p.quantity>0)==(cmd.side=='buy') or cmd.quantity>abs(p.quantity):
                reject('平仓方向或数量无效')
        elif p.quantity and (p.quantity>0)!=(cmd.side=='buy'):
            reject('请先平仓，再反向开仓')
        reference = cmd.limit_price if cmd.kind in {'limit', 'stop_limit'} else cmd.stop_price if cmd.kind=='stop_market' else state.last_price
        if reference is None:
            reject('请先推进到有行情的位置')
        if not cmd.reduce_only and not p.quantity:
            self._validate_protection(cmd.side=='buy', reference, cmd.stop_loss, cmd.take_profit)
        triggered_at = None
        if cmd.type == 'amend_order':
            order = next((o for o in state.orders if o.id==cmd.order_id and o.status=='pending' and o.kind!='market'), None)
            if order is None:
                reject('只可修改未成交挂单')
            if order.triggered_at and (order.kind != cmd.kind or order.stop_price != cmd.stop_price or order.side != cmd.side):
                reject('已触发订单如需更换触发条件，请撤单后重新下单')
            triggered_at = order.triggered_at
            order.status, order.reason = 'cancelled', '改单替换'
        if cmd.kind != 'market' and sum(o.status=='pending' for o in state.orders) >= 100:
            reject('每场最多同时保留 100 个挂单')
        order = ReplayOrder(id=str(uuid.uuid4()), created_at=state.cursor, side=cmd.side, quantity=cmd.quantity,
                            kind=cmd.kind, limit_price=cmd.limit_price, stop_price=cmd.stop_price, triggered_at=triggered_at, reduce_only=cmd.reduce_only,
                            stop_loss=cmd.stop_loss if not p.quantity else None, take_profit=cmd.take_profit if not p.quantity else None)
        state.orders.append(order)
        if order.kind != 'market':
            return []
        # A new market order uses only the current revealed price; legacy pending
        # market orders remain handled by match_minute when the replay advances.
        price = state.last_price
        if price is None:
            reject('请先推进到有行情的位置')
        slip = instrument.tick_size * state.config.slippage_ticks
        fill = ReplayFill(order_id=order.id, timestamp=state.cursor, side=order.side, quantity=order.quantity,
                          price=price + (slip if order.side == 'buy' else -slip),
                          fee=state.config.fee_per_side * order.quantity, reason='reduce' if order.reduce_only else 'entry')
        was_flat = not p.quantity
        apply_fill(state, fill, instrument)
        order.status = 'filled'
        if was_flat:
            p.stop_loss, p.take_profit = order.stop_loss, order.take_profit
        mark_to_market(state, price, instrument)
        return [fill]

    @staticmethod
    def _validate_protection(long: bool, reference: Any, stop: Any, profit: Any) -> None:
        if stop is not None and not (stop < reference if long else stop > reference):
            reject('止损须位于入场参考价的亏损一侧')
        if profit is not None and not (profit > reference if long else profit < reference):
            reject('止盈须位于入场参考价的盈利一侧')
