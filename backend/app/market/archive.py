"""Single PostgreSQL minute archive, preserving raw provider precision."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.database import SessionFactory
from app.core.models import Bar

ARCHIVE_SCHEMA = "market_archive_2023"
SCALE = 1_000_000_000
MAX_COVERAGE_RANGES = 512
logger = logging.getLogger(__name__)
CoverageInterval = tuple[datetime, datetime]


def table(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", ARCHIVE_SCHEMA) or not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError("Invalid archive identifier")
    return f'"{ARCHIVE_SCHEMA}"."{name}"'


@dataclass(frozen=True)
class ArchiveRecord:
    root: str
    continuous_symbol: str
    instrument_id: int
    ts_event_ns: int
    timestamp_utc: datetime
    open_raw: int
    high_raw: int
    low_raw: int
    close_raw: int
    volume: int | None
    publisher_id: int
    source: str = "provider"
    confirmed: bool = True


@dataclass(frozen=True)
class ArchiveBatch:
    records: list[ArchiveRecord]
    covered_until: datetime | None


async def ensure_archive_schema(connection: AsyncConnection) -> None:
    """Additive, repeatable upgrade; never deletes a legacy or archive table."""
    await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{ARCHIVE_SCHEMA}"'))
    await connection.execute(text(f"""CREATE TABLE IF NOT EXISTS {table('bars')} (
        root varchar(3) NOT NULL, continuous_symbol varchar(16) NOT NULL,
        instrument_id bigint NOT NULL, ts_event_ns bigint NOT NULL,
        timestamp_utc timestamptz NOT NULL,
        open_raw bigint NOT NULL, high_raw bigint NOT NULL, low_raw bigint NOT NULL,
        close_raw bigint NOT NULL, volume bigint CHECK(volume >= 0), publisher_id integer NOT NULL,
        source text NOT NULL DEFAULT 'provider', confirmed boolean NOT NULL DEFAULT true,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(root,ts_event_ns,instrument_id))"""))
    for ddl in ["ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'provider'",
                "ADD COLUMN IF NOT EXISTS confirmed boolean NOT NULL DEFAULT true",
                "ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()",
                "ALTER COLUMN volume DROP NOT NULL",
                "ADD COLUMN IF NOT EXISTS live_first_ns bigint",
                "ADD COLUMN IF NOT EXISTS live_last_ns bigint"]:
        await connection.execute(text(f"ALTER TABLE {table('bars')} {ddl}"))
    await connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_bars_symbol_time ON {table('bars')}(root,timestamp_utc)"))
    await connection.execute(text(f"CREATE TABLE IF NOT EXISTS {table('metadata')} (key text PRIMARY KEY,value text NOT NULL)"))
    # A tombstone and a minute fingerprint prevent replays even after reconnects.
    await connection.execute(text(f"""CREATE TABLE IF NOT EXISTS {table('live_events')} (
        root text NOT NULL, event_key text NOT NULL, timestamp_utc timestamptz NOT NULL,
        PRIMARY KEY(root,event_key))"""))
    await connection.execute(text(f"""CREATE OR REPLACE VIEW {table('minute_bars')} AS
        SELECT root AS symbol,continuous_symbol,instrument_id,ts_event_ns,timestamp_utc,
        (open_raw::numeric/1000000000)::numeric(20,9) AS open,
        (high_raw::numeric/1000000000)::numeric(20,9) AS high,
        (low_raw::numeric/1000000000)::numeric(20,9) AS low,
        (close_raw::numeric/1000000000)::numeric(20,9) AS close,volume FROM {table('bars')}"""))


async def write_archive_records(session: AsyncSession, records: Sequence[ArchiveRecord]) -> int:
    """Upsert in the caller's transaction; provisional bars never replace confirmed."""
    if not records:
        return 0
    from dataclasses import asdict
    statement = text(f"""INSERT INTO {table('bars')}
        (root,continuous_symbol,instrument_id,ts_event_ns,timestamp_utc,
         open_raw,high_raw,low_raw,close_raw,volume,publisher_id,source,confirmed)
        VALUES (:root,:continuous_symbol,:instrument_id,:ts_event_ns,:timestamp_utc,
         :open_raw,:high_raw,:low_raw,:close_raw,:volume,:publisher_id,:source,:confirmed)
        ON CONFLICT(root,ts_event_ns,instrument_id) DO UPDATE SET
         open_raw=excluded.open_raw, high_raw=excluded.high_raw, low_raw=excluded.low_raw,
         close_raw=excluded.close_raw, volume=excluded.volume,publisher_id=excluded.publisher_id,
         continuous_symbol=excluded.continuous_symbol,source=excluded.source,
         confirmed=excluded.confirmed,updated_at=now()
        WHERE NOT bars.confirmed OR excluded.confirmed""")
    await session.execute(statement, [asdict(record) for record in records])
    return len(records)


