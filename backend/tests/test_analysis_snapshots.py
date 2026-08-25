from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.analysis.history.snapshots import bars_hash, create_input_snapshot, create_review_input_snapshot
from app.analysis.tasks.models import AnalysisTask, AnalysisTaskCreate
from app.core.models import Bar


def test_bar_hash_normalizes_timezone_equivalent_timestamps() -> None:
    utc = Bar(timestamp=datetime(2026, 8, 12, 1, tzinfo=timezone.utc), open=1, high=2, low=0, close=1.5, volume=3)
    same = Bar.model_validate({**utc.model_dump(), "timestamp": "2026-08-12T09:00:00+08:00"})

    assert bars_hash([utc]) == bars_hash([same])


def test_bar_hash_changes_with_market_values() -> None:
    first = Bar(timestamp=datetime(2026, 8, 12, 1, tzinfo=timezone.utc), open=1, high=2, low=0, close=1.5, volume=3)
    changed = first.model_copy(update={"close": 1.6})

    assert bars_hash([first]) != bars_hash([changed])


@pytest.mark.anyio
async def test_latest_market_snapshot_freezes_latest_100_closed_bars() -> None:
    now = datetime(2026, 8, 12, 12, 3, tzinfo=timezone.utc)
    task = AnalysisTask(id=uuid.uuid4(), kind="analysis", title="最新行情", description="", status="pending", config_json={"symbol": "ES", "period": "5m"})
    latest_closed_open = now.replace(minute=0) - timedelta(minutes=5)
    bars = [Bar(timestamp=latest_closed_open - timedelta(minutes=5 * offset), open=1, high=2, low=0, close=1.5, volume=3) for offset in reversed(range(100))]

    class Provider:
        def __init__(self): self.query = None
        async def get_range(self, query): self.query = query; return bars
    class Repository:
        async def add_snapshot(self, snapshot): return snapshot

    provider = Provider()
    snapshot = await create_input_snapshot(None, task, provider, Repository(), now=now)

    assert provider.query.end == datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    assert len(snapshot.bars_json) == 100
    assert snapshot.query_json["start"] == bars[0].timestamp.isoformat().replace("+00:00", "Z")
    assert snapshot.query_json["end"] == datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.mark.anyio
async def test_review_snapshot_freezes_every_trade_period_input() -> None:
    trade_id = uuid.uuid4()
    task = AnalysisTask(
        id=uuid.uuid4(), kind="review", title="复盘", description="", status="pending",
        config_json={"selected_trade_ids": [str(trade_id)], "periods": ["5m", "1h"]},
    )
    trade = SimpleNamespace(
        id=trade_id, symbol_root="ES", entered_at=datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        exited_at=datetime(2026, 8, 12, 2, tzinfo=timezone.utc), direction="long",
        entry_price=100, exit_price=101, size=1, reported_pnl=1,
    )
    bar = Bar(timestamp=datetime(2026, 8, 12, 1, tzinfo=timezone.utc), open=100, high=102, low=99, close=101, volume=3)

    class Provider:
        def __init__(self): self.queries = []
        async def get_range(self, query):
            self.queries.append(query)
            return [bar]

    class Repository:
        def __init__(self): self.snapshot = None
        async def add_snapshot(self, snapshot): self.snapshot = snapshot; return snapshot

    provider, repository = Provider(), Repository()
    snapshot = await create_review_input_snapshot(None, task, [trade], provider, repository)

    children = snapshot.query_json["children"]
    assert len(provider.queries) == 2
    assert [(child["trade_id"], child["period"]) for child in children] == [(str(trade_id), "5m"), (str(trade_id), "1h")]
    assert all(child["query"]["analysis_mode"] == "trade_review" for child in children)
    assert all(child["bars"] == [bar.model_dump(mode="json")] for child in children)
