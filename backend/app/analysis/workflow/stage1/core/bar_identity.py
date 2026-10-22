"""Stable K-line identities and market-session bar numbering."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from app.analysis.workflow.stage1.core.data.base import KlineBar, KlineFrame
from app.analysis.workflow.stage1.core.datetime_ts import ts_open_to_ms
from app.core.models import TradingHours
from app.market.trading_hours import rth_window


SessionName = Literal["CME", "US_EQUITY_RTH"]
CME_TIME_ZONE = ZoneInfo("America/Chicago")
US_EQUITY_TIME_ZONE = ZoneInfo("America/New_York")
CME_SESSION_OPEN = time(17, 0)
US_EQUITY_RTH_OPEN = time(9, 30)

_CME_ROOTS = {
    "ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K",
    "CL", "MCL", "NG", "RB", "HO", "GC", "MGC", "SI", "HG", "PL", "PA",
    "ZB", "UB", "ZN", "ZF", "ZT", "ZQ", "SR3",
    "ZC", "ZW", "ZS", "ZM", "ZL", "KE", "HE", "LE", "GF",
    "6A", "6B", "6C", "6E", "6J", "6M", "6N", "6S", "DX", "BTC", "MBT", "ETH", "MET",
}


class BarRef(BaseModel):
    bar_timestamp: datetime
    timeframe: str = Field(min_length=1)
    session: SessionName
    trading_hours: TradingHours = "ETH"
    day_index: int = Field(ge=1)

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "BarRef":
        if self.bar_timestamp.tzinfo is None:
            self.bar_timestamp = self.bar_timestamp.replace(tzinfo=timezone.utc)
        else:
            self.bar_timestamp = self.bar_timestamp.astimezone(timezone.utc)
        return self

    @property
    def identity(self) -> tuple[datetime, str, str, str]:
        return (self.bar_timestamp, self.timeframe, self.session, self.trading_hours)


class BarRange(BaseModel):
    start: BarRef
    end: BarRef

    @model_validator(mode="after")
    def validate_identity_domain(self) -> "BarRange":
        if (self.start.timeframe != self.end.timeframe or self.start.session != self.end.session
                or self.start.trading_hours != self.end.trading_hours):
            raise ValueError("bar_range endpoints must share timeframe, session and trading_hours")
        if self.start.bar_timestamp > self.end.bar_timestamp:
            raise ValueError("bar_range start must not be later than end")
        return self


def _symbol_root(symbol: str) -> str:
    normalized = symbol.upper().strip().split(":")[-1]
    for root in sorted((item for item in _CME_ROOTS if item[0].isdigit()), key=len, reverse=True):
        if normalized.startswith(root):
            return root
    letters = "".join(char for char in normalized if char.isalpha())
    for root in sorted(_CME_ROOTS, key=len, reverse=True):
        if letters.startswith(root):
            return root
    return letters


def session_for_symbol(symbol: str) -> SessionName:
    return "CME" if _symbol_root(symbol) in _CME_ROOTS else "US_EQUITY_RTH"


def _utc_datetime(ts_open: float | datetime) -> datetime:
    if isinstance(ts_open, datetime):
        return (ts_open.replace(tzinfo=timezone.utc) if ts_open.tzinfo is None else ts_open).astimezone(timezone.utc)
    return datetime.fromtimestamp(ts_open_to_ms(ts_open) / 1000, tz=timezone.utc)


def _timestamp_ms(ts_open: float | datetime) -> int:
    if isinstance(ts_open, datetime):
        return int(_utc_datetime(ts_open).timestamp() * 1000)
    return int(ts_open_to_ms(ts_open))


def _session_key(opened_at: datetime, session: SessionName) -> date:
    if session == "CME":
        local = opened_at.astimezone(CME_TIME_ZONE)
        return local.date() if local.time() >= CME_SESSION_OPEN else local.date() - timedelta(days=1)
    local = opened_at.astimezone(US_EQUITY_TIME_ZONE)
    return local.date() if local.time() >= US_EQUITY_RTH_OPEN else local.date() - timedelta(days=1)


def _timeframe_seconds(timeframe: str) -> int:
    match = re.fullmatch(r"(\d+)([mhd])", timeframe.strip().lower())
    if not match:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    amount = int(match.group(1))
    unit = {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if amount < 1:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return amount * unit


def _day_index(opened_at: datetime, session: SessionName, timeframe: str) -> int:
    key = _session_key(opened_at, session)
    if session == "CME":
        start = datetime.combine(key, CME_SESSION_OPEN, tzinfo=CME_TIME_ZONE)
    else:
        start = datetime.combine(key, US_EQUITY_RTH_OPEN, tzinfo=US_EQUITY_TIME_ZONE)
    elapsed_seconds = (opened_at - start.astimezone(timezone.utc)).total_seconds()
    return max(1, int(elapsed_seconds // _timeframe_seconds(timeframe)) + 1)


def assign_bar_refs(
    bars: Sequence[KlineBar],
    *,
    symbol: str,
    timeframe: str,
    trading_hours: TradingHours | None = None,
) -> list[BarRef]:
    """Return references aligned to ``bars``, numbered from each actual session open."""
    return assign_timestamp_refs(
        [bar.ts_open for bar in bars],
        symbol=symbol,
        timeframe=timeframe,
        trading_hours=trading_hours or (bars[0].trading_hours if bars else "ETH"),
    )


def assign_timestamp_refs(
    timestamps: Sequence[float | datetime],
    *,
    symbol: str,
    timeframe: str,
    trading_hours: TradingHours = "ETH",
) -> list[BarRef]:
    """Return references aligned to arbitrary bar-open timestamps."""
    session = session_for_symbol(symbol)
    positioned = sorted(enumerate(timestamps), key=lambda item: _timestamp_ms(item[1]))
    refs: list[BarRef | None] = [None] * len(timestamps)
    seen: set[int] = set()
    for original_index, timestamp in positioned:
        timestamp_ms = _timestamp_ms(timestamp)
        if timestamp_ms in seen:
            raise ValueError("duplicate bar timestamp")
        seen.add(timestamp_ms)
        opened_at = _utc_datetime(timestamp_ms)
        if trading_hours == "RTH":
            elapsed = (opened_at - rth_window(opened_at, symbol)[0]).total_seconds()
            day_index = max(1, int(elapsed // _timeframe_seconds(timeframe)) + 1)
        else:
            day_index = _day_index(opened_at, session, timeframe)
        refs[original_index] = BarRef(
            bar_timestamp=opened_at,
            timeframe=timeframe,
            session=session,
            trading_hours=trading_hours,
            day_index=day_index,
        )
    return [ref for ref in refs if ref is not None]


def resolve_bar_ref(frame: KlineFrame, ref: BarRef) -> KlineBar:
    if (ref.timeframe != frame.timeframe or ref.session != session_for_symbol(frame.symbol)
            or ref.trading_hours != frame.trading_hours):
        raise ValueError("bar_ref does not exist in frame")
    timestamp_ms = int(ref.bar_timestamp.timestamp() * 1000)
    match = next((bar for bar in frame.bars if ts_open_to_ms(bar.ts_open) == timestamp_ms), None)
    if match is None:
        raise ValueError("bar_ref does not exist in frame")
    canonical = assign_timestamp_refs(
        [match.ts_open], symbol=frame.symbol, timeframe=frame.timeframe, trading_hours=frame.trading_hours
    )[0]
    if ref.day_index != canonical.day_index:
        raise ValueError("bar_ref day_index does not match its session timestamp")
    return match


def enrich_api_bars(bars: Sequence[object], *, symbol: str, timeframe: str, trading_hours: TradingHours | None = None) -> list[object]:
    """Copy Pydantic API bars with authoritative identity metadata attached."""
    refs = assign_timestamp_refs(
        [getattr(bar, "timestamp") for bar in bars],
        symbol=symbol,
        timeframe=timeframe,
        trading_hours=trading_hours or (getattr(bars[0], "trading_hours", "ETH") if bars else "ETH"),
    )
    return [
        bar.model_copy(update={
            "timeframe": ref.timeframe,
            "session": ref.session,
            "trading_hours": ref.trading_hours,
            "day_index": ref.day_index,
        })
        for bar, ref in zip(bars, refs)
    ]