def _coverage_interval(start: Any, end: Any) -> CoverageInterval | None:
    """Metadata without both explicit UTC bounds cannot prove coverage."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        left = datetime.fromisoformat(start.replace("Z", "+00:00"))
        right = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Historic date-only metadata was written in UTC as well.
    left = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
    right = right.replace(tzinfo=UTC) if right.tzinfo is None else right.astimezone(UTC)
    return (left, right) if left < right else None


def _metadata_json(value: str | None, key: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        logger.warning("archive invalid_coverage_metadata key=%s", key)
        return None


def _stored_coverage_ranges(value: str | None, key: str) -> list[CoverageInterval]:
    data = _metadata_json(value, key)
    if not isinstance(data, list):
        return []
    ranges = []
    for item in data:
        if isinstance(item, list) and len(item) == 2:
            interval = _coverage_interval(item[0], item[1])
            if interval is not None:
                ranges.append(interval)
    return ranges


def _merge_coverage_ranges(ranges: Sequence[CoverageInterval]) -> list[CoverageInterval]:
    merged: list[CoverageInterval] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


async def write_archive_coverage(
    session: AsyncSession, symbol: str, start: datetime, end: datetime,
) -> None:
    """Persist proven half-open ranges in the same transaction as their records."""
    interval = _coverage_interval(start.isoformat(), end.isoformat())
    if interval is None:
        return
    key = f"coverage_ranges:{symbol}"
    # Create the lockable row first; concurrent first writers also serialize here.
    await session.execute(text(f"INSERT INTO {table('metadata')} (key,value) VALUES (:key,'[]') ON CONFLICT(key) DO NOTHING"), {"key": key})
    value = await session.scalar(text(f"SELECT value FROM {table('metadata')} WHERE key=:key FOR UPDATE"), {"key": key})
    ranges = _merge_coverage_ranges([*_stored_coverage_ranges(value, key), interval])
    # Bound metadata size without closing holes: evicted old ranges may be fetched
    # again, but will never incorrectly prove data that was not downloaded.
    ranges = ranges[-MAX_COVERAGE_RANGES:]
    encoded = json.dumps([[left.isoformat(), right.isoformat()] for left, right in ranges])
    await session.execute(text(f"UPDATE {table('metadata')} SET value=:value WHERE key=:key"), {"key": key, "value": encoded})


async def _archive_coverage_ranges(symbol: str) -> list[CoverageInterval]:
    backfill_key = f"backfill_2023:{symbol}"
    range_key = f"coverage_ranges:{symbol}"
    async with SessionFactory() as session:
        values = dict((await session.execute(text(f"SELECT key,value FROM {table('metadata')} WHERE key IN ('start','end_exclusive','status',:backfill_key,:range_key)"), {"backfill_key": backfill_key, "range_key": range_key})).all())
    ranges = _stored_coverage_ranges(values.get(range_key), range_key)
    if symbol in {"MES", "MNQ", "MGC"} and values.get("status") == "complete":
        legacy = _coverage_interval(values.get("start"), values.get("end_exclusive"))
        if legacy is not None:
            ranges.append(legacy)
    backfill = _metadata_json(values.get(backfill_key), backfill_key)
    if isinstance(backfill, dict):
        committed = _coverage_interval(backfill.get("start"), backfill.get("covered_until"))
        if committed is not None:
            ranges.append(committed)
    return _merge_coverage_ranges(ranges)


async def archive_uncovered_ranges(symbol: str, start: datetime, end: datetime) -> list[CoverageInterval]:
    """Return the parts of a range lacking stored download coverage evidence."""
    if start >= end:
        return []
    cursor = start
    missing: list[CoverageInterval] = []
    for left, right in await _archive_coverage_ranges(symbol):
        if right <= cursor:
            continue
        if left >= end:
            break
        if left > cursor:
            missing.append((cursor, left))
        cursor = max(cursor, min(right, end))
    if cursor < end:
        missing.append((cursor, end))
    return missing


async def archive_range_is_covered(symbol: str, start: datetime, end: datetime) -> bool:
    """Check downloaded ranges, including confirmed sessions with no trades."""
    requested = _coverage_interval(start.isoformat(), end.isoformat())
    if requested is None:
        return False
    return not await archive_uncovered_ranges(symbol, *requested)


async def read_archive_bars(symbol: str, period: str, start: datetime, end: datetime) -> list[Bar]:
    if period != "1m":
        from app.market.service import aggregate_bars
        minutes = await read_archive_bars(symbol, "1m", start, end)
        computed = aggregate_bars(minutes, period, include_partial=True)
        # Unreconstructable legacy hours stay available after the old table is removed.
        if period == "1h":
            async with SessionFactory() as session:
                exists = await session.scalar(text("SELECT to_regclass(:name)"), {"name": f"{ARCHIVE_SCHEMA}.legacy_bars_backup"})
                if exists:
                    rows = (await session.execute(text(f"SELECT opened_at,open,high,low,close,volume FROM {table('legacy_bars_backup')} WHERE symbol=:symbol AND period='1h' AND opened_at>=:start AND opened_at<:end"), {"symbol":symbol,"start":start,"end":end})).mappings().all()
                    by_time = {bar.timestamp:bar for bar in computed}
                    for row in rows:
                        # A partial minute sequence cannot replace a complete migrated hour.
                        count=sum(1 for bar in minutes if row['opened_at']<=bar.timestamp<row['opened_at']+timedelta(hours=1))
                        if count<60 or row['opened_at'] not in by_time:
                            by_time[row['opened_at']]=Bar(timestamp=row['opened_at'],open=row['open'],high=row['high'],low=row['low'],close=row['close'],volume=row['volume'])
                    computed = sorted(by_time.values(), key=lambda bar:bar.timestamp)
        return computed
    async with SessionFactory() as session:
        rows = (await session.execute(text(f"""SELECT DISTINCT ON (timestamp_utc)
          timestamp_utc,open_raw,high_raw,low_raw,close_raw,volume
          FROM {table('bars')} WHERE root=:symbol AND timestamp_utc>=:start AND timestamp_utc<:end
          ORDER BY timestamp_utc,confirmed DESC,(instrument_id>0) DESC,updated_at DESC,instrument_id DESC"""),
          {"symbol":symbol,"start":start,"end":end})).mappings().all()
    return [Bar(timestamp=row['timestamp_utc'],open=row['open_raw']/SCALE,
        high=row['high_raw']/SCALE,low=row['low_raw']/SCALE,close=row['close_raw']/SCALE,
        volume=row['volume']) for row in rows]


async def initial_coverage(session: AsyncSession, symbol: str) -> datetime | None:
    # Archived download chunks prove coverage through empty sessions, unlike max(bar).
    metadata = (await session.execute(text(f"SELECT key,value FROM {table('metadata')}"))).all()
    values = dict(metadata)
    for key in ('end_exclusive','source_end_exclusive','end'):
        if symbol in ('MES','MNQ','MGC') and values.get(key):
            try:
                return datetime.fromisoformat(values[key].replace('Z','+00:00')).astimezone(UTC)
            except ValueError:
                pass
    latest = await session.scalar(text(f"SELECT max(timestamp_utc) FROM {table('bars')} WHERE root=:root AND confirmed"),{'root':symbol})
    if latest:
        from datetime import timedelta
        return latest + timedelta(minutes=1)
    return None
