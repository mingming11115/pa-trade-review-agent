"""Daily bar index: count from 1 at each trading-day open (PAIO-style)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

# CME equity-index futures session calendar (matches TradingView 1D for ES/NQ-style).
DEFAULT_BAR_INDEX_TZ = "America/Chicago"


def _to_utc_datetime(ts_open: float | datetime) -> datetime:
    if isinstance(ts_open, datetime):
        if ts_open.tzinfo is None:
            return ts_open.replace(tzinfo=timezone.utc)
        return ts_open.astimezone(timezone.utc)
    sec = float(ts_open)
    if sec > 1e12:
        sec /= 1000.0
    return datetime.fromtimestamp(sec, tz=timezone.utc)


def trading_day_key(ts_open: float | datetime, tz_name: str = DEFAULT_BAR_INDEX_TZ) -> str:
    """Return YYYY-MM-DD of the bar open in the session timezone."""
    local = _to_utc_datetime(ts_open).astimezone(ZoneInfo(tz_name))
    return local.strftime("%Y-%m-%d")


def compute_daily_indices_chronological(
    ts_opens: Sequence[float | datetime],
    *,
    tz_name: str = DEFAULT_BAR_INDEX_TZ,
) -> list[int]:
    """Assign 1..N within each trading day for oldest→newest bars."""
    indices: list[int] = []
    prev_day: str | None = None
    counter = 0
    for ts in ts_opens:
        day = trading_day_key(ts, tz_name)
        if day != prev_day:
            counter = 1
            prev_day = day
        else:
            counter += 1
        indices.append(counter)
    return indices


def compute_daily_indices_newest_first(
    ts_opens_newest_first: Sequence[float | datetime],
    *,
    tz_name: str = DEFAULT_BAR_INDEX_TZ,
) -> list[int]:
    """Same as chronological helper, but input/output are newest-first (frame.bars order)."""
    if not ts_opens_newest_first:
        return []
    chrono = list(reversed(ts_opens_newest_first))
    chrono_indices = compute_daily_indices_chronological(chrono, tz_name=tz_name)
    return list(reversed(chrono_indices))
