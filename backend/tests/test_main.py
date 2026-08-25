import asyncio
from types import SimpleNamespace

import app.main as main_module


def test_lifespan_runs_and_stops_historical_and_realtime_collectors(monkeypatch):
    events = []

    class FakeCollector:
        def __init__(self, *_args, **_kwargs):
            self.stopped = asyncio.Event()
            self.name = type(self).__name__

        async def run_forever(self):
            events.append(f"run:{self.name}")
            await self.stopped.wait()

        def stop(self):
            events.append(f"stop:{self.name}")
            self.stopped.set()

    class HistoricalCollector(FakeCollector):
        pass

    class RealtimeCollector(FakeCollector):
        pass

    async def no_op(*_args):
        return None

    monkeypatch.setattr(main_module, "MinuteCollector", HistoricalCollector)
    monkeypatch.setattr(main_module, "RealtimeTradeCollector", RealtimeCollector)
    monkeypatch.setattr(main_module, "ensure_schema", no_op)
    monkeypatch.setattr(main_module, "bootstrap_admin", no_op)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            collector_enabled=True,
            collector_symbols=("ES", "NQ"),
            collector_lookback_minutes=30,
            collector_max_catchup_minutes=360,
            live_ws_enabled=True,
            hist_api_key="secret-value",
        ),
    )

    async def exercise():
        async with main_module.lifespan(None):
            await asyncio.sleep(0)

    asyncio.run(exercise())

    assert events == [
        "run:HistoricalCollector",
        "run:RealtimeCollector",
        "stop:HistoricalCollector",
        "stop:RealtimeCollector",
    ]


def test_lifespan_isolates_realtime_task_failure(monkeypatch):
    events = []

    class RealtimeCollector:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_forever(self):
            raise RuntimeError("stream failed")

        def stop(self):
            events.append("realtime-stopped")

    async def no_op(*_args):
        return None

    monkeypatch.setattr(main_module, "RealtimeTradeCollector", RealtimeCollector)
    monkeypatch.setattr(main_module, "ensure_schema", no_op)
    monkeypatch.setattr(main_module, "bootstrap_admin", no_op)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(
            collector_enabled=False,
            live_ws_enabled=True,
            hist_api_key="secret-value",
        ),
    )

    async def exercise():
        async with main_module.lifespan(None):
            await asyncio.sleep(0)
            events.append("api-alive")

    asyncio.run(exercise())

    assert events == ["api-alive", "realtime-stopped"]
