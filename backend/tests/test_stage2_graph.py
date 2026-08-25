from datetime import datetime, timezone

import anyio

from app.analysis.workflow.graph import DemoAnalysisWorkflow, run_demo_analysis_workflow
from app.analysis.contracts import IncrementalDelta, PrecheckResult, Stage1Result
from app.core.models import Bar, Direction, HistoricalQuery
from app.analysis.workflow.stage1.adapter import build_stage1_frame
from app.analysis.workflow.stage2.adapter import build_original_stage2_messages
from app.analysis.workflow.stage2.graph_nodes import (
    build_order_method_precheck,
    build_raw_tradeability,
    build_risk_precheck,
    build_signal_precheck,
)


def _frame(*, flat: bool = False, count: int = 40):
    bars = []
    for index in range(count):
        base = 100.0 if flat else 100.0 + index
        bars.append(
            Bar(
                timestamp=datetime(2022, 1, 1, 0, index, tzinfo=timezone.utc),
                open=base,
                high=base if flat else base + 2.0,
                low=base if flat else base - 1.0,
                close=base if flat else base + 1.5,
                volume=10,
            )
        )
    return build_stage1_frame(bars, "ES", "1m", visible_count=len(bars))


def _stage1() -> Stage1Result:
    return Stage1Result(
        result_kind="live",
        precheck=PrecheckResult(passed=True, closed_bar_count=30),
        cycle_position="趋势",
        direction=Direction.bullish,
        confidence=80,
        support_levels=[105.0],
        resistance_levels=[130.0],
        gate_result="proceed",
        incremental_delta=IncrementalDelta(),
    )


def test_stage2_graph_uses_real_nodes_instead_of_trace_only_decision_nodes() -> None:
    """删除任一真实 Stage2 节点或恢复伪 decision 链时，本测试必须失败。"""
    workflow = DemoAnalysisWorkflow(provider=object())
    nodes = set(workflow._compiled.get_graph().nodes)

    assert {
        "stage2_context",
        "stage2_precheck",
        "stage2_llm",
        "stage2_parse",
        "stage2_valid",
        "stage2_finalize",
        "stage2_terminal",
    } <= nodes
    assert "run_stage2" not in nodes
    assert not {"signal_precheck", "risk_precheck", "order_method_precheck", "stage2_precheck_router"} & nodes
    assert not any(name.startswith("decision_") for name in nodes)


def test_raw_tradeability_merged_into_prepare_context() -> None:
    """可交易性硬门若仍作为独立节点挡在 Stage1 LLM 前，本测试必须失败。"""
    workflow = DemoAnalysisWorkflow(provider=object())
    graph = workflow._compiled.get_graph()
    nodes = set(graph.nodes)
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert "raw_tradeability_precheck" not in nodes
    assert ("stage1_features", "stage1_llm") in edges
    assert ("prepare_context", "stage1_features") in edges
    assert ("prepare_context", "stage1_terminal") in edges


def test_stage1_feature_nodes_are_merged() -> None:
    workflow = DemoAnalysisWorkflow(provider=object())
    nodes = set(workflow._compiled.get_graph().nodes)
    edges = {(edge.source, edge.target) for edge in workflow._compiled.get_graph().edges}

    assert "stage1_features" in nodes
    assert not {"direction_cal", "always_in_cal", "cycle_features_cal", "chaos_metrics_cal", "momentum_metrics_cal"} & nodes
    assert ("prepare_context", "stage1_features") in edges
    assert ("stage1_features", "stage1_llm") in edges


def test_stage2_prechecks_produce_prompt_ready_evidence() -> None:
    """任一前置计算丢失证据字段或允许集合时，本测试必须失败。"""
    frame = _frame()
    stage1 = _stage1()

    raw = build_raw_tradeability(frame)
    signal = build_signal_precheck(frame, stage1)
    risk = build_risk_precheck(frame, stage1, signal)
    order = build_order_method_precheck(stage1, signal, risk)

    assert raw["passed"] is True
    assert signal["signal_candidates"]
    assert risk["stop_anchors"]
    assert order["allowed_order_types"] == ["突破单", "市价单", "限价单"]

    messages, _, _ = build_original_stage2_messages(
        frame,
        stage1,
        precheck_context={"signal": signal, "risk": risk, "order_method": order},
    )
    assert "Stage2 LangGraph 前置计算上下文" in messages[-1]["content"]
    assert '"allowed_order_types"' in messages[-1]["content"]


def test_flat_market_fails_raw_tradeability_without_market_semantics() -> None:
    """零波动行情被放到 Stage1 LLM 时，本测试必须失败。"""
    result = build_raw_tradeability(_frame(flat=True))

    assert result["passed"] is False
    assert result["atr"] is None


def test_raw_tradeability_requires_35_closed_bars_for_ema20_warmup() -> None:
    """把最低数量降到 35 根以下时，本测试必须失败。"""
    insufficient = build_raw_tradeability(_frame(count=34))
    sufficient = build_raw_tradeability(_frame(count=35))

    assert insufficient["passed"] is False
    assert insufficient["closed_bar_count"] == 34
    assert sufficient["passed"] is True


def test_stage2_precheck_stops_before_second_llm() -> None:
    """任一 Stage2 硬门失败后仍路由至第二个 LLM 时，本测试必须失败。"""
    workflow = DemoAnalysisWorkflow(provider=object())
    state = {
        "stage2_frame": _frame(flat=True),
        "stage1": _stage1().model_copy(update={"support_levels": [], "resistance_levels": []}),
        "trace": [],
    }

    update = anyio.run(workflow._stage2_precheck, state)
    state.update(update)

    assert workflow._route_stage2_precheck(state) == "terminal"
    assert state["trace"] == ["stage2_precheck:failed"]
    assert state["signal_precheck"]["passed"] is False


def test_raw_tradeability_failure_skips_both_llms(monkeypatch) -> None:
    """可交易性预检失败后任何一次 LLM 调用都会使本测试失败。"""
    class FlatProvider:
        async def get_range(self, query):
            return [
                Bar(
                    timestamp=datetime(2022, 1, 1, 0, index, tzinfo=timezone.utc),
                    open=100,
                    high=100,
                    low=100,
                    close=100,
                    volume=10,
                )
                for index in range(30)
            ]

    async def forbidden_call(*args, **kwargs):
        raise AssertionError("可交易性预检失败时不应调用任何 LLM")

    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", forbidden_call)
    query = HistoricalQuery(
        symbol="ES",
        period="1m",
        start=datetime(2022, 1, 1, tzinfo=timezone.utc),
        end=datetime(2022, 1, 2, tzinfo=timezone.utc),
    )

    result = anyio.run(run_demo_analysis_workflow, FlatProvider(), query)

    assert result.audit.stage1_model_called is False
    assert result.audit.stage2_model_called is False
    assert result.stage2.terminal.outcome == "wait"
    assert result.stage2.terminal.terminal_node == "prepare_context"
    assert "prepare_context" in result.audit.graph_trail
    assert "stage1_llm" not in result.audit.graph_trail
