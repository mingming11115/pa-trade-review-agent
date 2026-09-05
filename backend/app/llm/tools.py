"""LLM tool registry: market data tools exposed to the model.

Tools are registered in a central ``_TOOL_REGISTRY`` and automatically
discovered by :func:`list_llm_tools` and :func:`execute_llm_tool`.  Each tool
is an instance of :class:`LLMTool` with:

* a unique ``name``
* an OpenAI-compatible JSON-schema ``parameters`` definition
* an async ``execute(**arguments)`` method returning a JSON-serializable dict

To add a new tool, create an :class:`LLMTool` subclass, implement ``execute``,
and register it at module level via ``_TOOL_REGISTRY.register(MyTool())``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.models import HistoricalQuery, Period
from app.market.provider import MassiveHistoricalProvider
from app.market.service import (
    LocalFirstMarketProvider,
    PERIOD_MINUTES,
    collection_statuses,
    query_market_range,
    validate_kline_symbol,
)
from app.analysis.history.service import get_analysis_history, list_analysis_history

UTC = timezone.utc

# ─── registry ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LLMToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        """将工具定义转换为 OpenAI function-calling 兼容的字典格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class LLMTool(ABC):
    """Base class for a registered tool."""

    name: str
    description: str
    parameters: dict[str, Any]

    @abstractmethod
    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """执行工具并返回可 JSON 序列化的结果字典。"""

    def definition(self) -> LLMToolDefinition:
        """返回当前工具的 :class:`LLMToolDefinition` 定义对象。"""
        return LLMToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )


class _ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, LLMTool] = {}

    def register(self, tool: LLMTool) -> LLMTool:
        """注册一个工具实例，若名称已存在则抛出 :class:`ValueError`。"""
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def names(self) -> list[str]:
        """返回所有已注册工具的名称列表。"""
        return list(self._tools)

    def definitions(self) -> list[LLMToolDefinition]:
        """返回所有已注册工具的定义列表。"""
        return [tool.definition() for tool in self._tools.values()]

    def get(self, name: str) -> LLMTool | None:
        """根据名称获取已注册的工具实例，不存在则返回 ``None``。"""
        return self._tools.get(name)


_TOOL_REGISTRY = _ToolRegistry()


# ─── shared helpers ──────────────────────────────────────────────────────────


def _provider() -> LocalFirstMarketProvider:
    """创建并返回一个本地优先的市场数据提供者实例。"""
    return LocalFirstMarketProvider(MassiveHistoricalProvider(get_settings()))


# ─── market.get_bars ─────────────────────────────────────────────────────────


class _MarketGetBarsInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=100)
    period: Period
    start: datetime
    end: datetime
    include_partial: bool = False


class MarketGetBarsTool(LLMTool):
    name = "market.get_bars"
    description = (
        "Fetch normalized K-line (OHLCV) bars for a symbol and time range. "
        "Bars are returned oldest-first with EMA20 and ATR14 indicators."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Market symbol such as ES or NQ."},
            "period": {
                "type": "string",
                "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
            },
            "start": {
                "type": "string",
                "format": "date-time",
                "description": "Inclusive start time in ISO-8601.",
            },
            "end": {
                "type": "string",
                "format": "date-time",
                "description": "Exclusive end time in ISO-8601.",
            },
            "include_partial": {
                "type": "boolean",
                "description": "Whether to include the current partial bucket.",
                "default": False,
            },
        },
        "required": ["symbol", "period", "start", "end"],
        "additionalProperties": False,
    }

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """获取指定标的和时间段内的标准化 K 线（OHLCV）数据，包含 EMA20 和 ATR14 指标。"""
        parsed = _MarketGetBarsInput.model_validate(arguments)
        resolved_symbol = validate_kline_symbol(parsed.symbol)
        query = HistoricalQuery(
            symbol=resolved_symbol,
            period=parsed.period,
            start=parsed.start,
            end=parsed.end,
        )
        provider = _provider()
        await provider.get_range(query)
        market = await query_market_range(
            resolved_symbol,
            parsed.period.value,
            parsed.start,
            parsed.end,
            include_partial=parsed.include_partial,
        )
        return {
            "tool": self.name,
            "symbol": parsed.symbol.upper(),
            "resolved_symbol": market.symbol,
            "period": market.period,
            "start": parsed.start.isoformat(),
            "end": parsed.end.isoformat(),
            "include_partial": parsed.include_partial,
            "bars": [bar.model_dump(mode="json") for bar in market.bars],
            "coverage": market.coverage.model_dump(mode="json"),
        }


# ─── market.get_latest_bars ──────────────────────────────────────────────────


