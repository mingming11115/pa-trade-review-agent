"""行情数据服务模块。

负责 K 线数据的采集、存储、聚合、覆盖率计算、回填以及价格告警规则的评估与触发。
核心设计理念为"本地优先"：优先从本地数据库读取已有 K 线，缺失时再回源上游并回写。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import KLINE_SYMBOLS, Settings, get_settings
from app.core.database import Base, SessionFactory, ensure_schema, engine
from app.core.models import Bar, HistoricalQuery, Period
from app.analysis.workflow.stage1.core.bar_identity import enrich_api_bars
from app.market.provider import MassiveHistoricalProvider
from app.core.errors import ProviderError
from app.core.logging_context import get_request_id, set_trace_context, reset_trace_context, bind_trace_fields


logger = logging.getLogger(__name__)
UTC = timezone.utc
COLLECTOR_LOCK_ID = 724019222
HISTORICAL_BACKFILL_TIMEOUT_SECONDS = 120
STORED_PERIODS = {"1m", "1h"}
PERIOD_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
# 与前端 feed.ANALYSIS_LOOKBACK_BARS 保持一致，用于实时快照回看窗口。
ANALYSIS_LOOKBACK_BARS = 80
SOURCE_PERIOD = {"1m": "1m", "5m": "1m", "15m": "1m", "30m": "1m", "1h": "1h", "4h": "1h", "1d": "1h"}
_CONTRACT_ROOT = re.compile(r"^([A-Z0-9]+?)(?:[FGHJKMNQUVXZ]\d{1,2})?$")


def _extract_symbol_root(symbol: str) -> str:
    """Strip month/year code so ESU6 / ESZ25 → ES, MNQU6 → MNQ."""
    normalized = symbol.strip().upper()
    match = _CONTRACT_ROOT.fullmatch(normalized)
    return match.group(1) if match else normalized


def validate_kline_symbol(symbol: str) -> str:
    """Normalize concrete contracts to their own root, preserving micros."""
    root = _extract_symbol_root(symbol)
    storage = root
    if storage not in KLINE_SYMBOLS:
        raise ValueError("K 线仅支持 ES、NQ、MES、MNQ、MGC")
    return storage


class CollectionState(Base):
    __tablename__ = "market_collection_states"

    symbol: Mapped[str] = mapped_column(String(100), primary_key=True)
    covered_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="idle")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


class ScheduledJobRun(Base):
    __tablename__ = "scheduled_job_runs"
    __table_args__ = (UniqueConstraint("job_key", "scheduled_for", name="uq_scheduled_job_slot"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_key: Mapped[str] = mapped_column(String(160), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(100), index=True)
    period: Mapped[str] = mapped_column(String(10))
    trigger_type: Mapped[str] = mapped_column(String(30), default="bar_close")
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class AlertRecord(Base):
    __tablename__ = "alert_records"
    __table_args__ = (UniqueConstraint("rule_id", "bar_opened_at", "signal_key", name="uq_alert_signal"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(index=True)
    bar_opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    signal_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(240))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    delivery_status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Coverage(BaseModel):
    source_period: str
    expected_bars: int
    actual_bars: int
    complete: bool
    missing_buckets: list[datetime] = Field(default_factory=list)


class MarketRange(BaseModel):
    symbol: str
    period: str
    bars: list[Bar]
    coverage: Coverage


class CollectionStatus(BaseModel):
    symbol: str
    latest_closed_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    status: str
    last_error: str | None
    consecutive_failures: int
    stale_seconds: int | None


class AlertRuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    symbol: str = Field(min_length=1, max_length=100)
    period: str = Field(pattern="^(1m|5m|15m|30m|1h|4h|1d)$")
    trigger_type: Literal["bar_close", "interval"] = "bar_close"
    threshold: float = Field(ge=0)
    enabled: bool = True


class AlertRulePublic(AlertRuleInput):
    id: uuid.UUID
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class AlertPublic(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    bar_opened_at: datetime
    signal_key: str
    title: str
    message: str
    evidence: dict[str, Any] | None
    is_read: bool
    delivery_status: str
    created_at: datetime
    model_config = {"from_attributes": True}


def floor_bucket(value: datetime, minutes: int) -> datetime:
    """将时间向下取整到指定分钟周期的桶起始时间（UTC）。"""
    utc_value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    epoch_minutes = int(utc_value.timestamp()) // 60
    return datetime.fromtimestamp((epoch_minutes // minutes) * minutes * 60, tz=UTC)


def closed_boundary(now: datetime, period: str = "1m") -> datetime:
    """返回当前时间对应的最近已收盘 K 线桶起始时间。"""
    return floor_bucket(now, PERIOD_MINUTES[period])


def aggregate_bars(
    bars: Sequence[Bar],
    period: str,
    *,
    now: datetime | None = None,
    include_partial: bool = False,
) -> list[Bar]:
    """将低周期 K 线聚合为高周期 K 线（如 1m→5m、1m→1h）。"""
    if period not in PERIOD_MINUTES:
        raise ValueError(f"unsupported period: {period}")
    minutes = PERIOD_MINUTES[period]
    boundary = closed_boundary(now or datetime.now(UTC), period)  # 当前未收盘桶边界
    buckets: dict[datetime, list[Bar]] = {}
    for bar in sorted(bars, key=lambda item: item.timestamp):
        opened_at = floor_bucket(bar.timestamp, minutes)
        if not include_partial and opened_at >= boundary:
            continue  # 跳过未收盘桶
        buckets.setdefault(opened_at, []).append(bar)
    result: list[Bar] = []
    for opened_at, items in sorted(buckets.items()):
        volumes = [item.volume for item in items if item.volume is not None]
        result.append(Bar(timestamp=opened_at, open=items[0].open, high=max(item.high for item in items), low=min(item.low for item in items), close=items[-1].close, volume=sum(volumes) if volumes else None))
    return result


def calculate_coverage(bars: Sequence[Bar], start: datetime, end: datetime, source_period: str) -> Coverage:
    """计算指定时间范围内的 K 线覆盖情况：预期数量、实际数量、缺失桶列表。"""
    minutes = PERIOD_MINUTES[source_period]
    cursor = floor_bucket(start, minutes)
    boundary = floor_bucket(end, minutes)
    actual = {floor_bucket(bar.timestamp, minutes) for bar in bars}
    expected: list[datetime] = []
    while cursor < boundary:
        expected.append(cursor)
        cursor += timedelta(minutes=minutes)
    missing = [bucket for bucket in expected if bucket not in actual]
    return Coverage(source_period=source_period, expected_bars=len(expected), actual_bars=len(actual & set(expected)), complete=not missing, missing_buckets=missing[:500])


def find_missing_session_buckets(
    bars: Sequence[Bar],
    period: str,
    symbol: str,
    *,
    limit: int = 500,
) -> list[datetime]:
    """Return open times missing between consecutive bars within the same session day.

    Weekend / daily maintenance gaps that cross CME or RTH session boundaries are
    ignored; only holes inside an active session day are treated as incomplete data.
    """
    if period not in PERIOD_MINUTES or len(bars) < 2:
        return []
    from app.analysis.workflow.stage1.core.bar_identity import _session_key, session_for_symbol

    minutes = PERIOD_MINUTES[period]
    step = timedelta(minutes=minutes)
    session = session_for_symbol(symbol)
    missing: list[datetime] = []
    ordered = sorted(bars, key=lambda item: item.timestamp)
    for previous, current in zip(ordered, ordered[1:]):
        prev_ts = floor_bucket(previous.timestamp, minutes)
        curr_ts = floor_bucket(current.timestamp, minutes)
        if curr_ts <= prev_ts:
            continue
        if _session_key(prev_ts, session) != _session_key(curr_ts, session):
            continue
        cursor = prev_ts + step
        while cursor < curr_ts:
            missing.append(cursor)
            if len(missing) >= limit:
                return missing
            cursor += step
    return missing


async def read_stored_bars(symbol: str, period: str, start: datetime, end: datetime) -> list[Bar]:
    """Read every market consumer from the unified archive."""
    from app.market.archive import read_archive_bars
    return await read_archive_bars(validate_kline_symbol(symbol),period,start,end)


async def query_market_range(
    symbol: str,
    period: str,
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    include_partial: bool = False,
) -> MarketRange:
    """查询指定标的和周期的 K 线数据，自动从源周期聚合并计算覆盖率。"""
    from app.market.archive import archive_range_is_covered

    source_period = SOURCE_PERIOD[period]
    logger.info(
        "market_bars query_market_range request_id=%s symbol=%s period=%s source_period=%s start=%s end=%s include_partial=%s",
        get_request_id(),
        symbol,
        period,
        source_period,
        start.isoformat(),
        end.isoformat(),
        include_partial,
    )
    source_start = floor_bucket(start, PERIOD_MINUTES[source_period])
    coverage_end = min(end, closed_boundary(now or datetime.now(UTC), source_period))
    # Read committed proof first so a later backfill commit cannot make an older
    # empty bar snapshot appear complete. Proof only covers the closed prefix.
    archive_covered = source_start < coverage_end and await archive_range_is_covered(
        validate_kline_symbol(symbol), source_start, coverage_end,
    )
    source_bars = await read_stored_bars(symbol, source_period, source_start, end)
    coverage = calculate_coverage(source_bars, start, coverage_end, source_period)
    if not coverage.complete and archive_covered:
        # Downloaded empty sessions are complete even when wall-clock buckets have
        # no trades. Keep counts unchanged and clear the false missing-bar report.
        coverage = coverage.model_copy(update={"complete": True, "missing_buckets": []})
    bars = source_bars if period == source_period else aggregate_bars(source_bars, period, now=now, include_partial=include_partial)
    bars = [bar for bar in bars if bar.timestamp >= start and bar.timestamp < end
        and (include_partial or bar.timestamp < closed_boundary(now or datetime.now(UTC),period))]
    logger.info(
        "market_bars query_market_range result request_id=%s symbol=%s period=%s source_bars=%d returned_bars=%d coverage_expected=%d coverage_actual=%d coverage_complete=%s missing=%d",
        get_request_id(),
        symbol,
        period,
        len(source_bars),
        len(bars),
        coverage.expected_bars,
        coverage.actual_bars,
        coverage.complete,
        len(coverage.missing_buckets),
    )
    return MarketRange(
        symbol=symbol,
        period=period,
        bars=enrich_api_bars(bars, symbol=symbol, timeframe=period),
        coverage=coverage,
    )


async def backfill_range(provider: MassiveHistoricalProvider, query: HistoricalQuery) -> MarketRange:
    """Backfill exact continuous records into the single minute source."""
    from app.market.archive import write_archive_coverage, write_archive_records

    symbol = validate_kline_symbol(query.symbol)
    source_start = floor_bucket(query.start, PERIOD_MINUTES[SOURCE_PERIOD[query.period.value]])
    try:
        # Bound interactive retries, writes and the final read as one request.
        # Long-running collectors continue using get_archive_range directly.
        async with asyncio.timeout(HISTORICAL_BACKFILL_TIMEOUT_SECONDS):
            batch = await provider.get_archive_range(symbol, source_start, query.end)
            async with SessionFactory.begin() as session:
                await write_archive_records(session, batch.records)
                if batch.covered_until is not None:
                    await write_archive_coverage(session, symbol, source_start, min(query.end, batch.covered_until))
            return await query_market_range(symbol, query.period.value, query.start, query.end)
    except TimeoutError as exc:
        logger.warning(
            "market_bars backfill_timeout request_id=%s symbol=%s period=%s start=%s end=%s timeout_seconds=%s",
            get_request_id(), symbol, query.period.value, query.start.isoformat(), query.end.isoformat(),
            HISTORICAL_BACKFILL_TIMEOUT_SECONDS, exc_info=True,
        )
        raise ProviderError("provider_timeout", "Historical backfill timed out", 504) from exc


class LocalFirstMarketProvider:
    """本地优先的市场数据提供者：先查本地数据库，缺失时回源上游并回写。"""

    def __init__(self, upstream: MassiveHistoricalProvider):
        self.upstream = upstream

    async def get_range(self, query: HistoricalQuery) -> list[Bar]:
        """获取指定查询范围的 K 线：本地完整则直接返回，否则回源补数据。

        回源失败时退化为本地已有数据，避免实时轮询因上游短暂不可用而整体失败。
        """
        await ensure_schema()
        storage_symbol = validate_kline_symbol(query.symbol)
        local_query = query.model_copy(update={"symbol": storage_symbol})
        logger.info(
            "market_bars provider_lookup request_id=%s symbol=%s period=%s start=%s end=%s",
            get_request_id(),
            storage_symbol,
            query.period.value,
            query.start.isoformat(),
            query.end.isoformat(),
        )
        local = await query_market_range(storage_symbol, query.period.value, query.start, query.end)
        if local.coverage.complete:
            logger.info(
                "market_bars provider_hit request_id=%s symbol=%s period=%s bars=%d",
                get_request_id(),
                storage_symbol,
                query.period.value,
                len(local.bars),
            )
            return local.bars
        try:
            logger.info(
                "market_bars provider_miss request_id=%s symbol=%s period=%s local_bars=%d coverage_complete=%s",
                get_request_id(),
                storage_symbol,
                query.period.value,
                len(local.bars),
                local.coverage.complete,
            )
            result = await backfill_range(self.upstream, local_query)
            logger.info(
                "market_bars provider_backfilled request_id=%s symbol=%s period=%s bars=%d",
                get_request_id(),
                storage_symbol,
                query.period.value,
                len(result.bars),
            )
            return result.bars
        except ProviderError:
            if local.bars:
                logger.warning("upstream backfill failed, serving local data symbol=%s bars=%d", storage_symbol, len(local.bars))
                return local.bars
            raise


class MinuteCollector:
    """分钟级 K 线采集器：定时从上游拉取最新 1m K 线并写入本地。"""

    def __init__(self, settings: Settings | None = None, provider_factory: Callable[[], MassiveHistoricalProvider] | None = None):
        self.settings = settings or get_settings()
        self.provider_factory = provider_factory or (lambda: MassiveHistoricalProvider(self.settings))
        self._stop = asyncio.Event()
        self.last_round_errors = 0

    def _collection_window(self, latest_closed_at: datetime | None, end: datetime) -> tuple[datetime, datetime]:
        """计算一次采集的时间窗口 [start, chunk_end)。

        总是重叠最近的 lookback 窗口以修补中途空洞，
        并限制单次上游拉取的跨度，多日追赶分批进行。
        """
        lookback = timedelta(minutes=self.settings.collector_lookback_minutes)
        heal_start = end - lookback
        catchup_start = latest_closed_at or heal_start
        start = min(catchup_start, heal_start)
        max_span = timedelta(minutes=self.settings.collector_max_catchup_minutes)
        chunk_end = min(end, start + max_span)
        return start, chunk_end

    async def _finish_failure(self, symbol: str, run_id: uuid.UUID, status: str, error: str) -> None:
        async with SessionFactory.begin() as session:
            state = await session.get(CollectionState,symbol)
            run = await session.get(ScheduledJobRun,run_id)
            if state:
                state.status = status
                state.last_error = error
                state.consecutive_failures = (state.consecutive_failures or 0)+1
            if run:
                run.status=status
                run.completed_at=datetime.now(UTC)
                run.detail={**(run.detail or {}),"error":error}

    async def collect_symbol(self, symbol: str, now: datetime | None = None) -> int:
        """Persist a slot, fetch raw records, and commit data plus coverage atomically."""
        from app.market.archive import initial_coverage, write_archive_records
        current=now or datetime.now(UTC)
        interval=getattr(self.settings,"collector_interval_seconds",60)
        slot=datetime.fromtimestamp(int(current.timestamp())//interval*interval,UTC)
        run_id=uuid.uuid4()
        async with SessionFactory.begin() as session:
            claimed=await session.scalar(pg_insert(ScheduledJobRun).values(
                id=run_id,job_key=f"market-minute:{symbol}",scheduled_for=slot,
                status="running",started_at=current,detail={"symbol":symbol,"trace_id":get_request_id()}
                ).on_conflict_do_nothing(index_elements=[ScheduledJobRun.job_key,ScheduledJobRun.scheduled_for]).returning(ScheduledJobRun.id))
            if not claimed:
                return 0
            state=await session.get(CollectionState,symbol)
            if state is None:
                state=CollectionState(symbol=symbol,consecutive_failures=0)
                session.add(state)
            if state.consecutive_failures and state.last_attempt_at:
                cooldown=min(900,60*2**min(state.consecutive_failures,4))
                if (current-state.last_attempt_at).total_seconds()<cooldown:
                    run=await session.get(ScheduledJobRun,run_id)
                    run.status="deferred"
                    run.completed_at=current
                    run.detail={**(run.detail or {}),"reason":"failure_backoff"}
                    return 0
            latest=state.covered_until
            if latest is None:
                initial=await initial_coverage(session,symbol)
                candidates=[value for value in (initial,state.latest_closed_at) if value is not None]
                latest=max(candidates) if candidates else None
            state.last_attempt_at=current
            state.status="running"
        end=closed_boundary(current,"1m")
        start,chunk_end=self._collection_window(latest,end)
        with bind_trace_fields(run_id=str(run_id)):
            try:
                async with SessionFactory.begin() as session:
                    run=await session.get(ScheduledJobRun,run_id)
                    run.detail={**(run.detail or {}),"start":start.isoformat(),"end":chunk_end.isoformat()}
                timeout=max(1.0,self.settings.hist_timeout_seconds*(self.settings.hist_max_retries+1)+5)
                async with asyncio.timeout(timeout):
                    batch=await self.provider_factory().get_archive_range(symbol,start,chunk_end)
                async with SessionFactory.begin() as session:
                    state=await session.get(CollectionState,symbol)
                    run=await session.get(ScheduledJobRun,run_id)
                    count=await write_archive_records(session,batch.records)
                    if batch.covered_until is not None:
                        state.covered_until=max(state.covered_until or batch.covered_until,batch.covered_until)
                        if batch.records:
                            state.latest_closed_at=max(state.latest_closed_at or start,max(record.timestamp_utc for record in batch.records)+timedelta(minutes=1))
                        state.last_success_at=current
                        state.status="ok" if batch.records else "no_data"
                        state.last_error=None
                        state.consecutive_failures=0
                        run.status="success"
                    else:
                        state.status="waiting_for_provider"
                        state.last_error=None
                        run.status="waiting_for_provider"
                    run.completed_at=datetime.now(UTC)
                    run.detail={**(run.detail or {}),"rows":count,
                        "covered_until":batch.covered_until.isoformat() if batch.covered_until else None}
                logger.info("archive collection_finished symbol=%s rows=%d status=%s trace_id=%s run_id=%s",
                    symbol,count,run.status,get_request_id(),run_id)
                return count
            except asyncio.CancelledError:
                cleanup=asyncio.create_task(self._finish_failure(symbol,run_id,"cancelled","cancelled"))
                await asyncio.shield(cleanup)
                raise
            except Exception as exc:
                error=exc.code if isinstance(exc,ProviderError) else type(exc).__name__
                status="timed_out" if isinstance(exc,TimeoutError) or error=="provider_timeout" else "failed"
                await self._finish_failure(symbol,run_id,status,error)
                raise

    async def run_once(self, now: datetime | None = None) -> None:
        """One global DB lease across embedded and external worker processes."""
        from app.market.archive import table
        await ensure_schema()
        tokens=set_trace_context(str(uuid.uuid4()))
        try:
            # Transaction-scoped lock releases on connection loss and process exit.
            async with engine.begin() as lease:
                if not (await lease.execute(text("SELECT pg_try_advisory_xact_lock(CAST(:lock_id AS bigint))"), {"lock_id":COLLECTOR_LOCK_ID})).scalar_one():
                    logger.info("archive collection_skipped reason=worker_lock trace_id=%s",get_request_id())
                    return
                current=now or datetime.now(UTC)
                async with SessionFactory.begin() as session:
                    await session.execute(update(ScheduledJobRun).where(
                        ScheduledJobRun.job_key.like("market-minute:%"),ScheduledJobRun.status=="running"
                        ).values(status="interrupted",completed_at=current))
                    await session.execute(update(CollectionState).where(CollectionState.status=="running"
                        ).values(status="interrupted",last_error="worker_interrupted"))
                    await session.execute(text(f"""DELETE FROM {table('live_events')} e
                      WHERE e.timestamp_utc<:cutoff AND EXISTS (
                        SELECT 1 FROM {table('bars')} b WHERE b.root=e.root
                        AND b.timestamp_utc=date_trunc('minute',e.timestamp_utc)
                        AND b.confirmed AND b.instrument_id>0)"""),
                        {"cutoff":current-timedelta(days=1)})
                interval=getattr(self.settings,"collector_interval_seconds",60)
                slot=datetime.fromtimestamp(int(current.timestamp())//interval*interval,UTC)
                async with SessionFactory() as session:
                    existing=set((await session.scalars(select(ScheduledJobRun.job_key).where(
                        ScheduledJobRun.scheduled_for==slot,ScheduledJobRun.job_key.like("market-minute:%")))).all())
                performed=False
                self.last_round_errors=0
                for symbol in self.settings.collector_symbols:
                    if f"market-minute:{symbol}" in existing:
                        continue
                    # Slots are claimed independently: one symbol's failure never blocks another.
                    try:
                        await self.collect_symbol(symbol,current)
                        performed=True
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        performed=True
                        self.last_round_errors+=1
                        logger.exception("archive collection_failed symbol=%s trace_id=%s",symbol,get_request_id())
                if performed:
                    await evaluate_alert_rules(current)
        finally:
            reset_trace_context(tokens)

    async def run_forever(self) -> None:
        """Run all targets periodically; transient DB failures do not kill the service."""
        while not self._stop.is_set():
            started=datetime.now(UTC)
            try:
                await self.run_once(started)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("archive round_failed")
            delay=max(1.0,getattr(self.settings,"collector_interval_seconds",60)-(datetime.now(UTC)-started).total_seconds())
            try:
                await asyncio.wait_for(self._stop.wait(),timeout=delay)
            except TimeoutError:
                pass

    def stop(self) -> None:
        """设置停止标志，通知采集循环退出。"""
        self._stop.set()


async def collection_statuses(symbols: Sequence[str] | None = None) -> list[CollectionStatus]:
    """查询所有（或指定）标的的采集状态，包含延迟秒数。"""
    selected = list(symbols or get_settings().collector_symbols)
    async with SessionFactory() as session:
        states = (await session.scalars(select(CollectionState).where(CollectionState.symbol.in_(selected)))).all() if selected else []
    by_symbol = {state.symbol: state for state in states}
    now = datetime.now(UTC)
    result = []
    for symbol in selected:
        state = by_symbol.get(symbol)
        latest = state.latest_closed_at if state else None
        result.append(CollectionStatus(symbol=symbol, latest_closed_at=latest, last_attempt_at=state.last_attempt_at if state else None, last_success_at=state.last_success_at if state else None, status=state.status if state else "never_run", last_error=state.last_error if state else None, consecutive_failures=state.consecutive_failures if state else 0, stale_seconds=max(0, int((now - latest).total_seconds())) if latest else None))
    return result


async def create_alert_rule(payload: AlertRuleInput) -> AlertRulePublic:
    """创建新的价格预警规则。"""
    await ensure_schema()
    async with SessionFactory() as session:
        values = payload.model_dump()
        values["symbol"] = payload.symbol.upper()
        rule = AlertRule(**values)
        session.add(rule); await session.commit(); await session.refresh(rule)
        return AlertRulePublic.model_validate(rule)


async def list_alert_rules() -> list[AlertRulePublic]:
    """列出所有价格预警规则（按创建时间倒序）。"""
    await ensure_schema()
    async with SessionFactory() as session:
        rows = (await session.scalars(select(AlertRule).order_by(AlertRule.created_at.desc()))).all()
    return [AlertRulePublic.model_validate(row) for row in rows]


async def update_alert_rule(rule_id: uuid.UUID, payload: AlertRuleInput) -> AlertRulePublic:
    """更新指定 ID 的价格预警规则。"""
    await ensure_schema()
    async with SessionFactory() as session:
        rule = await session.get(AlertRule, rule_id)
        if rule is None:
            from app.core.errors import AppError
            raise AppError("alert_rule_not_found", "告警规则不存在", 404)
        for key, value in payload.model_dump().items(): setattr(rule, key, value.upper() if key == "symbol" else value)
        rule.updated_at = datetime.now(UTC); await session.commit(); await session.refresh(rule)
        return AlertRulePublic.model_validate(rule)


async def remove_alert_rule(rule_id: uuid.UUID) -> None:
    """删除指定 ID 的价格预警规则。"""
    await ensure_schema()
    async with SessionFactory() as session:
        rule = await session.get(AlertRule, rule_id)
        if rule: await session.delete(rule); await session.commit()


async def list_alerts(limit: int = 200) -> list[AlertPublic]:
    """查询最近的触发预警记录（按创建时间倒序）。"""
    await ensure_schema()
    async with SessionFactory() as session:
        rows = (await session.scalars(select(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(limit))).all()
    return [AlertPublic.model_validate(row) for row in rows]


async def mark_alert_read(alert_id: uuid.UUID) -> AlertPublic:
    """将指定预警标记为已读。"""
    await ensure_schema()
    async with SessionFactory() as session:
        alert = await session.get(AlertRecord, alert_id)
        if alert is None:
            from app.core.errors import AppError
            raise AppError("alert_not_found", "告警不存在", 404)
        alert.is_read = True; await session.commit(); await session.refresh(alert)
        return AlertPublic.model_validate(alert)


async def evaluate_alert_rules(now: datetime) -> None:
    """评估所有启用的告警规则：检查最新 K 线是否触发阈值或交易信号，并生成告警记录。"""
    await ensure_schema()
    async with SessionFactory() as session:
        rules = (await session.scalars(select(AlertRule).where(AlertRule.enabled.is_(True)))).all()
    for rule in rules:
        end = closed_boundary(now, rule.period)
        start = end - timedelta(minutes=PERIOD_MINUTES[rule.period] * 3)
        market_range = await query_market_range(rule.symbol, rule.period, start, end, now=now)
        if not market_range.bars:
            continue
        latest = market_range.bars[-1]
        change = 0.0 if latest.open == 0 else abs((latest.close - latest.open) / latest.open * 100)
        slot = floor_bucket(latest.timestamp, PERIOD_MINUTES[rule.period])
        run_id = uuid.uuid4()
        async with SessionFactory() as session:
            statement = pg_insert(ScheduledJobRun).values(id=run_id, job_key=f"alert:{rule.id}", scheduled_for=slot, status="running", detail={"change_percent": change})
            inserted = await session.execute(statement.on_conflict_do_nothing(index_elements=[ScheduledJobRun.job_key, ScheduledJobRun.scheduled_for]).returning(ScheduledJobRun.id))
            if inserted.scalar_one_or_none() is None:
                await session.rollback(); continue
            await session.commit()

        from app.core.logging_context import get_trace_id, normalize_trace_id, reset_trace_context, set_trace_context
        trace_id = get_trace_id()
        tokens = set_trace_context(normalize_trace_id(None if trace_id == '-' else trace_id), run_id=str(run_id))
        try:
            analysis_result = None
            analysis_error = None
            try:
                from app.analysis.workflow.graph import run_demo_analysis_workflow
                from app.analysis.history.service import persist_analysis_result
                from app.knowledge.service import get_or_create_run_prompt_snapshot
                # Snapshot: last ANALYSIS_LOOKBACK_BARS closed bars ending at closed_boundary(now).
                # analysis_mode must stay "realtime" so history maps to the live workbench.
                analysis_query = HistoricalQuery(
                    symbol=rule.symbol,
                    period=Period(rule.period),
                    start=end - timedelta(minutes=PERIOD_MINUTES[rule.period] * ANALYSIS_LOOKBACK_BARS),
                    end=end,
                    analysis_mode="realtime",
                )
                # Scheduled rules currently have no user owner; use the ownerless scope.
                prompt_snapshot = await get_or_create_run_prompt_snapshot(run_id, None)
                analysis_result = await run_demo_analysis_workflow(
                    LocalFirstMarketProvider(MassiveHistoricalProvider(get_settings())), analysis_query,
                    run_id=run_id, prompt_snapshot=prompt_snapshot,
                )
                await persist_analysis_result(analysis_result)
            except Exception as exc:
                analysis_error = type(exc).__name__
                logger.exception("scheduled analysis failed for rule %s", rule.id)

            async with SessionFactory() as session:
                run = await session.get(ScheduledJobRun, run_id)
                if run:
                    run.status = "completed" if analysis_result else "failed"
                    run.completed_at = datetime.now(UTC)
                    run.detail = {"change_percent": change, "run_id": analysis_result.run_id if analysis_result else None, "error": analysis_error}
                rule_row = await session.get(AlertRule, rule.id)
                if rule_row: rule_row.last_run_at = now
                trade_signal = bool(analysis_result and analysis_result.stage2.terminal.outcome == "trade")
                if change >= rule.threshold or trade_signal:
                    direction = "up" if latest.close >= latest.open else "down"
                    signal_key = f"trade:{analysis_result.stage2.decision.direction}" if trade_signal else f"range:{direction}:{rule.threshold}"
                    reason = analysis_result.stage2.terminal.reason if trade_signal else f"已收盘 K 线振幅 {change:.2f}%，达到阈值 {rule.threshold:.2f}%"
                    alert_statement = pg_insert(AlertRecord).values(id=uuid.uuid4(), rule_id=rule.id, bar_opened_at=slot, signal_key=signal_key, title=f"{rule.symbol} {rule.period} {'交易信号' if trade_signal else '波动告警'}", message=reason, evidence={"open": latest.open, "close": latest.close, "change_percent": change, "run_id": analysis_result.run_id if analysis_result else None}, delivery_status="browser_pending")
                    await session.execute(alert_statement.on_conflict_do_nothing(index_elements=[AlertRecord.rule_id, AlertRecord.bar_opened_at, AlertRecord.signal_key]))
                await session.commit()
        finally:
            reset_trace_context(tokens)
