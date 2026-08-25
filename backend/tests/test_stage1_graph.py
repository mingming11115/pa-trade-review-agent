from datetime import datetime, timezone

import anyio

from app.analysis.workflow.graph import DemoAnalysisWorkflow, run_demo_analysis_workflow
from app.core.models import Bar
from app.core.models import HistoricalQuery
from app.analysis.workflow.stage1.adapter import build_stage1_frame
from app.analysis.workflow.stage1.adapter import build_original_stage1_messages
from app.analysis.workflow.stage1.graph_nodes import (
    build_chaos_metrics,
    build_cycle_features,
    build_momentum_metrics,
    build_program_node_context,
)


def _frame(count: int = 20):
    bars = [
        Bar(
            timestamp=datetime(2022, 1, 1, 0, index, tzinfo=timezone.utc),
            open=100 + index,
            high=103 + index,
            low=99 + index,
            close=102 + index,
            volume=10,
        )
        for index in range(count)
    ]
    return build_stage1_frame(bars, "ES", "1m", visible_count=count)


def test_program_context_contains_authoritative_nodes_before_llm() -> None:
    context = build_program_node_context(_frame())

    assert context["data_valid"]["node_id"] == "data_sufficiency"
    assert context["direction"]["node_id"] == "program_direction"
    assert context["always_in"]["node_id"] == "always_in"
    assert context["data_valid"]["answer"] == "是"


def test_cycle_features_are_structured_for_prompt_injection() -> None:
    features = build_cycle_features(_frame())

    assert features["bar_count"] == 20
    assert features["recent_bar_range"]["start"]["day_index"] == 61
    assert features["recent_bar_range"]["end"]["day_index"] == 80
    assert features["recent_bar_range"]["start"]["timeframe"] == "1m"
    assert isinstance(features["mean_overlap"], (float, type(None)))
    assert set(features["swing_structure"]) == {"higher_highs", "higher_lows", "lower_highs", "lower_lows"}


def test_chaos_and_momentum_metrics_are_structured() -> None:
    frame = _frame()
    chaos = build_chaos_metrics(frame)
    momentum = build_momentum_metrics(frame)

    assert 0 <= chaos["chaos_score"] <= 3
    assert set(chaos) >= {"ema_flat", "mean_overlap", "high_overlap", "direction_score", "no_direction"}
    assert set(momentum) >= {"bull_trend_bars", "bear_trend_bars", "trend_bar_ratio", "mean_overlap"}


def test_stage1_prompt_includes_program_context() -> None:
    frame = _frame()
    context = build_program_node_context(frame)

    messages = build_original_stage1_messages(frame, program_context=context)

    prompt = messages[-1]["content"]
    assert "Stage1 LangGraph 程序上下文" in prompt
    assert '"cycle_features"' in prompt
    assert '"chaos_metrics"' in prompt
    assert '"momentum_metrics"' in prompt
    assert "not_extreme_chaos answer=否（极端混乱" not in prompt
    assert "not_extreme_chaos answer=是（极端混乱" in prompt


def test_insufficient_data_stops_before_stage1_llm(monkeypatch) -> None:
    class ShortProvider:
        async def get_range(self, query):
            return [
                Bar(
                    timestamp=datetime(2022, 1, 1, 0, index, tzinfo=timezone.utc),
                    open=100 + index,
                    high=103 + index,
                    low=99 + index,
                    close=102 + index,
                    volume=10,
                )
                for index in range(10)
            ]

    async def forbidden_call(*args, **kwargs):
        raise AssertionError("数据不足时不应调用 LLM")

    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", forbidden_call)
    query = HistoricalQuery(
        symbol="ES",
        period="1m",
        start=datetime(2022, 1, 1, tzinfo=timezone.utc),
        end=datetime(2022, 1, 2, tzinfo=timezone.utc),
    )

    result = anyio.run(run_demo_analysis_workflow, ShortProvider(), query)

    assert result.audit.stage1_model_called is False
    assert result.audit.stage2_model_called is False
    assert result.stage2.terminal.outcome == "wait"
    assert "prepare_context" in result.audit.graph_trail
    assert "stage1_llm" not in result.audit.graph_trail
    assert "stage1_terminal" in result.audit.graph_trail


def test_stage1_graph_uses_one_preparation_node() -> None:
    workflow = DemoAnalysisWorkflow(provider=object())
    nodes = set(workflow._compiled.get_graph().nodes)

    assert "prepare_context" in nodes
    assert not {"hydrate_memory", "load_bars", "local_precheck", "prepare_snapshot", "data_valid"} & nodes


def test_short_visible_range_expands_loaded_bars_and_exposes_all_of_them() -> None:
    """补拉数据仍被隐藏在 indicator_source_bars 时，本测试必须失败。"""
    class Provider:
        async def get_range(self, query):
            return [
                Bar(
                    timestamp=datetime(2022, 1, 1, 0, index, tzinfo=timezone.utc),
                    open=100 + index,
                    high=102 + index,
                    low=99 + index,
                    close=101 + index,
                    volume=10,
                )
                for index in range(40)
            ]

    workflow = DemoAnalysisWorkflow(provider=Provider())
    query = HistoricalQuery(
        symbol="ES",
        period="1m",
        start=datetime(2022, 1, 1, 0, 35, tzinfo=timezone.utc),
        end=datetime(2022, 1, 1, 0, 39, tzinfo=timezone.utc),
    )
    state = {"query": query, "trace": []}

    update = anyio.run(workflow._load_bars, state)

    assert len(update["bars"]) == 40
    assert update["bars"] == update["indicator_source_bars"]


def test_missing_session_bucket_stops_before_stage1_llm(monkeypatch) -> None:
    class GappyProvider:
        async def get_range(self, query):
            # Contiguous minutes 0..39 except minute 20 missing — same CME session day.
            return [
                Bar(
                    timestamp=datetime(2022, 1, 3, 15, index, tzinfo=timezone.utc),
                    open=100 + index,
                    high=103 + index,
                    low=99 + index,
                    close=102 + index,
                    volume=10,
                )
                for index in range(40)
                if index != 20
            ]

    async def forbidden_call(*args, **kwargs):
        raise AssertionError("缺桶时不应调用 LLM")

    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", forbidden_call)
    query = HistoricalQuery(
        symbol="ES",
        period="1m",
        start=datetime(2022, 1, 3, 15, 0, tzinfo=timezone.utc),
        end=datetime(2022, 1, 3, 15, 40, tzinfo=timezone.utc),
        analysis_mode="realtime",
    )

    result = anyio.run(run_demo_analysis_workflow, GappyProvider(), query)

    assert result.audit.stage1_model_called is False
    assert result.audit.stage2_model_called is False
    assert result.stage1.precheck.passed is False
    assert result.stage1.precheck.failure_type == "missing_buckets"
    assert result.stage2.terminal.outcome == "wait"
    assert "缺桶" in (result.stage2.terminal.reason or "")
    assert "stage1_llm" not in result.audit.graph_trail