class _MarketGetLatestBarsInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=100)
    period: Period = Period.minute_5
    count: int = Field(default=20, ge=1, le=500)


class MarketGetLatestBarsTool(LLMTool):
    name = "market.get_latest_bars"
    description = (
        "Fetch the most recent N closed K-line bars for a symbol and period. "
        "The window is computed relative to the current UTC time; only closed "
        "(finalized) bars are returned."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Market symbol such as ES or NQ."},
            "period": {
                "type": "string",
                "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                "default": "5m",
            },
            "count": {
                "type": "integer",
                "description": "Number of recent closed bars to return (1-500).",
                "default": 20,
                "minimum": 1,
                "maximum": 500,
            },
        },
        "required": ["symbol"],
        "additionalProperties": False,
    }

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """获取指定标的最近 N 根已收盘的 K 线数据，时间窗口基于当前 UTC 时间计算。"""
        parsed = _MarketGetLatestBarsInput.model_validate(arguments)
        resolved_symbol = validate_kline_symbol(parsed.symbol)
        period = parsed.period
        period_minutes = PERIOD_MINUTES[period.value]
        now = datetime.now(UTC)
        end = now - timedelta(minutes=period_minutes)
        start = end - timedelta(minutes=period_minutes * parsed.count)

        provider = _provider()
        query = HistoricalQuery(
            symbol=resolved_symbol,
            period=period,
            start=start,
            end=end,
        )
        await provider.get_range(query)
        market = await query_market_range(
            resolved_symbol,
            period.value,
            start,
            end,
        )
        bars = market.bars[-parsed.count:] if len(market.bars) > parsed.count else market.bars
        return {
            "tool": self.name,
            "symbol": parsed.symbol.upper(),
            "resolved_symbol": market.symbol,
            "period": period.value,
            "count": len(bars),
            "bars": [bar.model_dump(mode="json") for bar in bars],
            "coverage": market.coverage.model_dump(mode="json"),
        }


# ─── market.get_bar_count ────────────────────────────────────────────────────


class _MarketGetBarCountInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=100)
    period: Period = Period.minute_5
    start: datetime
    end: datetime
    include_partial: bool = False


class MarketGetBarCountTool(LLMTool):
    name = "market.get_bar_count"
    description = (
        "Count how many K-line bars exist in a given time range for a symbol "
        "and period, plus coverage metadata (expected vs actual bars, missing "
        "buckets). Useful for quickly checking data completeness without "
        "transferring full OHLC arrays."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Market symbol such as ES or NQ."},
            "period": {
                "type": "string",
                "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                "default": "5m",
            },
            "start": {
                "type": "string",
                "format": "date-time",
                "description": "Inclusive start time in ISO-8601.",
            },
            "end": {
                "type": "string",
                "format": "date-time",
                "description": "Exclusive end time in ISO-8601.",
            },
            "include_partial": {
                "type": "boolean",
                "description": "Whether to count the current partial bucket.",
                "default": False,
            },
        },
        "required": ["symbol", "start", "end"],
        "additionalProperties": False,
    }

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """统计指定标的和时间段内 K 线的数量及覆盖元数据（预期 vs 实际、缺失桶等）。"""
        parsed = _MarketGetBarCountInput.model_validate(arguments)
        resolved_symbol = validate_kline_symbol(parsed.symbol)
        provider = _provider()
        query = HistoricalQuery(
            symbol=resolved_symbol,
            period=parsed.period,
            start=parsed.start,
            end=parsed.end,
        )
        await provider.get_range(query)
        market = await query_market_range(
            resolved_symbol,
            parsed.period.value,
            parsed.start,
            parsed.end,
            include_partial=parsed.include_partial,
        )
        return {
            "tool": self.name,
            "symbol": parsed.symbol.upper(),
            "resolved_symbol": market.symbol,
            "period": market.period,
            "start": parsed.start.isoformat(),
            "end": parsed.end.isoformat(),
            "bar_count": len(market.bars),
            "coverage": market.coverage.model_dump(mode="json"),
        }


# ─── market.get_collection_status ────────────────────────────────────────────


