from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.analysis.tasks.models import AnalysisTask, AnalysisTaskConfig, ReviewTaskConfig
from app.analysis.tasks.repository import AnalysisTaskRepository
from app.core.contracts import resolve_contract_symbol
from app.core.errors import AppError
from app.core.models import Bar, HistoricalQuery
from app.market.analysis import PERIOD_SECONDS
from app.market.service import closed_boundary

LATEST_ANALYSIS_BARS = 100

_SNAPSHOT_STORE: dict[uuid.UUID, "FrozenInputSnapshot"] = {}


@dataclass(slots=True)
class FrozenInputSnapshot:
    id: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID | None
    query_json: dict[str, object]
    resolved_symbol: str
    bars_json: list[dict[str, object]]
    bars_hash: str
    prompt_versions_json: dict[str, object]
    model_config_json: dict[str, object]
    confirmation_id: str
    expires_at: datetime
    created_at: datetime


def get_frozen_snapshot(snapshot_id: uuid.UUID) -> FrozenInputSnapshot | None:
    return _SNAPSHOT_STORE.get(snapshot_id)


def _normalized_bar(bar: Bar) -> dict[str, object]:
    timestamp = bar.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "timeframe": bar.timeframe,
        "session": bar.session,
        "day_index": bar.day_index,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def bars_hash(bars: list[Bar]) -> str:
    payload = json.dumps(
        [_normalized_bar(bar) for bar in bars],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def create_input_snapshot(owner_id: uuid.UUID | None, task: AnalysisTask, provider, repository: AnalysisTaskRepository, *, now: datetime | None = None) -> FrozenInputSnapshot:
    if task.kind != "analysis":
        raise ValueError("review snapshot creation is handled by the review runner")
    config = AnalysisTaskConfig.model_validate(task.config_json)
    current = now or datetime.now(timezone.utc)
    end = closed_boundary(current, config.period.value)
    start = end - timedelta(seconds=PERIOD_SECONDS[config.period] * (LATEST_ANALYSIS_BARS + 20))
    query = HistoricalQuery(symbol=config.symbol, period=config.period, start=start, end=end, analysis_mode="historical")
    resolved = resolve_contract_symbol(query.symbol, query.start, query.end)
    bars = await provider.get_range(query.model_copy(update={"symbol": resolved}))
    bars = sorted((bar for bar in bars if bar.timestamp < end), key=lambda bar: bar.timestamp)[-LATEST_ANALYSIS_BARS:]
    if len(bars) < LATEST_ANALYSIS_BARS:
        raise AppError("analysis_bars_insufficient", f"最新行情不足 {LATEST_ANALYSIS_BARS} 根已收盘 K 线", 422)
    query = query.model_copy(update={"start": bars[0].timestamp})
    snapshot = FrozenInputSnapshot(
        id=uuid.uuid4(),
        task_id=task.id,
        user_id=owner_id,
        query_json=query.model_dump(mode="json", by_alias=True),
        resolved_symbol=resolved,
        bars_json=[bar.model_dump(mode="json") for bar in bars],
        bars_hash=bars_hash(bars),
        prompt_versions_json={},
        model_config_json={},
        confirmation_id=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    _SNAPSHOT_STORE[snapshot.id] = snapshot
    return snapshot


async def create_review_input_snapshot(owner_id: uuid.UUID | None, task: AnalysisTask, trades, provider, repository: AnalysisTaskRepository) -> FrozenInputSnapshot:
    config = ReviewTaskConfig.model_validate(task.config_json)
    selected = {str(trade.id): trade for trade in trades}
    children: list[dict[str, object]] = []
    all_bars: list[Bar] = []
    for trade_id in config.selected_trade_ids:
        trade = selected[trade_id]
        trade_input = {
            "trade_id": str(trade.id),
            "symbol": trade.symbol_root,
            "entered_at": trade.entered_at,
            "exited_at": trade.exited_at,
            "direction": trade.direction,
            "entry_price": float(trade.entry_price),
            "exit_price": float(trade.exit_price),
            "size": float(trade.size),
            "reported_pnl": None if trade.reported_pnl is None else float(trade.reported_pnl),
        }
        for period in config.periods:
            query = HistoricalQuery(
                symbol=trade.symbol_root,
                period=period,
                start=trade.entered_at,
                end=trade.exited_at,
                analysis_mode="trade_review",
                trades=[trade_input],
            )
            resolved = resolve_contract_symbol(query.symbol, query.start, query.end)
            bars = await provider.get_range(query.model_copy(update={"symbol": resolved}))
            all_bars.extend(bars)
            children.append({
                "key": f"{trade.id}:{period.value}",
                "trade_id": str(trade.id),
                "period": period.value,
                "query": query.model_dump(mode="json", by_alias=True),
                "resolved_symbol": resolved,
                "bars": [bar.model_dump(mode="json") for bar in bars],
                "bars_hash": bars_hash(bars),
            })
    snapshot = FrozenInputSnapshot(
        id=uuid.uuid4(),
        task_id=task.id,
        user_id=owner_id,
        query_json={"kind": "review", "children": children},
        resolved_symbol="MULTI",
        bars_json=[],
        bars_hash=bars_hash(all_bars),
        prompt_versions_json={},
        model_config_json={},
        confirmation_id=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        created_at=datetime.now(timezone.utc),
    )
    _SNAPSHOT_STORE[snapshot.id] = snapshot
    return snapshot
