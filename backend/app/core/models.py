from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Direction(str, Enum):
    bullish = "bullish"
    bearish = "bearish"
    neutral = "neutral"


class Period(str, Enum):
    minute_1 = "1m"
    minute_5 = "5m"
    minute_15 = "15m"
    minute_30 = "30m"
    hour_1 = "1h"
    hour_4 = "4h"
    day_1 = "1d"


class AnalysisTradeInput(BaseModel):
    trade_id: str
    symbol: str
    entered_at: datetime
    exited_at: datetime
    direction: Literal["long", "short"]
    entry_price: float
    exit_price: float
    size: float
    reported_pnl: float | None = None


class HistoricalQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, populate_by_name=True)

    dataset: str = Field(default="GLBX.MDP3", min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=100)
    period: Period = Period.minute_1
    provider_schema: str = Field(
        default="ohlcv-1m",
        alias="schema",
        serialization_alias="schema",
        min_length=1,
        max_length=50,
    )
    start: datetime
    end: datetime
    analysis_mode: Literal["trade_review", "historical", "realtime"] = "historical"
    trades: list[AnalysisTradeInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> "HistoricalQuery":
        """校验查询时间范围，并根据周期自动设置 provider_schema。"""
        if self.end <= self.start:
            raise ValueError("end must be later than start")
        self.provider_schema = (
            "ohlcv-1d" if self.period is Period.day_1 else "ohlcv-1m"
        )
        return self


class Bar(BaseModel):
    timestamp: datetime
    timeframe: str | None = None
    session: Literal["CME", "US_EQUITY_RTH"] | None = None
    day_index: int | None = Field(default=None, ge=1)
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def finite_numbers(cls, value: Optional[float]) -> Optional[float]:
        """校验数值为有限数（非 NaN、非无穷大）。"""
        if value is None:
            return value
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("value must be finite")
        return value

    @model_validator(mode="after")
    def validate_geometry(self) -> "Bar":
        """校验 K 线几何关系：high >= open/close/low，low <= open/close/high。"""
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to open, close and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to open, close and high")
        return self


class BasicAnalysis(BaseModel):
    bar_count: int
    start: datetime
    end: datetime
    first_open: float
    latest_close: float
    period_high: float
    period_low: float
    change_percent: float
    bullish_bars: int
    bearish_bars: int
    neutral_bars: int
    direction: Direction
    method: str


class DemoAnalysisResponse(BaseModel):
    query: HistoricalQuery
    resolved_symbol: str
    analysis: BasicAnalysis
    bars: list[Bar]
    analysis_id: str
    status: str
    snapshot: object
    stage1: object
    stage2: object
    review_result: list[object] | None = None
    audit: object


class HealthResponse(BaseModel):
    status: str
    api_version: str
    provider_configured: bool
    provider_transport: str
    storage_status: str
    auth_required: bool = False


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict[str, object]] = Field(default_factory=list)
