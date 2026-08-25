from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import anyio

from app.analysis.contracts import IncrementalDelta, PrecheckResult, Stage1Result
from app.llm.client import LLMResponse
from app.core.models import Bar, Direction
from app.analysis.workflow.stage1.adapter import (
    build_original_stage1_messages,
    build_stage1_frame,
    execute_original_stage1,
)
from app.analysis.workflow.stage1.core.bar_identity import BarRange, assign_bar_refs
from app.analysis.workflow.stage1.core.data.base import KlineFrame


def _bars(count: int = 60) -> list[Bar]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=start + timedelta(minutes=5 * index),
            open=100 + index,
            high=103 + index,
            low=99 + index,
            close=102 + index,
            volume=10,
        )
        for index in range(count)
    ]


def _base() -> Stage1Result:
    return Stage1Result(
        result_kind="live",
        precheck=PrecheckResult(passed=True, closed_bar_count=40),
        direction=Direction.bullish,
        confidence=60,
        gate_result="proceed",
        incremental_delta=IncrementalDelta(),
    )


def _valid_output(frame: KlineFrame) -> dict:
    newest_first = assign_bar_refs(frame.bars, symbol=frame.symbol, timeframe=frame.timeframe)
    recent_chronological = list(reversed(newest_first[:5]))
    full_range = BarRange(start=newest_first[-1], end=newest_first[0]).model_dump(mode="json")
    return {
        "cycle_position": "normal_channel",
        "direction": "bullish",
        "diagnosis_confidence": 78,
        "market_phase": "stable",
        "detected_patterns": [],
        "key_signals": ["多头延续"],
        "htf_context": "背景偏多",
        "entry_setup": "交由阶段二判断",
        "strategy_files_needed": ["上涨通道分析识别.txt"],
        "support_levels": ["100"],
        "resistance_levels": ["170"],
        "bar_by_bar_summary": [
            {
                "bar_ref": ref.model_dump(mode="json"),
                "role": "structure",
                "bar_type": "trend_bull",
                "context_effect": "strengthens_bull",
                "follow_through": "yes",
                "trapped_side": "none",
                "reason": "价格延续上涨",
            }
            for ref in recent_chronological
        ],
        "gate_trace": [
            {
                "node_id": node_id,
                "question": f"节点 {node_id}",
                "answer": "是",
                "reason": "结构清晰，可继续进入阶段二" if node_id == "momentum_enough" else "结构清晰",
                "bar_range": full_range,
            }
            for node_id in ("data_sufficiency", "cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "program_direction", "always_in", "momentum_enough")
        ],
        "gate_result": "proceed",
    }


def test_original_prompt_uses_ordered_system_and_task_files() -> None:
    frame = build_stage1_frame(_bars(), "ESM4", "5m", visible_count=40)
    messages = build_original_stage1_messages(frame)
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert system.index("【角色指令】") < system.index("# 交易二元决策树")
    assert user.index("# 市场诊断框架（三层递进）") < user.index("【文件定位】本文件职责：K线信号")
    assert "程序预填充节点判断依据" in user
    assert "bar_timestamp | timeframe | session | day_index" in user
    assert not re.search(r"\bK\d+", user)
    assert frame.bars[0].close == _bars()[-1].close
    assert frame.bars[0].seq == 1


def test_original_validator_retries_raw_invalid_json() -> None:
    frame = build_stage1_frame(_bars(), "ESM4", "5m", visible_count=40)
    outputs = ["不是 JSON", json.dumps(_valid_output(frame), ensure_ascii=False)]
    prompts: list[str] = []

    async def fake_call(system: str, payload: dict) -> LLMResponse:
        prompts.append(payload["_user_prompt"])
        raw = outputs[len(prompts) - 1]
        return LLMResponse({}, 10, 5, 15, "model", "deepseek-v4-pro", raw_content=raw)

    result, called = anyio.run(
        execute_original_stage1,
        _base(),
        frame,
        fake_call,
        lambda response, stage: None,
    )
    assert called is True
    assert len(prompts) == 2
    assert "校验未通过" in prompts[1]
    assert result.result_kind == "live"
    assert result.confidence == 78
