from datetime import datetime, timedelta, timezone

from app.market.indicators import atr_full, build_market_indicators, classify_bar, ema_full
from app.core.models import Bar


def make_bars(count: int = 70) -> list[Bar]:
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    return [Bar(timestamp=start + timedelta(minutes=index), open=100 + index, high=102 + index, low=99 + index, close=101 + index, volume=10) for index in range(count)]


def test_ema_and_atr_use_reference_warmup_semantics() -> None:
    bars = make_bars()
    ema = ema_full([bar.close for bar in bars], 20)
    atr = atr_full(bars, 14)
    assert ema[18] is None
    assert ema[19] == 110.5
    assert atr[12] is None
    assert atr[13] is not None


def test_indicator_snapshot_is_newest_first_and_aligned() -> None:
    bars = make_bars()
    indicators = build_market_indicators(bars, bars[-20:], symbol="ES", timeframe="1m")
    assert indicators["per_bar"][0]["timeframe"] == "1m"
    assert indicators["per_bar"][0]["session"] == "CME"
    assert indicators["per_bar"][0]["day_index"] >= 1
    assert indicators["per_bar"][0]["bar_timestamp"] == bars[-1].timestamp.isoformat()
    assert indicators["per_bar"][0]["close"] == bars[-1].close
    assert indicators["per_bar"][0]["ema20"] is not None
    assert indicators["program_features"]["atr14_current"] is not None
    assert "hl_count" in indicators["program_features"]
    assert "measured_moves" in indicators["program_features"]
    assert "program_gate" in indicators["program_features"]
    assert "inside_sequence" in indicators["per_bar"][0]
    assert "follow_through_1_2" in indicators["per_bar"][0]


def test_inside_and_outside_take_geometry_priority() -> None:
    previous = Bar(timestamp=datetime(2022, 1, 1, tzinfo=timezone.utc), open=100, high=110, low=90, close=105)
    inside = Bar(timestamp=datetime(2022, 1, 2, tzinfo=timezone.utc), open=99, high=108, low=92, close=101)
    outside = Bar(timestamp=datetime(2022, 1, 3, tzinfo=timezone.utc), open=100, high=112, low=88, close=111)
    assert classify_bar(inside, previous) == "inside"
    assert classify_bar(outside, previous) == "outside_bull"
