from datetime import UTC, datetime, timedelta, time as wall_time
from decimal import Decimal
import random
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from app.analysis.workflow.stage1.core.bar_identity import assign_timestamp_refs
from app.core.models import TradingHours
from app.market.trading_hours import bucket_end, bucket_start, in_trading_hours, rth_window
from app.core.database import SessionFactory
from app.core.errors import AppError
from app.market.archive import SCALE, table, archive_uncovered_ranges
from app.market.service import PERIOD_MINUTES, floor_bucket
from app.replay.contracts import MinuteSnapshot


def visible_bars(minutes: list[MinuteSnapshot], cursor: datetime, period: str, limit: int,
                 symbol: str = 'ES', trading_hours: TradingHours = 'ETH') -> list[dict[str, Any]]:
    buckets: dict[datetime, dict[str, Any]] = {}
    for minute in sorted(minutes, key=lambda m: m.timestamp):
        if (minute.timestamp + timedelta(minutes=1) > cursor or not minute.confirmed
                or not in_trading_hours(minute.timestamp, symbol, trading_hours)):
            continue
        key = bucket_start(minute.timestamp, period, symbol, trading_hours)
        if key not in buckets:
            buckets[key] = dict(timestamp=key.isoformat(), open=float(minute.open), high=float(minute.high),
                                low=float(minute.low), close=float(minute.close), volume=minute.volume or 0,
                                is_partial=bucket_end(minute.timestamp, period, symbol, trading_hours) > cursor,
                                trading_hours=trading_hours)
        else:
            bar = buckets[key]
            bar.update(high=max(bar['high'], float(minute.high)), low=min(bar['low'], float(minute.low)),
                       close=float(minute.close), volume=bar['volume'] + (minute.volume or 0))
    visible = list(buckets.values())[-limit:]
    if trading_hours == "ETH":
        for bar in visible:
            bar.pop("trading_hours", None)
        return visible
    refs = assign_timestamp_refs([datetime.fromisoformat(bar['timestamp']) for bar in visible],
                                 symbol=symbol, timeframe=period, trading_hours=trading_hours)
    for bar, ref in zip(visible, refs):
        bar.update(timeframe=ref.timeframe, session=ref.session, day_index=ref.day_index)
    return visible

def regular_closure(start: datetime, end: datetime) -> bool:
    """Prove only regular CME breaks; holidays need stored coverage evidence."""
    minute = start
    eastern = ZoneInfo('America/New_York')
    while minute < end:
        local = minute.astimezone(eastern)
        weekday, hour = local.weekday(), local.hour
        closed = (weekday == 5 or (weekday == 4 and hour >= 17)
                  or (weekday == 6 and hour < 18)
                  or (weekday < 4 and hour == 17))
        if not closed:
            return False
        minute += timedelta(minutes=1)
    return True


