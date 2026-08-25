from __future__ import annotations

import json
import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

import websockets

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionFactory
from app.market.service import MarketBar


logger = logging.getLogger(__name__)


LIVE_SYMBOL_ROOTS = {"ES.c.0": "ES", "NQ.c.0": "NQ"}


@dataclass(frozen=True)
class LiveTrade:
    symbol: str
    timestamp: datetime
    price: float
    size: float


def subscription_payload(symbols: tuple[str, ...]) -> str:
    """构建 WebSocket 订阅消息的 JSON 字符串。"""
    return json.dumps(
        {
            "action": "subscribe",
            "dataset": "GLBX.MDP3",
            "schema": "trades",
            "stype_in": "continuous",
            "symbols": list(symbols),
        }
    )


def parse_live_trade(message: str | bytes) -> LiveTrade | None:
    """解析 WebSocket 逐笔成交消息，返回 LiveTrade 或 None（解析失败时）。"""
    try:
        payload = json.loads(message)
        if not isinstance(payload, dict):
            return None
        fields = payload.get("fields")
        is_record_envelope = isinstance(fields, dict)
        values = fields if is_record_envelope else payload
        matched_symbols = payload.get("matched_symbols")
        raw_symbol = (
            matched_symbols[0]
            if is_record_envelope and isinstance(matched_symbols, list) and matched_symbols
            else payload.get("symbol", "")
        )
        symbol = LIVE_SYMBOL_ROOTS.get(str(raw_symbol))
        header = values.get("hd") if isinstance(values.get("hd"), dict) else {}
        raw_timestamp = values.get("ts_event", header.get("ts_event"))
        price = float(values["price"])
        if is_record_envelope:
            price /= 1_000_000_000
        size = float(values["size"])
        timestamp = _parse_timestamp(raw_timestamp)
    except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return None
    if symbol is None or timestamp is None:
        return None
    if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
        return None
    return LiveTrade(symbol=symbol, timestamp=timestamp, price=price, size=size)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    if numeric > 1e17:
        numeric /= 1_000_000_000
    elif numeric > 1e14:
        numeric /= 1_000_000
    elif numeric > 1e11:
        numeric /= 1_000
    return datetime.fromtimestamp(numeric, tz=UTC)


async def persist_live_trade(trade: LiveTrade) -> None:
    opened_at = trade.timestamp.replace(second=0, microsecond=0)
    values = {
        "id": uuid.uuid4(),
        "symbol": trade.symbol,
        "period": "1m",
        "opened_at": opened_at,
        "open": trade.price,
        "high": trade.price,
        "low": trade.price,
        "close": trade.price,
        "volume": trade.size,
        "source": "websocket:trades",
        "updated_at": datetime.now(UTC),
    }
    statement = pg_insert(MarketBar).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[MarketBar.symbol, MarketBar.period, MarketBar.opened_at],
        set_={
            "high": func.greatest(MarketBar.high, statement.excluded.high),
            "low": func.least(MarketBar.low, statement.excluded.low),
            "close": statement.excluded.close,
            "volume": MarketBar.volume + statement.excluded.volume,
            "source": "websocket:trades",
            "updated_at": datetime.now(UTC),
        },
    )
    async with SessionFactory() as session:
        await session.execute(statement)
        await session.commit()


class RealtimeTradeCollector:
    def __init__(self, settings: Any, connect: Callable[..., Any] | None = None) -> None:
        self.settings = settings
        self.connect = connect or websockets.connect
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        retry_index = 0
        delays = (1, 2, 4, 8, 15)
        while not self._stop.is_set():
            try:
                async with self.connect(
                    self.settings.live_ws_url,
                    additional_headers={"x-api-key": self.settings.hist_api_key},
                    max_size=None,
                ) as websocket:
                    await websocket.recv()
                    await websocket.send(subscription_payload(self.settings.live_ws_symbols))
                    retry_index = 0
                    async for message in websocket:
                        if self._stop.is_set():
                            break
                        trade = parse_live_trade(message)
                        if trade is not None:
                            await persist_live_trade(trade)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "live market websocket disconnected error=%s detail=%s",
                    type(exc).__name__,
                    exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

            if self._stop.is_set():
                break
            delay = delays[min(retry_index, len(delays) - 1)]
            retry_index += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