class MarketGetCollectionStatusTool(LLMTool):
    name = "market.get_collection_status"
    description = (
        "Check the real-time market data collection state for all configured "
        "symbols (or a single symbol). Returns latest closed bar time, staleness, "
        "and error status for each symbol."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Optional specific symbol to query (e.g. ES or NQ). "
                "If omitted, returns status for all configured symbols.",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """查询所有已配置标的（或单个标的）的实时市场数据采集状态，包括最新收盘时间、延迟和错误信息。"""
        symbol = arguments.get("symbol")
        symbols = [validate_kline_symbol(symbol)] if symbol else None
        statuses = await collection_statuses(symbols=symbols)
        return {
            "tool": self.name,
            "symbol": symbol.upper() if symbol else None,
            "statuses": [
                {
                    "symbol": s.symbol,
                    "latest_closed_at": s.latest_closed_at.isoformat() if s.latest_closed_at else None,
                    "last_attempt_at": s.last_attempt_at.isoformat() if s.last_attempt_at else None,
                    "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
                    "status": s.status,
                    "last_error": s.last_error,
                    "consecutive_failures": s.consecutive_failures,
                    "stale_seconds": s.stale_seconds,
                }
                for s in statuses
            ],
        }


# ─── analysis.list_history ───────────────────────────────────────────────────


class _AnalysisHistoryListInput(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)
    symbol: str | None = Field(default=None, max_length=100)
    period: str | None = Field(default=None, pattern="^(1m|5m|15m|30m|1h|4h|1d)$")
    mode: str | None = Field(default=None, pattern="^(trade_review|historical|realtime)$")
    favorite: bool | None = None


class AnalysisListHistoryTool(LLMTool):
    name = "analysis.list_history"
    description = (
        "List recent analysis history summaries. Each item includes run_id, "
        "symbol, period, mode, status, direction, favorite, notes, tags, and timestamps. "
        "Optionally filter by symbol, period, mode, or favorite flag. Results are "
        "ordered newest-first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of records to return (1-200).",
                "default": 20,
                "minimum": 1,
                "maximum": 200,
            },
            "symbol": {
                "type": "string",
                "description": "Filter by market symbol such as ES or NQ.",
            },
            "period": {
                "type": "string",
                "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                "description": "Filter by K-line period.",
            },
            "mode": {
                "type": "string",
                "enum": ["trade_review", "historical", "realtime"],
                "description": "Filter by analysis mode.",
            },
            "favorite": {
                "type": "boolean",
                "description": "Filter by favorite flag.",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """列出近期的分析历史摘要，可按标的、周期、模式或收藏标志过滤，结果按时间倒序排列。"""
        parsed = _AnalysisHistoryListInput.model_validate(arguments)
        summaries = await list_analysis_history(
            limit=parsed.limit,
            symbol=parsed.symbol,
            period=parsed.period,
            mode=parsed.mode,
            favorite=parsed.favorite,
        )
        return {
            "tool": self.name,
            "count": len(summaries),
            "filters": {
                "symbol": parsed.symbol,
                "period": parsed.period,
                "mode": parsed.mode,
                "favorite": parsed.favorite,
                "limit": parsed.limit,
            },
            "items": [s.model_dump(mode="json") for s in summaries],
        }


# ─── analysis.get_detail ────────────────────────────────────────────────────


class AnalysisGetDetailTool(LLMTool):
    name = "analysis.get_detail"
    description = (
        "Get the full detail of a single analysis by its run_id. "
        "Returns the complete result JSON including stage1, stage2, decision, "
        "and the LLM transcript."
    )
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The unique analysis identifier.",
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    }

    async def execute(self, **arguments: Any) -> dict[str, Any]:
        """根据 run_id 获取单次分析的完整详情，包括 stage1、stage2、decision 及 LLM 对话记录。"""
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise AppError("llm_tool_arguments_invalid", "run_id 不能为空", 422)
        result = await get_analysis_history(run_id)
        return {
            "tool": self.name,
            "run_id": run_id,
            "result": result,
        }


# ─── registration ───────────────────────────────────────────────────────────

_TOOL_REGISTRY.register(MarketGetBarsTool())
_TOOL_REGISTRY.register(MarketGetLatestBarsTool())
_TOOL_REGISTRY.register(MarketGetBarCountTool())
_TOOL_REGISTRY.register(MarketGetCollectionStatusTool())
_TOOL_REGISTRY.register(AnalysisListHistoryTool())
_TOOL_REGISTRY.register(AnalysisGetDetailTool())


# ─── public API (unchanged interface) ────────────────────────────────────────

MARKET_BARS_TOOL_NAME = "market.get_bars"


def list_llm_tools() -> list[dict[str, Any]]:
    """返回所有已注册工具的 OpenAI 兼容定义列表。"""
    return [definition.to_openai() for definition in _TOOL_REGISTRY.definitions()]


async def execute_llm_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """根据工具名称执行对应的 LLM 工具，若工具不存在则抛出 ``AppError``。"""
    tool = _TOOL_REGISTRY.get(name)
    if tool is None:
        raise AppError("llm_tool_not_found", f"未知工具: {name}", 404)
    return await tool.execute(**arguments)
