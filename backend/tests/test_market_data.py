from datetime import datetime, timedelta, timezone
import asyncio
from types import SimpleNamespace

import pytest

from app.market.service import ANALYSIS_LOOKBACK_BARS, LocalFirstMarketProvider, MinuteCollector, aggregate_bars, calculate_coverage, floor_bucket
from app.core.models import Bar, HistoricalQuery, Period
from app.core.errors import ProviderError


UTC = timezone.utc


def test_analysis_lookback_matches_realtime_snapshot_contract():
    assert ANALYSIS_LOOKBACK_BARS == 80


def bar(minute: int, value: float, volume: float = 1) -> Bar:
    return Bar(
        timestamp=datetime(2026, 8, 8, 1, minute, tzinfo=UTC),
        open=value,
        high=value + 2,
        low=value - 1,
        close=value + 1,
        volume=volume,
    )


def test_floor_bucket_uses_utc_epoch_boundaries():
    value = datetime(2026, 8, 8, 1, 17, 33, tzinfo=UTC)
    assert floor_bucket(value, 15) == datetime(2026, 8, 8, 1, 15, tzinfo=UTC)
    assert floor_bucket(value, 240) == datetime(2026, 8, 8, 0, 0, tzinfo=UTC)


def test_aggregate_ohlcv_and_exclude_open_bucket():
    bars = [bar(index, 100 + index, 2) for index in range(5)]
    result = aggregate_bars(bars, "5m", now=datetime(2026, 8, 8, 1, 6, tzinfo=UTC))
    assert len(result) == 1
    assert result[0].open == 100
    assert result[0].high == 106
    assert result[0].low == 99
    assert result[0].close == 105
    assert result[0].volume == 10


def test_aggregate_does_not_emit_current_unclosed_bucket():
    bars = [bar(index, 100 + index) for index in range(5, 10)]
    assert aggregate_bars(bars, "5m", now=datetime(2026, 8, 8, 1, 9, tzinfo=UTC)) == []


def test_aggregate_can_emit_and_update_current_partial_bucket():
    bars = [bar(5, 105, 2), bar(6, 106, 3)]

    result = aggregate_bars(
        bars,
        "5m",
        now=datetime(2026, 8, 8, 1, 7, tzinfo=UTC),
        include_partial=True,
    )

    assert len(result) == 1
    assert result[0].timestamp == datetime(2026, 8, 8, 1, 5, tzinfo=UTC)
    assert result[0].open == 105
    assert result[0].high == 108
    assert result[0].low == 104
    assert result[0].close == 107
    assert result[0].volume == 5


def test_coverage_reports_missing_source_buckets():
    start = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    bars = [bar(0, 100), bar(2, 102)]
    coverage = calculate_coverage(bars, start, start + timedelta(minutes=3), "1m")
    assert coverage.expected_bars == 3
    assert coverage.actual_bars == 2
    assert coverage.complete is False
    assert coverage.missing_buckets == [start + timedelta(minutes=1)]


def test_find_missing_session_buckets_ignores_cme_session_boundary():
    from app.market.service import find_missing_session_buckets

    # 15:58 CT ≈ 20:58 UTC (CDT) and 17:00 CT ≈ 22:00 UTC — different CME session days.
    before_break = Bar(
        timestamp=datetime(2026, 8, 7, 20, 58, tzinfo=UTC),
        open=100, high=101, low=99, close=100.5, volume=1,
    )
    after_open = Bar(
        timestamp=datetime(2026, 8, 7, 22, 0, tzinfo=UTC),
        open=100, high=101, low=99, close=100.5, volume=1,
    )
    assert find_missing_session_buckets([before_break, after_open], "1m", "ES") == []


def test_find_missing_session_buckets_flags_intra_session_hole():
    from app.market.service import find_missing_session_buckets

    start = datetime(2026, 8, 8, 1, 0, tzinfo=UTC)
    bars = [bar(0, 100), bar(2, 102)]
    assert find_missing_session_buckets(bars, "1m", "ES") == [start + timedelta(minutes=1)]


def test_collector_window_heals_recent_lookback_and_chunks_catchup():
    collector = MinuteCollector(
        settings=SimpleNamespace(
            collector_lookback_minutes=30,
            collector_max_catchup_minutes=120,
        )
    )
    end = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    # Fresh symbol: only the heal/lookback window.
    start, chunk_end = collector._collection_window(None, end)
    assert start == end - timedelta(minutes=30)
    assert chunk_end == end

    # Caught up: still re-pull lookback so holes are rewritten.
    latest = end - timedelta(minutes=1)
    start, chunk_end = collector._collection_window(latest, end)
    assert start == end - timedelta(minutes=30)
    assert chunk_end == end

    # Multi-day lag: advance in catch-up chunks from the watermark.
    latest = end - timedelta(hours=10)
    start, chunk_end = collector._collection_window(latest, end)
    assert start == latest
    assert chunk_end == latest + timedelta(minutes=120)


