from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.analysis.workflow.stage1.core.bar_identity import BarRef, assign_bar_refs, assign_timestamp_refs, resolve_bar_ref
from app.analysis.workflow.stage1.core.data.base import IndicatorBundle, KlineBar, KlineFrame


def _bar(timestamp: str, seq: int) -> KlineBar:
    opened = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return KlineBar(
        seq=seq,
        ts_open=opened.timestamp() * 1000,
        open=100,
        high=102,
        low=99,
        close=101,
        volume=10,
    )


def _frame(bars: list[KlineBar], *, symbol: str = "ES", timeframe: str = "5m") -> KlineFrame:
    empty = tuple(float("nan") for _ in bars)
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=tuple(bars),
        indicators=IndicatorBundle(ema20=empty, atr14=empty),
        snapshot_ts_local_ms=0,
    )


def test_cme_session_resets_at_1700_chicago_after_maintenance_break() -> None:
    # August is CDT (UTC-5): 20:55Z=15:55 CT, 22:00Z=17:00 CT.
    bars = [_bar("2026-08-11T20:55:00Z", 2), _bar("2026-08-11T22:00:00Z", 1)]

    refs = assign_bar_refs(bars, symbol="ES", timeframe="5m")

    assert [ref.day_index for ref in refs] == [276, 1]
    assert refs[-1].session == "CME"


def test_cme_open_follows_daylight_saving_time() -> None:
    # January is CST (UTC-6): 23:00Z=17:00 CT and begins a new session.
    bars = [_bar("2026-01-11T21:55:00Z", 2), _bar("2026-01-11T23:00:00Z", 1)]

    refs = assign_bar_refs(bars, symbol="NQ", timeframe="5m")

    assert [ref.day_index for ref in refs] == [276, 1]


def test_us_equity_rth_starts_at_0930_new_york() -> None:
    # August is EDT (UTC-4): 13:30Z=09:30 ET.
    bars = [_bar("2026-08-11T13:30:00Z", 2), _bar("2026-08-11T13:35:00Z", 1)]

    refs = assign_bar_refs(bars, symbol="AAPL", timeframe="5m")

    assert [(ref.session, ref.day_index) for ref in refs] == [
        ("US_EQUITY_RTH", 1),
        ("US_EQUITY_RTH", 2),
    ]


def test_identity_separates_timeframes_at_the_same_timestamp() -> None:
    timestamp = datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc)
    one_minute = BarRef(
        bar_timestamp=timestamp,
        timeframe="1m",
        session="CME",
        day_index=1,
    )
    five_minute = BarRef(
        bar_timestamp=timestamp,
        timeframe="5m",
        session="CME",
        day_index=1,
    )

    assert one_minute.identity != five_minute.identity


def test_cme_day_index_is_stable_when_window_starts_mid_session() -> None:
    refs = assign_timestamp_refs(
        [datetime.fromisoformat("2026-08-11T20:00:00+00:00")],
        symbol="ES",
        timeframe="5m",
    )
    assert refs[0].day_index == 265


def test_equity_rth_day_index_is_stable_when_window_starts_mid_session() -> None:
    refs = assign_timestamp_refs(
        [datetime.fromisoformat("2026-08-11T15:00:00+00:00")],
        symbol="AAPL",
        timeframe="5m",
    )
    assert refs[0].day_index == 19


def test_resolve_bar_ref_uses_timestamp_timeframe_and_session() -> None:
    bar = _bar("2026-08-11T22:00:00Z", 1)
    frame = _frame([bar])
    ref = assign_bar_refs(frame.bars, symbol=frame.symbol, timeframe=frame.timeframe)[0]

    assert resolve_bar_ref(frame, ref) is bar

    wrong_timeframe = ref.model_copy(update={"timeframe": "1m"})
    with pytest.raises(ValueError, match="does not exist"):
        resolve_bar_ref(frame, wrong_timeframe)

    wrong_day_index = ref.model_copy(update={"day_index": ref.day_index + 1})
    with pytest.raises(ValueError, match="day_index"):
        resolve_bar_ref(frame, wrong_day_index)
