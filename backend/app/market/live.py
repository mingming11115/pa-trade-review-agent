from __future__ import annotations

import json
import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
from datetime import UTC, datetime
from typing import Any, Callable

import websockets

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionFactory
from app.market import archive


logger = logging.getLogger(__name__)


LIVE_SYMBOL_ROOTS = {f"{root}.c.0":root for root in ("ES","NQ","MES","MNQ","MGC")}


@dataclass(frozen=True)
class LiveTrade:
    symbol: str
    timestamp: datetime
    price: float
    size: float
    instrument_id: int = field(default=0,compare=False)
    publisher_id: int = field(default=0,compare=False)
    event_key: str = field(default="",compare=False)
    raw_price: int | None = field(default=None,compare=False)
    ts_event_ns: int | None = field(default=None,compare=False)


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
    raw_ns = int(raw_timestamp) if str(raw_timestamp).isdigit() and int(raw_timestamp)>1e17 else int(timestamp.timestamp()*1_000_000_000)
    raw_price = int(values["price"]) if is_record_envelope else int(Decimal(str(values["price"]))*1_000_000_000)
    try:
        instrument_id=int(values.get("instrument_id",header.get("instrument_id",0)))
        publisher_id=int(values.get("publisher_id",header.get("publisher_id",0)))
    except (ValueError,TypeError):
        return None
    # Exact envelopes (including sequence when supplied) deduplicate reconnect replays.
    event_key=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return LiveTrade(symbol=symbol, timestamp=timestamp, price=price, size=size,
        instrument_id=instrument_id,publisher_id=publisher_id,event_key=event_key,
        raw_price=raw_price,ts_event_ns=raw_ns)


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
    """Store provisional minutes in the archive without replay or order inflation."""
    minute=trade.timestamp.replace(second=0,microsecond=0)
    minute_ns=int(minute.timestamp())*1_000_000_000
    event_ns=trade.ts_event_ns or int(trade.timestamp.timestamp()*1_000_000_000)
    price=trade.raw_price if trade.raw_price is not None else int(Decimal(str(trade.price))*1_000_000_000)
    if trade.size!=int(trade.size):
        raise ValueError("Futures trade size must be an integer")
    event_key=trade.event_key or hashlib.sha256(f"{trade.symbol}:{trade.instrument_id}:{event_ns}:{price}:{trade.size}".encode()).hexdigest()
    params=dict(root=trade.symbol,event_key=event_key,timestamp=trade.timestamp,
        minute=minute,ns=minute_ns,event_ns=event_ns,price=price,size=int(trade.size),
        instrument=trade.instrument_id,publisher=trade.publisher_id,continuous=f"{trade.symbol}.c.0")
    async with SessionFactory.begin() as session:
        inserted=await session.scalar(text(f"""INSERT INTO {archive.table('live_events')}(root,event_key,timestamp_utc)
            VALUES (:root,:event_key,:timestamp) ON CONFLICT DO NOTHING RETURNING event_key"""),params)
        if inserted is None:
            return
        await session.execute(text(f"""INSERT INTO {archive.table('bars')}
           (root,continuous_symbol,instrument_id,ts_event_ns,timestamp_utc,open_raw,high_raw,
            low_raw,close_raw,volume,publisher_id,source,confirmed,live_first_ns,live_last_ns)
           SELECT CAST(:root AS varchar(3)),:continuous,:instrument,:ns,:minute,:price,:price,:price,:price,:size,:publisher,
             'websocket:trades',false,:event_ns,:event_ns
           WHERE NOT EXISTS (SELECT 1 FROM {archive.table('bars')}
             WHERE root=CAST(:root AS varchar(3)) AND ts_event_ns=:ns AND confirmed AND instrument_id>0)
           ON CONFLICT(root,ts_event_ns,instrument_id) DO UPDATE SET
             open_raw=CASE WHEN excluded.live_first_ns<bars.live_first_ns THEN excluded.open_raw ELSE bars.open_raw END,
             high_raw=greatest(bars.high_raw,excluded.high_raw), low_raw=least(bars.low_raw,excluded.low_raw),
             close_raw=CASE WHEN excluded.live_last_ns>=bars.live_last_ns THEN excluded.close_raw ELSE bars.close_raw END,
             volume=coalesce(bars.volume,0)+excluded.volume,
             live_first_ns=least(bars.live_first_ns,excluded.live_first_ns),
             live_last_ns=greatest(bars.live_last_ns,excluded.live_last_ns),updated_at=now()
           WHERE NOT bars.confirmed"""),params)


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