def test_collector_runs_each_symbol_then_alert_evaluation(monkeypatch):
    events: list[str] = []
    collector = MinuteCollector(settings=SimpleNamespace(collector_symbols=("ES", "NQ")))

    async def no_schema(): return None
    async def collect(symbol: str, now=None): events.append(f"collect:{symbol}"); return 1
    async def evaluate(now): events.append("alerts")

    monkeypatch.setattr("app.market.service.ensure_schema", no_schema)
    monkeypatch.setattr(collector, "collect_symbol", collect)
    monkeypatch.setattr("app.market.service.evaluate_alert_rules", evaluate)
    asyncio.run(collector.run_once(datetime(2026, 8, 8, 1, 1, tzinfo=UTC)))
    assert events == ["collect:ES", "collect:NQ", "alerts"]


def test_kline_symbol_allowlist_normalizes_contracts_and_micros():
    from app.market.service import validate_kline_symbol

    assert validate_kline_symbol("es") == "ES"
    assert validate_kline_symbol("NQ") == "NQ"
    assert validate_kline_symbol("ESU6") == "ES"
    assert validate_kline_symbol("ESZ25") == "ES"
    assert validate_kline_symbol("MNQ") == "NQ"
    assert validate_kline_symbol("MNQU6") == "NQ"
    assert validate_kline_symbol("MES") == "ES"

    with pytest.raises(ValueError, match="仅支持 ES、NQ"):
        validate_kline_symbol("GC")
    with pytest.raises(ValueError, match="仅支持 ES、NQ"):
        validate_kline_symbol("CLU6")


def test_local_first_provider_serves_partial_on_upstream_failure(monkeypatch):
    async def fake_query_market_range(symbol, period, start, end, **kwargs):
        from app.market.service import MarketRange, Coverage
        bars = [bar(0, 100)]
        return MarketRange(
            symbol=symbol,
            period=period,
            bars=bars,
            coverage=Coverage(
                source_period="1m",
                expected_bars=5,
                actual_bars=1,
                complete=False,
                missing_buckets=[],
            ),
        )

    async def failing_backfill(provider, query):
        raise ProviderError("provider_unavailable", "upstream is down", 502)

    monkeypatch.setattr("app.market.service.query_market_range", fake_query_market_range)
    monkeypatch.setattr("app.market.service.backfill_range", failing_backfill)
    monkeypatch.setattr("app.market.service.ensure_schema", async_noop)

    provider = LocalFirstMarketProvider(upstream=object())
    query = HistoricalQuery(
        symbol="ES",
        period=Period.minute_1,
        start=datetime(2026, 8, 8, 1, 0, tzinfo=UTC),
        end=datetime(2026, 8, 8, 1, 5, tzinfo=UTC),
    )
    result = asyncio.run(provider.get_range(query))
    assert len(result) == 1
    assert result[0].open == 100


def test_local_first_provider_raises_when_no_local_and_upstream_fails(monkeypatch):
    async def empty_query_market_range(symbol, period, start, end, **kwargs):
        from app.market.service import MarketRange, Coverage
        return MarketRange(
            symbol=symbol,
            period=period,
            bars=[],
            coverage=Coverage(
                source_period="1m",
                expected_bars=5,
                actual_bars=0,
                complete=False,
                missing_buckets=[],
            ),
        )

    async def failing_backfill(provider, query):
        raise ProviderError("provider_unavailable", "upstream is down", 502)

    monkeypatch.setattr("app.market.service.query_market_range", empty_query_market_range)
    monkeypatch.setattr("app.market.service.backfill_range", failing_backfill)
    monkeypatch.setattr("app.market.service.ensure_schema", async_noop)

    provider = LocalFirstMarketProvider(upstream=object())
    query = HistoricalQuery(
        symbol="ES",
        period=Period.minute_1,
        start=datetime(2026, 8, 8, 1, 0, tzinfo=UTC),
        end=datetime(2026, 8, 8, 1, 5, tzinfo=UTC),
    )
    with pytest.raises(ProviderError, match="upstream is down"):
        asyncio.run(provider.get_range(query))


async def async_noop(*args, **kwargs):
    return None
