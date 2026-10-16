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
        json.dumps({"symbol": "GC.c.0", "ts_event": 1, "price": 1, "size": 1}),
        json.dumps({"symbol": "ES.c.0", "ts_event": 1, "price": 1}),
        json.dumps({"symbol": "ES.c.0", "ts_event": 1, "price": 1, "size": 0}),
    )

    assert [parse_live_trade(message) for message in messages] == [None] * len(messages)


import pytest
from test_market_archive import archive_db, record


@pytest.mark.asyncio
async def test_live_archive_deduplicates_and_preserves_chronological_open_close(archive_db,monkeypatch):
    archive,engine,factory=archive_db
    monkeypatch.setattr("app.market.live.SessionFactory",factory)
    later=LiveTrade(symbol="MES",timestamp=datetime(2026,9,30,10,50,20,tzinfo=UTC),price=102,size=2,event_key="later")
    earlier=LiveTrade(symbol="MES",timestamp=datetime(2026,9,30,10,50,10,tzinfo=UTC),price=100,size=3,event_key="earlier")
    await persist_live_trade(later)
    await persist_live_trade(earlier)
    await persist_live_trade(later)
    bars=await archive.read_archive_bars("MES","1m",datetime(2026,9,30,10,tzinfo=UTC),datetime(2026,9,30,11,tzinfo=UTC))
    assert len(bars)==1 and bars[0].open==100 and bars[0].close==102 and bars[0].volume==5
    async with factory.begin() as session:
        await archive.write_archive_records(session,[record()])
    await persist_live_trade(LiveTrade(symbol="MES",timestamp=later.timestamp,price=999,size=500,event_key="after-confirmed"))
    bars=await archive.read_archive_bars("MES","1m",datetime(2026,9,30,10,tzinfo=UTC),datetime(2026,9,30,11,tzinfo=UTC))
    assert bars[0].close==101.000000001 and bars[0].volume==7


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
