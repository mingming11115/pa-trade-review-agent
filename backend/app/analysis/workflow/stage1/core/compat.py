from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import ROOT_DIR


PROMPT_DIR = ROOT_DIR / "backend" / "app" / "analysis" / "workflow" / "prompt_engineering"
CYCLE_ENUM = ("spike", "micro_channel", "tight_channel", "normal_channel", "broad_channel", "trending_tr", "trading_range", "extreme_tr")


@dataclass
class ValidationSettings:
    normalization_mode: str = "lenient"
    disable_truncation_repair: bool = False
    stage1_coherence_checks: bool = False
    strict_bar_by_bar_features: bool = False
    trace_semantic_checks: bool = False
    retry_enabled: bool = True
    retry_max: int = 3
    retry_max_semantic: int = 1


@dataclass
class AnalysisRecord:
    stage1_diagnosis: dict[str, Any] | None = None
    stage1_response: Any = None
    kline_data: list[dict[str, Any]] | None = None


def normalize_stance(value: str | None) -> str:
    return value if value in {"conservative", "balanced", "aggressive", "extreme_aggressive"} else "conservative"


def build_decision_stance_guidance(value: str | None) -> str:
    return f"当前决策风格：{normalize_stance(value)}"


def format_epoch_for_display(value: float, short: bool = False) -> str:
    timestamp = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M" if short else "%Y-%m-%d %H:%M:%S")


def infer_price_tick_from_frame(frame: Any) -> float | None:
    prices = []
    for bar in getattr(frame, "bars", ()):
        prices.extend([float(bar.open), float(bar.high), float(bar.low), float(bar.close)])
    decimals = [abs(a - b) for a, b in zip(sorted(set(prices)), sorted(set(prices))[1:]) if abs(a - b) > 0]
    return min(decimals) if decimals else None


def format_validation_errors(
    invalid_fields: Any,
    *,
    missing_fields: list[str] | None = None,
    max_items: int = 6,
) -> str:
    invalid = list(invalid_fields or []) if isinstance(invalid_fields, (list, tuple)) else [str(invalid_fields)]
    missing = list(missing_fields or [])
    parts = [*(f"缺少 {item}" for item in missing), *(f"无效 {item}" for item in invalid)]
    return "；".join(parts[:max_items])
