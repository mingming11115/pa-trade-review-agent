import json
import asyncio
import importlib.util
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.market.live import (
    LiveTrade,
    RealtimeTradeCollector,
    parse_live_trade,
    persist_live_trade,
    subscription_payload,
)


def test_realtime_collector_has_websocket_proxy_runtime_dependency():
    """System proxy discovery may make websockets select its SOCKS transport."""
    assert importlib.util.find_spec("python_socks") is not None


def test_subscription_payload_matches_massive_live_contract():
    assert json.loads(subscription_payload(("ES.c.0", "NQ.c.0"))) == {
        "action": "subscribe",
        "dataset": "GLBX.MDP3",
        "schema": "trades",
        "stype_in": "continuous",
        "symbols": ["ES.c.0", "NQ.c.0"],
    }


def test_parse_live_trade_normalizes_nested_header_and_continuous_symbol():
    message = json.dumps(
        {
            "hd": {"ts_event": 1786449725123456789},
            "symbol": "ES.c.0",
            "price": "6412.25",
            "size": 3,
            "action": "T",
        }
    )

    assert parse_live_trade(message) == LiveTrade(
        symbol="ES",
        timestamp=datetime.fromtimestamp(1786449725.123456789, tz=UTC),
        price=6412.25,
        size=3.0,
    )


def test_parse_live_trade_accepts_flat_iso_timestamp():
    message = json.dumps(
        {
            "ts_event": "2026-08-11T12:02:05.250000Z",
            "symbol": "NQ.c.0",
            "price": 23100.5,
            "size": 1,
        }
    )

    assert parse_live_trade(message) == LiveTrade(
        symbol="NQ",
        timestamp=datetime(2026, 8, 11, 12, 2, 5, 250000, tzinfo=UTC),
        price=23100.5,
        size=1.0,
    )


def test_parse_live_trade_accepts_actual_massive_record_envelope():
    message = json.dumps(
        {
            "type": "record",
            "dataset": "GLBX.MDP3",
            "schema": "trades",
            "record_type": "TradeMsg",
            "fields": {
                "action": "T",
                "price": 7790000000000,
                "size": 5,
                "ts_event": 1786454235185674083,
            },
            "matched_symbols": ["ES.c.0"],
        }
    )

    assert parse_live_trade(message) == LiveTrade(
        symbol="ES",
        timestamp=datetime.fromtimestamp(1786454235.185674083, tz=UTC),
        price=7790.0,
        size=5.0,
    )


def test_parse_live_trade_ignores_control_and_invalid_messages():
    messages = (
        "not-json",
        json.dumps({"type": "connected"}),
        json.dumps({"symbol": "MES.c.0", "ts_event": 1, "price": 1, "size": 1}),
        json.dumps({"symbol": "ES.c.0", "ts_event": 1, "price": 1}),
        json.dumps({"symbol": "ES.c.0", "ts_event": 1, "price": 1, "size": 0}),
    )

    assert [parse_live_trade(message) for message in messages] == [None] * len(messages)


def test_persist_live_trade_builds_atomic_minute_ohlcv_upsert(monkeypatch):
    captured = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, statement):
            captured.append(statement)

        async def commit(self):
            return None

    monkeypatch.setattr("app.market.live.SessionFactory", FakeSession)
    trade = LiveTrade(
        symbol="ES",
        timestamp=datetime(2026, 8, 11, 12, 2, 59, 999999, tzinfo=UTC),
        price=6412.25,
        size=3,
    )

    asyncio.run(persist_live_trade(trade))

    compiled = captured[0].compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    params = compiled.params
    assert params["symbol"] == "ES"
    assert params["period"] == "1m"
    assert params["opened_at"] == datetime(2026, 8, 11, 12, 2, tzinfo=UTC)
    assert params["open"] == params["high"] == params["low"] == params["close"] == 6412.25
    assert params["volume"] == 3
    assert params["source"] == "websocket:trades"
    assert "greatest(" in sql
    assert "least(" in sql
    assert "market_bars.volume + excluded.volume" in sql
    assert "close = excluded.close" in sql


def test_realtime_collector_authenticates_subscribes_and_persists(monkeypatch):
    connection_calls = []
    sent = []
    persisted = []
    collector_holder = {}

    class FakeWebSocket:
        async def recv(self):
            return json.dumps({"type": "connected"})

        async def send(self, message):
            sent.append(json.loads(message))

        def __aiter__(self):
            return self

        async def __anext__(self):
            if persisted:
                collector_holder["collector"].stop()
                raise StopAsyncIteration
            return json.dumps(
                {
                    "ts_event": "2026-08-11T12:02:05Z",
                    "symbol": "ES.c.0",
                    "price": 6412.25,
                    "size": 2,
                }
            )

    class FakeConnection:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, *_):
            return None

    def connect(url, **kwargs):
        connection_calls.append((url, kwargs))
        return FakeConnection()

    async def capture(trade):
        persisted.append(trade)

    monkeypatch.setattr("app.market.live.persist_live_trade", capture)
    settings = SimpleNamespace(
        live_ws_url="ws://example.test/live",
        live_ws_symbols=("ES.c.0", "NQ.c.0"),
        hist_api_key="secret-value",
    )
    collector = RealtimeTradeCollector(settings=settings, connect=connect)
    collector_holder["collector"] = collector

    asyncio.run(asyncio.wait_for(collector.run_forever(), timeout=1))

    assert connection_calls == [
        (
            "ws://example.test/live",
            {"additional_headers": {"x-api-key": "secret-value"}, "max_size": None},
        )
    ]
    assert sent == [json.loads(subscription_payload(settings.live_ws_symbols))]
    assert persisted == [
        LiveTrade(
            symbol="ES",
            timestamp=datetime(2026, 8, 11, 12, 2, 5, tzinfo=UTC),
            price=6412.25,
            size=2,
        )
    ]


def test_realtime_collector_reconnects_after_connection_failure(monkeypatch):
    attempts = 0
    collector_holder = {}

    class StoppingWebSocket:
        async def recv(self):
            return "connected"

        async def send(self, _):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            collector_holder["collector"].stop()
            raise StopAsyncIteration

    class Connection:
        def __init__(self, fail):
            self.fail = fail

        async def __aenter__(self):
            if self.fail:
                raise OSError("disconnected")
            return StoppingWebSocket()

        async def __aexit__(self, *_):
            return None

    def connect(*_, **__):
        nonlocal attempts
        attempts += 1
        return Connection(fail=attempts == 1)

    settings = SimpleNamespace(
        live_ws_url="ws://example.test/live",
        live_ws_symbols=("ES.c.0", "NQ.c.0"),
        hist_api_key="secret-value",
    )
    collector = RealtimeTradeCollector(settings=settings, connect=connect)
    collector_holder["collector"] = collector

    asyncio.run(asyncio.wait_for(collector.run_forever(), timeout=2))

    assert attempts == 2
