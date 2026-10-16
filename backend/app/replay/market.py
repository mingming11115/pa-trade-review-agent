from datetime import UTC, datetime, timedelta
from decimal import Decimal
import random
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from app.core.database import SessionFactory
from app.core.errors import AppError
from app.market.archive import SCALE, table, archive_uncovered_ranges
from app.market.service import PERIOD_MINUTES, floor_bucket
from app.replay.contracts import MinuteSnapshot


def visible_bars(minutes: list[MinuteSnapshot], cursor: datetime, period: str, limit: int) -> list[dict[str, Any]]:
    buckets: dict[datetime, dict[str, Any]] = {}
    for minute in sorted(minutes, key=lambda m: m.timestamp):
        if minute.timestamp + timedelta(minutes=1) > cursor or not minute.confirmed:
            continue
        key = floor_bucket(minute.timestamp, PERIOD_MINUTES[period])
        if key not in buckets:
            buckets[key] = dict(timestamp=key.isoformat(), open=float(minute.open), high=float(minute.high),
                                low=float(minute.low), close=float(minute.close), volume=minute.volume or 0,
                                is_partial=key + timedelta(minutes=PERIOD_MINUTES[period]) > cursor)
        else:
            bar = buckets[key]
            bar.update(high=max(bar['high'], float(minute.high)), low=min(bar['low'], float(minute.low)),
                       close=float(minute.close), volume=bar['volume'] + (minute.volume or 0))
    return list(buckets.values())[-limit:]


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
    async def load(self, symbol: str, start: datetime, end: datetime, *, require_complete: bool = False) -> list[MinuteSnapshot]:
        """Read local snapshots; matching additionally requires proven missing minutes."""
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

    async def random_start(self, symbol: str) -> datetime:
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
            query = text(f'''SELECT b.timestamp_utc FROM {table('bars')} b
                WHERE b.root=:symbol AND b.confirmed AND b.timestamp_utc>=:pivot AND b.timestamp_utc<:latest
                AND EXISTS (SELECT 1 FROM {table('bars')} p WHERE p.root=b.root AND p.confirmed
                    AND p.timestamp_utc=b.timestamp_utc-interval '1 minute')
                ORDER BY b.timestamp_utc LIMIT 1''')
            for lower in (pivot, first):
                chosen = await session.scalar(query, dict(params, pivot=lower))
                if chosen is not None:
                    return chosen
        raise AppError('replay_no_market', '当前品种本地行情不足，随机回放需要连续已确认分钟', 409)

    async def next_minute(self, symbol: str, cursor: datetime) -> MinuteSnapshot:
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