class ReplayMarket:
    async def load(self, symbol: str, start: datetime, end: datetime, *, require_complete: bool = False, trading_hours: TradingHours = "ETH") -> list[MinuteSnapshot]:
        """Read local snapshots; matching additionally requires proven missing minutes."""
        if trading_hours == "RTH":
            return await self._load_rth(symbol, start, end, require_complete=require_complete)
        end = min(end, datetime.now(UTC).replace(second=0, microsecond=0))
        if end <= start:
            return []
        async with SessionFactory() as session:
            rows = (await session.execute(text(f'''SELECT DISTINCT ON (timestamp_utc)
                timestamp_utc,open_raw,high_raw,low_raw,close_raw,volume,instrument_id,source,confirmed
                FROM {table('bars')} WHERE root=:symbol AND timestamp_utc>=:start AND timestamp_utc<:end
                ORDER BY timestamp_utc,confirmed DESC,(instrument_id>0) DESC,updated_at DESC,instrument_id DESC'''),
                dict(symbol=symbol, start=start, end=end))).mappings().all()
        minutes = [MinuteSnapshot(timestamp=r['timestamp_utc'], **{k: Decimal(r[k+'_raw'])/SCALE for k in ['open','high','low','close']},
                    volume=r['volume'], instrument_id=r['instrument_id'], source=r['source'], confirmed=r['confirmed']) for r in rows]
        if require_complete:
            await self._assert_complete(symbol, start, end, minutes)
        return minutes

    async def _assert_complete(self, symbol: str, start: datetime, end: datetime, rows: list[MinuteSnapshot]) -> None:
        gaps: list[tuple[datetime, datetime]] = []
        cursor = start
        for row in rows:
            if not row.confirmed:
                raise AppError('replay_market_gap', '本地分钟行情未确认，已暂停', 409)
            if row.timestamp > cursor:
                gaps.append((cursor, row.timestamp))
            cursor = row.timestamp + timedelta(minutes=1)
        if cursor < end:
            gaps.append((cursor, end))
        if not gaps or all(regular_closure(left, right) for left, right in gaps):
            return
        uncovered = await archive_uncovered_ranges(symbol, start, end)
        if all(regular_closure(max(left, gap_start), min(right, gap_end))
               for left, right in uncovered for gap_start, gap_end in gaps
               if max(left, gap_start) < min(right, gap_end)):
            return
        raise AppError('replay_market_gap', '本地行情存在无法确认的数据缺口，已暂停；请先补充本地行情', 409)

    async def random_start(self, symbol: str, trading_hours: TradingHours = "ETH") -> datetime:
        """Seek an archived confirmed minute with a confirmed preceding price."""
        latest = datetime.now(UTC).replace(second=0, microsecond=0)
        async with SessionFactory() as session:
            params: dict[str, Any] = dict(symbol=symbol, latest=latest)
            bounds = []
            for direction in ('ASC', 'DESC'):
                bounds.append(await session.scalar(text(f'''SELECT timestamp_utc FROM {table('bars')}
                    WHERE root=:symbol AND confirmed AND timestamp_utc<:latest
                    ORDER BY timestamp_utc {direction} LIMIT 1'''), params))
            first, last = bounds
            if first is None or last is None:
                raise AppError('replay_no_market', '当前品种暂无可用本地回放行情', 409)
            pivot = (first + (last-first)*random.random()).replace(second=0, microsecond=0)
            window_filter = ""
            if trading_hours == "RTH":
                window_filter = """AND EXTRACT(ISODOW FROM b.timestamp_utc AT TIME ZONE 'America/New_York') <= 5
                    AND (b.timestamp_utc AT TIME ZONE 'America/New_York')::time >= CAST(:rth_open AS time)
                    AND (b.timestamp_utc AT TIME ZONE 'America/New_York')::time < CAST(:rth_close AS time)"""
                params.update(rth_open=wall_time(8, 20) if symbol.upper().startswith('MGC') else wall_time(9, 30),
                              rth_close=wall_time(13, 30) if symbol.upper().startswith('MGC') else wall_time(16, 15))
            query = text(f'''SELECT b.timestamp_utc FROM {table('bars')} b
                WHERE b.root=:symbol AND b.confirmed AND b.timestamp_utc>=:pivot AND b.timestamp_utc<:latest {window_filter}
                AND EXISTS (SELECT 1 FROM {table('bars')} p WHERE p.root=b.root AND p.confirmed
                    AND p.timestamp_utc=b.timestamp_utc-interval '1 minute')
                ORDER BY b.timestamp_utc LIMIT 1''')
            for lower in (pivot, first):
                chosen = await session.scalar(query, dict(params, pivot=lower))
                if chosen is not None:
                    return chosen
        raise AppError('replay_no_market', '当前品种本地行情不足，随机回放需要连续已确认分钟', 409)

    async def next_minute(self, symbol: str, cursor: datetime, trading_hours: TradingHours = "ETH") -> MinuteSnapshot:
        if trading_hours == "RTH":
            return await self._next_rth_minute(symbol, cursor)
        start = cursor
        horizon = min(cursor + timedelta(days=7), datetime.now(UTC).replace(second=0, microsecond=0))
        for span in [1, 50, 200, 800, 10080]:
            end = min(start + timedelta(minutes=span), horizon)
            if start >= end:
                break
            rows = await self.load(symbol, start, end)
            if rows:
                if not rows[0].confirmed:
                    raise AppError('replay_market_gap', '下一分钟行情未确认，已暂停；请稍后重试', 409)
                await self._assert_complete(symbol, cursor, rows[0].timestamp, [])
                return rows[0]
            start = end
        raise AppError('replay_no_market', '已到本地行情边界，后续七天内暂无已存行情', 409)


    async def _load_rth(self, symbol: str, start: datetime, end: datetime, *, require_complete: bool = False) -> list[MinuteSnapshot]:
        """Only require archive coverage inside the requested regular sessions."""
        rows: list[MinuteSnapshot] = []
        opened, closed = rth_window(start, symbol)
        while opened < end:
            begin, until = max(start, opened), min(end, closed)
            if begin < until and in_trading_hours(opened, symbol, 'RTH'):
                rows.extend(await self.load(symbol, begin, until, require_complete=require_complete))
            opened, closed = rth_window(opened + timedelta(days=1), symbol)
        return rows


    async def _next_rth_minute(self, symbol: str, cursor: datetime) -> MinuteSnapshot:
        """Search at most seven days, without querying excluded overnight gaps."""
        horizon = min(cursor + timedelta(days=7), datetime.now(UTC).replace(second=0, microsecond=0))
        opened, _ = rth_window(cursor, symbol)
        for day in range(8):
            # RTH opens remain on the next local date through DST transitions.
            opened, closed = rth_window(opened + timedelta(days=1) if day else cursor, symbol)
            start, end = max(cursor, opened), min(closed, horizon)
            if opened >= horizon:
                break
            if start >= end or not in_trading_hours(opened, symbol, 'RTH'):
                continue
            # Keep the common next-minute path small; widen only when empty.
            for span in (1, 50, 200, 800):
                until = min(start + timedelta(minutes=span), end)
                if start >= until:
                    break
                rows = await self.load(symbol, start, until)
                eligible = sorted((row for row in rows if start <= row.timestamp < until
                                   and in_trading_hours(row.timestamp, symbol, 'RTH')),
                                  key=lambda row: row.timestamp)
                if eligible:
                    if not eligible[0].confirmed:
                        raise AppError('replay_market_gap', '下一分钟行情未确认，已暂停；请稍后重试', 409)
                    await self._assert_complete(symbol, start, eligible[0].timestamp, [])
                    return eligible[0]
                await self._assert_complete(symbol, start, until, [])
                start = until
        raise AppError('replay_no_market', '后续七天内暂无已确认 RTH 行情，可重试或放弃场次', 409)
