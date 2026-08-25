"""统一分析工作流图模块。

本模块基于 LangGraph 构建两阶段（Stage1 市场诊断 + Stage2 交易决策）的有向图工作流，
包含状态定义、记忆存储、LLM 调用与流式输出、预检/解析/校验/组装等全部节点，
以及 LangGraph 不可用时的顺序降级路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any, TypedDict

from app.market.analysis import aggregate_bars, analyze_bars
from app.analysis.contracts import (
    AnalysisAudit,
    AnalysisMode,
    AnalysisSnapshot,
    AnalysisTrigger,
    Decision,
    GateTraceItem,
    IncrementalDelta,
    MarketSnapshot,
    PrecheckResult,
    PreviousContext,
    Stage1Result,
    Stage2Result,
    TerminalResult,
    TradeReviewResult,
    TradeSnapshot,
)
from app.core.contracts import resolve_contract_symbol
from app.core.models import Bar, BasicAnalysis, DemoAnalysisResponse, Direction, HistoricalQuery
from app.market.provider import MassiveHistoricalProvider
from app.personal.service import DebugPreview, TokenUsageRecord, append_usage, get_public_settings
from app.llm.client import LLMResponse, LLMStreamDelta, call_llm
from app.market.indicators import build_market_indicators
from app.market.service import find_missing_session_buckets
from app.market.analysis import PERIOD_SECONDS
from app.analysis.workflow.stage1.adapter import build_original_stage1_messages, build_stage1_frame, execute_original_stage1, merge_original_stage1
from app.analysis.workflow.stage2.adapter import build_original_stage2_messages, build_stage2_result, validate_original_stage2
from app.analysis.workflow.stage1.core.decision_nodes import (
    build_program_trace_node,
    judge_data_sufficiency,
)
from app.analysis.workflow.stage1.core.bar_identity import BarRange, assign_timestamp_refs, enrich_api_bars
from app.analysis.workflow.stage1.core.json_validator import JsonValidator, Ok, ValidationError, coalesce_model_json_text
from app.analysis.workflow.stage1.core.retry_feedback import build_retry_feedback
from app.analysis.audit import append_stage1_audit
from app.analysis.execution.runs import attach_llm_response, persist_llm_response, start_analysis_run, update_analysis_run
from app.analysis.workflow.stage1.graph_nodes import build_program_node_context
from app.analysis.workflow.stage2.graph_nodes import (
    MIN_CLOSED_BARS,
    build_order_method_precheck,
    build_raw_tradeability,
    build_risk_precheck,
    build_signal_precheck,
)

try:  # pragma: no cover - optional dependency
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - exercised through fallback path
    END = None
    StateGraph = None


class DemoAnalysisState(TypedDict, total=False):
    """工作流状态定义。

    使用 TypedDict 描述图在节点间传递的全部状态字段，
    total=False 表示所有字段均为可选（不同节点只写入自己负责的字段）。
    """

    query: HistoricalQuery  # 用户原始查询
    resolved_symbol: str  # 解析后的合约符号（含到期月）
    bars: list[Bar]  # 实际参与分析展示的 K 线
    indicator_source_bars: list[Bar]  # 用于指标计算的含预热 K 线
    analysis: BasicAnalysis  # 本地基础统计
    memory_key: str  # 记忆存储键
    memory_summary: str  # 记忆摘要（上一轮分析结果）
    trace: list[str]  # 执行轨迹字符串列表
    analysis_id: uuid.UUID  # 本次分析唯一 ID
    started_at: datetime  # 开始时间
    previous_context: PreviousContext | None  # 上一轮上下文
    snapshot: AnalysisSnapshot  # 分析快照
    stage1: Stage1Result  # 阶段一结果
    stage2: Stage2Result  # 阶段二结果
    review_result: list[TradeReviewResult]  # 交易复盘结果列表
    stage1_model_called: bool  # 阶段一是否调用了 LLM
    stage2_model_called: bool  # 阶段二是否调用了 LLM
    stage1_frame: Any  # 阶段一 K 线帧
    decision_execution: list[dict[str, Any]]  # 决策执行审计列表
    local_precheck_ok: bool  # 本地预检是否通过
    local_precheck_reason: str | None  # 本地预检失败原因
    local_precheck_failure_type: str | None  # 本地预检失败类型
    stage1_program_context: dict[str, Any]  # 阶段一程序权威上下文
    stage1_messages: list[dict[str, str]]  # 阶段一 LLM 消息列表
    stage1_response: LLMResponse | None  # 阶段一 LLM 原始响应
    stage1_candidate: dict[str, Any]  # 阶段一解析后的候选 JSON
    stage1_attempt: int  # 阶段一当前尝试次数
    stage1_retry_text: str  # 阶段一重试反馈文本
    stage1_validation_error: ValidationError | None  # 阶段一校验错误
    stage1_route: str  # 阶段一下一步路由
    stage1_run_id: Any  # 阶段一分析运行记录 ID
    raw_tradeability: dict[str, Any]  # 原始可交易性预检结果
    stage2_context: dict[str, Any]  # 阶段二上下文
    signal_precheck: dict[str, Any]  # 信号硬门预检
    risk_precheck: dict[str, Any]  # 风控硬门预检
    order_method_precheck: dict[str, Any]  # 下单方式硬门预检
    stage2_messages: list[dict[str, str]]  # 阶段二 LLM 消息列表
    stage2_frame: Any  # 阶段二 K 线帧
    stage2_stage1_json: dict[str, Any]  # 传给阶段二的 Stage1 JSON
    stage2_strategy_files: list[str]  # 阶段二引用的策略文件列表
    stage2_response: LLMResponse | None  # 阶段二 LLM 原始响应
    stage2_candidate: dict[str, Any]  # 阶段二解析后的候选 JSON
    stage2_attempt: int  # 阶段二当前尝试次数
    stage2_retry_text: str  # 阶段二重试反馈文本
    stage2_validation_error: ValidationError | None  # 阶段二校验错误
    stage2_route: str  # 阶段二下一步路由
    stage2_run_id: Any  # 阶段二分析运行记录 ID
    graph_trail: list[str]  # 图节点实际执行顺序


class AnalysisMemoryStore:
    """异步记忆存储，用于在同一分析键下保存/恢复上一轮上下文。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()  # 并发锁
        self._snapshots: dict[str, dict[str, Any]] = {}  # 内存中的快照字典

    async def load(self, key: str) -> dict[str, Any]:
        """按键加载上一轮记忆，不存在时返回空字典。"""
        async with self._lock:
            return self._snapshots.get(key, {})

    async def save(self, key: str, summary: dict[str, Any]) -> None:
        """保存本轮分析摘要到记忆存储。"""
        async with self._lock:
            self._snapshots[key] = summary


# 全局默认记忆存储实例
DEFAULT_MEMORY_STORE = AnalysisMemoryStore()
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DemoAnalysisWorkflow:
    """两阶段分析工作流的主类。

    封装 LangGraph 编译图、流式队列和 LLM 调用，
    提供 invoke（同步获取结果）和 stream（流式输出进度与推理）两种执行入口。
    """

    provider: MassiveHistoricalProvider  # 行情数据提供者
    memory_store: AnalysisMemoryStore = DEFAULT_MEMORY_STORE  # 记忆存储
    _compiled: Any = field(init=False, repr=False)  # 编译后的 LangGraph 图
    _stream_queue: asyncio.Queue[dict[str, Any] | None] | None = field(default=None, init=False, repr=False)  # 流式事件队列
    _stream_llm_stage: str | None = field(default=None, init=False, repr=False)  # 当前流式 LLM 阶段标识

    def __post_init__(self) -> None:
        """初始化后立即编译图。"""
        self._compiled = self._build_compiled_graph()

    async def _emit_stream_event(self, event: dict[str, Any]) -> None:
        """向流式队列推送一个事件，队列不存在时跳过。"""
        queue = self._stream_queue
        if queue is not None:
            await queue.put(event)

    async def _on_llm_delta(self, delta: LLMStreamDelta) -> None:
        """LLM 流式增量回调，将推理/输出文本以 llm_delta 事件推送。"""
        stage = self._stream_llm_stage or "stage1"
        await self._emit_stream_event({
            "type": "llm_delta",
            "stage": stage,
            "kind": delta.kind,
            "text": delta.text,
            "message": "思考中…" if delta.kind == "reasoning" else "撰写回答中…",
        })

    async def _call_llm_for_stage(
        self,
        stage: str,
        system: str,
        payload: dict[str, Any],
        *,
        analysis_id: str,
    ) -> LLMResponse | None:
        """带流式支持的 LLM 调用封装，记录当前阶段并在完成后恢复。"""
        previous = self._stream_llm_stage
        self._stream_llm_stage = stage
        request_payload = dict(payload)
        request_payload["analysis_id"] = analysis_id
        try:
            if self._stream_queue is not None:
                return await call_llm(system, request_payload, on_delta=self._on_llm_delta)
            return await call_llm(system, request_payload)
        finally:
            self._stream_llm_stage = previous

    async def _ainvoke_with_trail(self, query: HistoricalQuery) -> DemoAnalysisState:
        """运行编译后的图，同时记录真实 LangGraph 节点执行顺序。"""
        assert self._compiled is not None
        initial: DemoAnalysisState = {"query": query}
        trail: list[str] = []  # 节点执行轨迹
        state: DemoAnalysisState | None = None
        # 使用 astream 同时获取 updates（节点更新）和 values（全量状态）
        async for item in self._compiled.astream(
            initial,
            config={"recursion_limit": 64},
            stream_mode=["updates", "values"],
        ):
            mode, data = item if isinstance(item, tuple) else ("values", item)
            if mode == "updates" and isinstance(data, dict):
                for name, update in data.items():
                    trail.append(str(name))
                    await self._emit_node_progress(str(name), update if isinstance(update, dict) else {})
            elif mode == "values" and isinstance(data, dict):
                state = data  # type: ignore[assignment]
        # 如果流式没有产出最终状态，则回退到同步 ainvoke
        if state is None:
            state = await self._compiled.ainvoke(initial, config={"recursion_limit": 64})
        result: DemoAnalysisState = dict(state)
        result["graph_trail"] = trail
        return result

    async def _emit_node_progress(self, node_name: str, update: dict[str, Any]) -> None:
        """根据节点名推送阶段进度事件，用于前端实时展示。"""
        if self._stream_queue is None:
            return
        if node_name == "prepare_context":
            bars = update.get("bars")
            if bars is not None:
                await self._emit_stream_event({
                    "type": "market",
                    "stage": "market",
                    "message": f"已加载 {len(bars)} 根 K 线",
                    "resolved_symbol": update.get("resolved_symbol"),
                    "bars": [bar.model_dump(mode="json") if hasattr(bar, "model_dump") else bar for bar in bars],
                })
            return
        if node_name == "stage1_llm":
            await self._emit_stream_event({
                "type": "status",
                "stage": "stage1",
                "message": "阶段一：市场诊断与闸门推理中",
            })
            return
        if node_name == "stage1_finalize" and update.get("stage1") is not None:
            stage1 = update["stage1"]
            await self._emit_stream_event({
                "type": "stage1",
                "stage": "stage1",
                "message": "阶段一完成",
                "stage1": stage1.model_dump(mode="json") if hasattr(stage1, "model_dump") else stage1,
            })
            return
        if node_name == "stage2_llm":
            await self._emit_stream_event({
                "type": "status",
                "stage": "stage2",
                "message": "阶段二：交易决策推理中",
            })
            return
        if node_name == "stage2_finalize" and update.get("stage2") is not None:
            stage2 = update["stage2"]
            await self._emit_stream_event({
                "type": "stage2",
                "stage": "stage2",
                "message": "阶段二完成",
                "stage2": stage2.model_dump(mode="json") if hasattr(stage2, "model_dump") else stage2,
            })

    def _build_compiled_graph(self):
        """构建并编译 LangGraph 有向图，定义所有节点和边。"""
        if StateGraph is None:
            return None

        graph = StateGraph(DemoAnalysisState)

        # === 注册全部节点 ===
        graph.add_node("prepare_context", self._prepare_context)  # 准备阶段：记忆/行情/预检/快照
        graph.add_node("stage1_features", self._stage1_features)  # Stage1 程序特征计算
        graph.add_node("stage1_llm", self._stage1_llm)  # Stage1 LLM 调用
        graph.add_node("stage1_parse", self._stage1_parse)  # Stage1 JSON 解析
        graph.add_node("stage1_gate_validate", self._stage1_gate_validate)  # Stage1 综合校验
        graph.add_node("stage1_finalize", self._stage1_finalize)  # Stage1 结果组装
        graph.add_node("stage1_terminal", self._stage1_terminal)  # Stage1 终止/短路
        graph.add_node("stage2_context", self._stage2_context)  # Stage2 上下文准备
        graph.add_node("stage2_precheck", self._stage2_precheck)  # Stage2 前置硬门预检
        graph.add_node("stage2_llm", self._stage2_llm)  # Stage2 LLM 调用
        graph.add_node("stage2_parse", self._stage2_parse)  # Stage2 JSON 解析
        graph.add_node("stage2_valid", self._stage2_valid)  # Stage2 统一校验
        graph.add_node("stage2_finalize", self._stage2_finalize)  # Stage2 结果组装
        graph.add_node("stage2_terminal", self._stage2_terminal)  # Stage2 终止/短路
        graph.add_node("assemble_reviews", self._assemble_reviews)  # 交易复盘组装
        graph.add_node("write_memory", self._write_memory)  # 记忆持久化

        # === 定义边和条件路由 ===
        graph.set_entry_point("prepare_context")
        # 准备阶段通过预检则继续，否则直接终止
        graph.add_conditional_edges("prepare_context", self._route_prepared_context, {"continue": "stage1_features", "terminal": "stage1_terminal"})
        graph.add_edge("stage1_features", "stage1_llm")
        # LLM 有响应则解析，无可用模型则降级到本地结果
        graph.add_conditional_edges("stage1_llm", self._route_stage1_llm, {"parse": "stage1_parse", "fallback": "stage1_finalize"})
        # 解析成功则校验，失败且未耗尽则重试，否则终止
        graph.add_conditional_edges("stage1_parse", self._route_stage1_parse, {"validate": "stage1_gate_validate", "retry": "stage1_llm", "terminal": "stage1_terminal"})
        # 校验成功则组装，失败且未耗尽则重试，否则终止
        graph.add_conditional_edges("stage1_gate_validate", self._route_stage1_validation, {"finalize": "stage1_finalize", "retry": "stage1_llm", "terminal": "stage1_terminal"})
        # Stage1 放行则进入 Stage2，否则终止
        graph.add_conditional_edges("stage1_finalize", self._route_stage1_result, {"stage2": "stage2_context", "terminal": "stage1_terminal"})
        graph.add_edge("stage1_terminal", "assemble_reviews")
        graph.add_edge("stage2_context", "stage2_precheck")
        # 硬门全通过则调用 Stage2 LLM，否则终止
        graph.add_conditional_edges("stage2_precheck", self._route_stage2_precheck, {"llm": "stage2_llm", "terminal": "stage2_terminal"})
        graph.add_edge("stage2_llm", "stage2_parse")
        # 解析成功则校验，失败最多重试一次，否则终止
        graph.add_conditional_edges("stage2_parse", self._route_stage2_parse, {"validate": "stage2_valid", "retry": "stage2_llm", "terminal": "stage2_terminal"})
        # 校验成功则组装，失败最多重试一次，耗尽则终止
        graph.add_conditional_edges("stage2_valid", self._route_stage2_validation, {"retry": "stage2_llm", "terminal": "stage2_terminal", "finalize": "stage2_finalize"})
        graph.add_edge("stage2_finalize", "assemble_reviews")
        graph.add_edge("stage2_terminal", "assemble_reviews")
        graph.add_edge("assemble_reviews", "write_memory")
        graph.add_edge("write_memory", END)
        return graph.compile()

    async def invoke(self, query: HistoricalQuery) -> DemoAnalysisResponse:
        """同步执行工作流并返回最终分析响应。"""
        if self._compiled is not None:
            state = await self._ainvoke_with_trail(query)
            return self._to_response(state)

        # LangGraph 不可用时的降级路径：按节点顺序手动执行
        state: DemoAnalysisState = {"query": query, "graph_trail": []}
        state.update(await self._prepare_context(state))
        self._append_trail(state, "prepare_context")
        if self._route_prepared_context(state) == "terminal":
            self._append_trail(state, "stage1_terminal")
            state.update(await self._stage1_terminal(state))
        else:
            state.update(await self._run_stage1(state))
            self._append_trail(state, "stage1_llm" if state.get("stage1_model_called") else "stage1_finalize")
            if state["stage1"].gate_result == "proceed":
                state.update(await self._run_stage2_fallback(state))
            else:
                self._append_trail(state, "stage1_terminal")
                state.update(await self._stage1_terminal(state))
        state.update(await self._assemble_reviews(state))
        state.update(await self._write_memory(state))
        return self._to_response(state)

    async def stream(self, query: HistoricalQuery):
        """流式执行工作流，实时推送进度和 LLM 推理/输出增量。"""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._stream_queue = queue

        async def run() -> None:
            try:
                await queue.put({"type": "status", "stage": "prepare", "message": "正在执行统一分析图"})
                if self._compiled is not None:
                    state = await self._ainvoke_with_trail(query)
                else:
                    state = await self._run_sequential(query)
                await queue.put({
                    "type": "result",
                    "stage": "complete",
                    "message": "分析完成",
                    "result": self._to_response(state).model_dump(mode="json"),
                })
            except Exception as exc:
                await queue.put({
                    "type": "error",
                    "stage": "complete",
                    "message": f"分析失败：{type(exc).__name__}: {exc}",
                    "code": "analysis_error",
                })
                raise
            finally:
                await queue.put(None)  # 哨兵值，通知消费端结束
                self._stream_queue = None

        task = asyncio.create_task(run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            await task

    async def _run_sequential(self, query: HistoricalQuery) -> DemoAnalysisState:
        """LangGraph 不可用时的流式降级路径，按同一组节点顺序手动执行。"""
        state: DemoAnalysisState = {"query": query, "graph_trail": []}
        await self._emit_stream_event({"type": "status", "stage": "prepare", "message": "正在恢复上一轮分析上下文"})
        state.update(await self._prepare_context(state))
        self._append_trail(state, "prepare_context")
        await self._emit_stream_event({
            "type": "market",
            "stage": "market",
            "message": f"已加载 {len(state.get('bars', []))} 根 K 线",
            "resolved_symbol": state.get("resolved_symbol"),
            "bars": [bar.model_dump(mode="json") for bar in state.get("bars", [])],
        })
        if self._route_prepared_context(state) == "terminal":
            self._append_trail(state, "stage1_terminal")
            state.update(await self._stage1_terminal(state))
        else:
            await self._emit_stream_event({"type": "status", "stage": "stage1", "message": "阶段一：市场诊断与闸门推理中"})
            state.update(await self._run_stage1(state))
            self._append_trail(state, "stage1_llm" if state.get("stage1_model_called") else "stage1_finalize")
            await self._emit_stream_event({
                "type": "stage1",
                "stage": "stage1",
                "message": "阶段一完成",
                "analysis": state["analysis"].model_dump(mode="json"),
                "stage1": state["stage1"].model_dump(mode="json"),
            })
            await self._emit_stream_event({"type": "status", "stage": "stage2", "message": "阶段二：交易决策推理中"})
            if state["stage1"].gate_result == "proceed":
                state.update(await self._run_stage2_fallback(state))
            else:
                self._append_trail(state, "stage1_terminal")
                state.update(await self._stage1_terminal(state))
        state.update(await self._assemble_reviews(state))
        await self._emit_stream_event({
            "type": "stage2",
            "stage": "stage2",
            "message": "阶段二完成",
            "stage2": state["stage2"].model_dump(mode="json"),
        })
        await self._emit_stream_event({"type": "status", "stage": "persist", "message": "正在保存分析结果"})
        state.update(await self._write_memory(state))
        return state
    @staticmethod
    def _append_trail(state: DemoAnalysisState, node_id: str) -> None:
        """向 graph_trail 追加一个节点 ID，记录实际执行顺序。"""
        trail = list(state.get("graph_trail", []))
        trail.append(node_id)
        state["graph_trail"] = trail

    async def _prepare_context(self, state: DemoAnalysisState) -> dict[str, Any]:
        """一次完成记忆恢复、行情加载、数据/可交易性预检、快照构建和 data_sufficiency 判定。"""
        working: DemoAnalysisState = dict(state)
        # 依次执行：恢复记忆 -> 加载 K 线 -> 本地预检
        for step in (self._hydrate_memory, self._load_bars, self._local_precheck):
            working.update(await step(working))
        # 预检不通过时组装可终止的上下文
        if not working.get("local_precheck_ok"):
            working.update(await self._prepare_failed_context(working))
            return dict(working)
        # 预检通过：构建快照 -> 数据充足度判定 -> 可交易性预检
        working.update(await self._prepare_snapshot(working))
        working.update(await self._data_valid(working))
        working.update(await self._attach_raw_tradeability(working))
        return dict(working)

    async def _prepare_failed_context(self, state: DemoAnalysisState) -> dict[str, Any]:
        """预检失败时仍组装可终止的 stage1/snapshot，避免下游缺字段。"""
        bars = list(state.get("bars") or [])
        query = state["query"]
        symbol = state.get("resolved_symbol") or query.symbol
        reason = state.get("local_precheck_reason") or "行情数据无效"
        failure_type = state.get("local_precheck_failure_type") or "invalid_bars"
        # 构造预检失败的 Stage1Result
        stage1 = Stage1Result(
            result_kind="failed",
            precheck=PrecheckResult(
                passed=False,
                failure_type=failure_type,
                reason=reason,
                closed_bar_count=len(bars),
            ),
            confidence=0,
            gate_result="unknown",
            failure_subtype="insufficient_information",
        )
        # 为 K 线分配时间戳引用，用于 bar_range
        refs = (
            assign_timestamp_refs(
                [bar.timestamp for bar in bars],
                symbol=symbol,
                timeframe=query.period.value,
            )
            if bars
            else []
        )
        item = {
            "node_id": "data_sufficiency",
            "question": "数据是否满足分析要求？",
            "answer": "否",
            "reason": reason,
            "bar_range": BarRange(start=refs[0], end=refs[-1]).model_dump(mode="json") if refs else None,
            "source": "program",
        }
        # 缺桶类型仍可构建快照，复用已有的 _prepare_snapshot
        if failure_type == "missing_buckets" and bars:
            snapshot_update = await self._prepare_snapshot(state)
            return {
                **snapshot_update,
                "stage1": stage1,
                **self._with_program_context({**state, **snapshot_update}, "data_valid", item),
            }
        started = state.get("started_at") or datetime.now(timezone.utc)
        # 无 K 线时生成空白的基础分析
        analysis = (
            analyze_bars(bars)
            if bars
            else BasicAnalysis(
                bar_count=0,
                start=query.start,
                end=query.end,
                first_open=0.0,
                latest_close=0.0,
                period_high=0.0,
                period_low=0.0,
                change_percent=0.0,
                bullish_bars=0,
                bearish_bars=0,
                neutral_bars=0,
                direction=Direction.neutral,
                method="local_precheck_failed",
            )
        )
        snapshot = AnalysisSnapshot(
            analysis_id=state["analysis_id"],
            mode=AnalysisMode(query.analysis_mode),
            trigger=AnalysisTrigger(occurred_at=started),
            query=query,
            market=MarketSnapshot(
                symbol=query.symbol,
                contract=symbol,
                period=query.period.value,
                bars=bars,
                indicators={},
            ),
            trades=[TradeSnapshot.model_validate(trade.model_dump()) for trade in query.trades],
            previous_context=state.get("previous_context"),
            generated_at=datetime.now(timezone.utc),
        )
        return {
            "analysis": analysis,
            "snapshot": snapshot,
            "stage1": stage1,
            "stage1_frame": build_stage1_frame(bars, symbol, query.period.value, visible_count=len(bars)),
            **self._with_program_context(state, "data_valid", item),
        }

    async def _attach_raw_tradeability(self, state: DemoAnalysisState) -> dict[str, Any]:
        """把原可交易性硬门并入准备阶段：根数/ATR/波幅不足时短路，不进入后续节点。"""
        result = build_raw_tradeability(state["stage1_frame"])
        trace = list(state.get("trace", []))
        trace.append("raw_tradeability:passed" if result["passed"] else "raw_tradeability:failed")
        update: dict[str, Any] = {"raw_tradeability": result, "trace": trace}
        if result["passed"]:
            return update
        # 可交易性不通过：生成 data_sufficiency 否决节点并更新 Stage1
        from app.analysis.workflow.stage1.core.bar_identity import BarRange, assign_bar_refs
        refs = assign_bar_refs(
            state["stage1_frame"].bars,
            symbol=state["stage1_frame"].symbol,
            timeframe=state["stage1_frame"].timeframe,
        )
        item = {
            "node_id": "data_sufficiency",
            "question": "数据是否满足分析要求？",
            "answer": "否",
            "reason": result["reason"],
            "bar_range": BarRange(start=refs[-1], end=refs[0]).model_dump(mode="json") if refs else None,
            "source": "program",
        }
        update.update(self._with_program_context(state, "data_valid", item))
        stage1 = state.get("stage1")
        if stage1 is not None and stage1.precheck.passed:
            stage1 = stage1.model_copy(deep=True)
            stage1.result_kind = "failed"
            stage1.precheck = PrecheckResult(
                passed=False,
                failure_type="raw_tradeability",
                reason=result["reason"],
                closed_bar_count=int(result.get("closed_bar_count") or 0),
            )
            stage1.gate_result = "unknown"
            stage1.failure_subtype = "insufficient_information"
            update["stage1"] = stage1
        return update

    @staticmethod
    def _route_prepared_context(state: DemoAnalysisState) -> str:
        """OHLC、data_sufficiency 与可交易性预检均通过时继续本地计算，否则进入统一终止分支。"""
        data_valid = state.get("stage1_program_context", {}).get("data_valid", {})
        raw_ok = state.get("raw_tradeability", {}).get("passed", True)
        return (
            "continue"
            if state.get("local_precheck_ok") and data_valid.get("answer") == "是" and raw_ok
            else "terminal"
        )

    async def _hydrate_memory(self, state: DemoAnalysisState) -> dict[str, Any]:
        """恢复上一轮分析记忆，生成分析 ID 和开始时间。"""
        query = state["query"]
        memory_key = self._memory_key(query)
        memory_summary = await self.memory_store.load(memory_key)
        trace = ["memory:loaded"]
        if memory_summary:
            trace.append("memory:restored")
        return {
            "memory_key": memory_key,
            "memory_summary": str(memory_summary),
            "previous_context": PreviousContext.model_validate(memory_summary) if memory_summary else None,
            "analysis_id": uuid.uuid4(),
            "started_at": datetime.now(timezone.utc),
            "trace": trace,
        }

    async def _load_bars(self, state: DemoAnalysisState) -> dict[str, Any]:
        """加载并展示全部分析 K 线；不足最低数量时逐步向历史方向扩展。"""
        query = state["query"]
        resolved_symbol = resolve_contract_symbol(query.symbol, query.start, query.end)
        lookback_periods = 50  # 初始预热根数
        indicator_source_bars: list[Bar] = []
        previous_count = -1
        # 最多扩展 4 次，每次翻倍预热根数直到满足最低要求
        for _ in range(4):
            expanded_start = query.start - timedelta(seconds=PERIOD_SECONDS[query.period] * lookback_periods)
            expanded_query = query.model_copy(update={"symbol": resolved_symbol, "start": expanded_start})
            indicator_source_bars = aggregate_bars(await self.provider.get_range(expanded_query), query.period)
            if len(indicator_source_bars) >= MIN_CLOSED_BARS or len(indicator_source_bars) <= previous_count:
                break
            previous_count = len(indicator_source_bars)
            lookback_periods *= 2
        # 为 K 线分配时间戳引用并丰富元数据
        indicator_source_bars = enrich_api_bars(
            indicator_source_bars,
            symbol=resolved_symbol,
            timeframe=query.period.value,
        )
        bars = list(indicator_source_bars)
        trace = list(state.get("trace", []))
        trace.append(f"bars:loaded:{len(bars)}")
        return {"resolved_symbol": resolved_symbol, "bars": bars, "indicator_source_bars": indicator_source_bars, "trace": trace}

    async def _local_precheck(self, state: DemoAnalysisState) -> dict[str, Any]:
        """校验原始 K 线 OHLC 与时段内缺桶，并写入 local_precheck_ok。"""
        bars = state.get("bars", [])
        query = state["query"]
        symbol = state.get("resolved_symbol") or query.symbol
        # 校验每根 K 线的 OHLC 合法性：high >= low，open/close 在 [low, high] 内
        ohlc_ok = bool(bars) and all(
            bar.high >= bar.low and bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high
            for bar in bars
        )
        # OHLC 合法时检查交易时段内是否有缺桶
        missing = (
            find_missing_session_buckets(bars, query.period.value, symbol)
            if ohlc_ok
            else []
        )
        ok = ohlc_ok and not missing
        reason = None
        failure_type = None
        if not bars:
            reason, failure_type = "K 线序列为空，无法分析", "bars_empty"
        elif not ohlc_ok:
            reason, failure_type = "存在 K 线 OHLC 不合法", "bars_empty_or_bad_ohlc"
        elif missing:
            reason = (
                f"交易时段内 K 线缺桶 {len(missing)} 个，"
                f"首缺 {missing[0].isoformat()}"
            )
            failure_type = "missing_buckets"
        trace = list(state.get("trace", []))
        trace.append("local_precheck:passed" if ok else "local_precheck:failed")
        return {
            "local_precheck_ok": ok,
            "local_precheck_reason": reason,
            "local_precheck_failure_type": failure_type,
            "trace": trace,
        }

    @staticmethod
    def _route_local_precheck(state: DemoAnalysisState) -> str:
        """原始 K 线合法则构建快照，否则进入 Stage1 终止分支。"""
        return "prepare" if state.get("local_precheck_ok") else "terminal"

    async def _prepare_snapshot(self, state: DemoAnalysisState) -> dict[str, Any]:
        """构建分析快照、基础统计和初始 Stage1 结果。"""
        bars = state["bars"]
        analysis = analyze_bars(bars)
        previous = state.get("previous_context")
        snapshot = AnalysisSnapshot(
            analysis_id=state["analysis_id"],
            mode=AnalysisMode(state["query"].analysis_mode),
            trigger=AnalysisTrigger(occurred_at=state["started_at"]),
            query=state["query"],
            market=MarketSnapshot(
                symbol=state["query"].symbol,
                contract=state["resolved_symbol"],
                period=state["query"].period.value,
                bars=bars,
                indicators=build_market_indicators(
                    state["indicator_source_bars"], bars,
                    symbol=state["resolved_symbol"], timeframe=state["query"].period.value,
                ),
            ),
            trades=[TradeSnapshot.model_validate(trade.model_dump()) for trade in state["query"].trades],
            previous_context=previous,
            generated_at=datetime.now(timezone.utc),
        )
        stage1 = self._build_stage1(
            analysis,
            bars,
            previous,
            snapshot.market.indicators,
            symbol=snapshot.market.contract,
            timeframe=snapshot.market.period,
        )
        stage1_frame = build_stage1_frame(
            state["indicator_source_bars"],
            state["resolved_symbol"],
            state["query"].period.value,
            visible_count=len(bars),
        )
        trace = list(state.get("trace", []))
        trace.append("snapshot:prepared")
        return {
            "analysis": analysis,
            "snapshot": snapshot,
            "stage1": stage1,
            "stage1_frame": stage1_frame,
            "trace": trace,
        }

    @staticmethod
    def _with_program_context(state: DemoAnalysisState, key: str, value: Any) -> dict[str, Any]:
        """以不可变更新方式向 Stage1 程序上下文写入一个计算结果。"""
        context = dict(state.get("stage1_program_context", {}))
        context[key] = value
        return {"stage1_program_context": context}

    async def _data_valid(self, state: DemoAnalysisState) -> dict[str, Any]:
        """生成程序权威的 data_sufficiency 数据充足度节点，数据不足时供前置路由短路。"""
        if state["stage1"].precheck.passed:
            item = build_program_trace_node(
                judge_data_sufficiency(state["stage1_frame"]),
                frame=state["stage1_frame"],
            )
        else:
            # 预检未通过时手动构造否决节点
            from app.analysis.workflow.stage1.core.bar_identity import BarRange, assign_bar_refs
            refs = assign_bar_refs(
                state["stage1_frame"].bars,
                symbol=state["stage1_frame"].symbol,
                timeframe=state["stage1_frame"].timeframe,
            )
            item = {
                "node_id": "data_sufficiency",
                "question": "数据是否满足分析要求？",
                "answer": "否",
                "reason": state["stage1"].precheck.reason or "数据不足",
                "bar_range": BarRange(start=refs[-1], end=refs[0]).model_dump(mode="json") if refs else None,
                "source": "program",
            }
        return self._with_program_context(state, "data_valid", item)

    async def _stage1_features(self, state: DemoAnalysisState) -> dict[str, Any]:
        """一次计算 Stage1 LLM 所需的方向、Always-In、周期/混乱/动量程序特征。"""
        built = build_program_node_context(state["stage1_frame"])
        context = dict(state.get("stage1_program_context", {}))
        data_valid = context.get("data_valid")
        context.update(built)
        # 保留之前已有的 data_valid 结果
        if data_valid is not None:
            context["data_valid"] = data_valid
        return {"stage1_program_context": context}

    @staticmethod
    def _route_stage1_pre_llm(state: DemoAnalysisState) -> str:
        """data_sufficiency 通过才允许调用 Stage1 LLM，否则零调用进入终止分支。"""
        item = state.get("stage1_program_context", {}).get("data_valid", {})
        return "llm" if item.get("answer") == "是" else "terminal"

    async def _stage1_llm(self, state: DemoAnalysisState) -> dict[str, Any]:
        """执行唯一的 Stage1 LLM 请求，记录用量和原始响应，不承担解析或校验。"""
        attempt = int(state.get("stage1_attempt", 0)) + 1
        messages = build_original_stage1_messages(
            state["stage1_frame"], program_context=state.get("stage1_program_context")
        )
        retry_text = state.get("stage1_retry_text", "")
        response = await self._call_llm_for_stage(
            "stage1",
            messages[0]["content"],
            {"_user_prompt": messages[1]["content"] + retry_text, "_preserve_raw": True},
            analysis_id=str(state["analysis_id"]),
        )
        trace = list(state.get("trace", []))
        trace.append(f"stage1_llm:attempt_{attempt}")
        # 模型不可用时走降级路径
        if response is None:
            return {
                "stage1_attempt": attempt,
                "stage1_response": None,
                "stage1_model_called": False,
                "stage1_route": "fallback",
                "trace": trace,
            }
        self._record_usage(state, response, f"stage1_attempt_{attempt}")
        run_id = await persist_llm_response(
            response,
            analysis_id=str(state["analysis_id"]),
            stage="stage1",
            attempt=attempt,
            mode=state["query"].analysis_mode,
            symbol=state["query"].symbol,
            period=state["query"].period.value,
        )
        return {
            "stage1_attempt": attempt,
            "stage1_messages": messages,
            "stage1_response": response,
            "stage1_model_called": True,
            "stage1_run_id": run_id,
            "stage1_route": "parse",
            "trace": trace,
        }

    @staticmethod
    def _route_stage1_llm(state: DemoAnalysisState) -> str:
        """有模型响应则进入解析；无可用模型时沿用本地 Stage1 结果完成降级。"""
        return "parse" if state.get("stage1_response") is not None else "fallback"

    async def _stage1_parse(self, state: DemoAnalysisState) -> dict[str, Any]:
        """只解析 Stage1 原始 JSON；语法失败时生成重试反馈，不执行业务校验。"""
        response = state.get("stage1_response")
        # 合并 raw_content 和 reasoning_content 中的 JSON 文本
        raw = coalesce_model_json_text(
            response.raw_content if response is not None else "",
            response.reasoning_content if response is not None else "",
        )
        try:
            text = str(raw or "").strip()
            # 去除 markdown 代码块包裹
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            candidate = json.loads(text.strip())
            if not isinstance(candidate, dict):
                raise ValueError("顶层 JSON 必须是对象")
        except (json.JSONDecodeError, ValueError) as exc:
            # 语法解析失败：生成重试反馈
            error = ValidationError(category="a", stage="stage1", raw_text=str(raw or ""), message=str(exc))
            retry_text = "\n\n" + build_retry_feedback(
                error,
                stage="stage1",
                attempt=state.get("stage1_attempt", 1),
                max_attempts=2,
                frame=state["stage1_frame"],
                previous_raw=str(raw or ""),
            )
            return {"stage1_validation_error": error, "stage1_retry_text": retry_text, "stage1_route": "retry"}
        return {"stage1_candidate": candidate, "stage1_validation_error": None, "stage1_route": "validate"}

    @staticmethod
    def _route_stage1_parse(state: DemoAnalysisState) -> str:
        """解析成功进入综合校验；失败且未耗尽则重试，否则终止。"""
        if state.get("stage1_route") == "validate":
            return "validate"
        return "retry" if state.get("stage1_attempt", 0) < 2 else "terminal"

    async def _stage1_gate_validate(self, state: DemoAnalysisState) -> dict[str, Any]:
        """统一校验 cycle_identifiable/not_extreme_chaos/direction_decidable/background_near_term_coherent/momentum_enough、程序节点及跨字段一致性。"""
        response = state.get("stage1_response")
        raw = coalesce_model_json_text(
            response.raw_content if response is not None else "",
            response.reasoning_content if response is not None else "",
        )
        result = JsonValidator().validate("stage1", str(raw or ""), kline_frame=state["stage1_frame"])
        if isinstance(result, Ok):
            # 检查 AI 负责的 5 个 gate 节点是否齐全
            required = {"cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "momentum_enough"}
            present = {
                str(item.get("node_id", ""))
                for item in result.obj.get("gate_trace", [])
                if isinstance(item, dict)
            }
            missing = sorted(required - present)
            if missing:
                result = ValidationError(
                    category="b",
                    stage="stage1",
                    raw_text=str(raw or ""),
                    missing_fields=[f"gate_trace.{node_id}" for node_id in missing],
                    message="Stage1 缺少 AI 负责的 gate 节点",
                )
        if isinstance(result, Ok):
            # 校验通过：更新运行记录并进入组装
            await update_analysis_run(state.get("stage1_run_id"), status="validated", normalized_output=result.obj)
            return {"stage1_candidate": result.obj, "stage1_validation_error": None, "stage1_route": "finalize"}
        # 校验失败：记录错误并生成重试反馈
        errors = result.invalid_fields or result.missing_fields or [result.message]
        await update_analysis_run(state.get("stage1_run_id"), status="validation_failed", validation_errors=errors)
        retry_text = "\n\n" + build_retry_feedback(
            result,
            stage="stage1",
            attempt=state.get("stage1_attempt", 1),
            max_attempts=2,
            frame=state["stage1_frame"],
            previous_raw=str(raw or ""),
        )
        return {"stage1_validation_error": result, "stage1_retry_text": retry_text, "stage1_route": "retry"}

    @staticmethod
    def _route_stage1_validation(state: DemoAnalysisState) -> str:
        """综合校验成功进入组装；失败且未耗尽则回到同一 LLM node。"""
        if state.get("stage1_route") == "finalize":
            return "finalize"
        return "retry" if state.get("stage1_attempt", 0) < 2 else "terminal"

    async def _stage1_finalize(self, state: DemoAnalysisState) -> dict[str, Any]:
        """合并 AI 候选和程序权威节点，生成最终 Stage1Result 与可审计执行轨迹。"""
        candidate = state.get("stage1_candidate")
        stage1 = merge_original_stage1(state["stage1"], candidate) if candidate else state["stage1"]
        # 将 gate_trace 项写入决策执行审计
        execution = list(state.get("decision_execution", []))
        for item in stage1.gate_trace:
            payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            execution.append({"phase": "gate", "status": "evaluated", **payload})
        trace = list(state.get("trace", []))
        trace.extend(["stage1:completed", f"gate:{stage1.gate_result}"])
        return {"stage1": stage1, "decision_execution": execution, "trace": trace}

    @staticmethod
    def _route_stage1_result(state: DemoAnalysisState) -> str:
        """Stage1 放行时进入 Stage2；wait/unknown 时进入统一终止分支。"""
        return "stage2" if state["stage1"].gate_result == "proceed" else "terminal"

    async def _stage1_terminal(self, state: DemoAnalysisState) -> dict[str, Any]:
        """将预检失败、重试耗尽或未放行统一组装为不调用 Stage2 LLM 的 WAIT。"""
        stage1 = state.get("stage1")
        # 重试耗尽或路由到 terminal 时标记 Stage1 为失败
        if stage1 is not None and state.get("stage1_route") in {"retry", "terminal"}:
            stage1.result_kind = "failed"
            stage1.gate_result = "unknown"
            stage1.failure_subtype = "retry_exhausted"
            stage1.risk_warning = "Stage 1 校验失败或未放行"
        raw = state.get("raw_tradeability") or {}
        raw_failed = raw.get("passed") is False
        raw_reason = raw.get("reason") if raw_failed else None
        precheck_reason = state.get("local_precheck_reason")
        # 组装短路的 Stage2 WAIT 结果
        stage2 = Stage2Result(
            result_kind="short_circuit",
            terminal=TerminalResult(
                outcome="wait",
                reason=(
                    raw_reason
                    or precheck_reason
                    or (stage1.precheck.reason if stage1 is not None else None)
                    or "行情数据无效"
                    or "Stage 1 未放行"
                ),
                terminal_node=(
                    "prepare_context"
                    if raw_failed
                    or precheck_reason
                    or (stage1 is not None and not stage1.precheck.passed)
                    else "gate"
                ),
            ),
        )
        return {"stage2": stage2, "stage2_model_called": False}

    async def _run_stage1(self, state: DemoAnalysisState) -> dict[str, Any]:
        """降级路径中的 Stage1 执行，委托给 execute_original_stage1 完成完整流程。"""
        stage1 = state["stage1"]
        stage1_frame = state["stage1_frame"]

        async def stage1_call(system: str, payload: dict[str, Any]) -> LLMResponse | None:
            return await self._call_llm_for_stage("stage1", system, payload, analysis_id=str(state["analysis_id"]))

        stage1, stage1_model_called = await execute_original_stage1(
            stage1,
            stage1_frame,
            stage1_call,
            lambda result, label: self._record_usage(state, result, label),
            analysis_context={
                "analysis_id": str(state["analysis_id"]),
                "mode": state["query"].analysis_mode,
                "symbol": state["query"].symbol,
                "period": state["query"].period.value,
            },
        )
        trace = list(state.get("trace", []))
        trace.extend(["stage1:completed", f"gate:{stage1.gate_result}"])
        return {"stage1": stage1, "stage1_model_called": stage1_model_called, "trace": trace}

    async def _stage2_context(self, state: DemoAnalysisState) -> dict[str, Any]:
        """准备 Stage2 前置计算、Prompt 和统一校验共用的不可变上下文。"""
        frame = build_stage1_frame(
            state["indicator_source_bars"],
            state["resolved_symbol"],
            state["query"].period.value,
            visible_count=len(state["bars"]),
        )
        _, stage1_json, strategy_files = build_original_stage2_messages(frame, state["stage1"])
        context = {
            "stage1": stage1_json,
            "strategy_files": strategy_files,
            "symbol": state["resolved_symbol"],
            "period": state["query"].period.value,
        }
        return {
            "stage2_frame": frame,
            "stage2_stage1_json": stage1_json,
            "stage2_strategy_files": strategy_files,
            "stage2_context": context,
        }

    async def _stage2_precheck(self, state: DemoAnalysisState) -> dict[str, Any]:
        """一次完成信号/风控/下单硬门，并写入是否允许调用 Stage2 LLM 的路由。"""
        signal = build_signal_precheck(state["stage2_frame"], state["stage1"])
        risk = build_risk_precheck(state["stage2_frame"], state["stage1"], signal)
        order = build_order_method_precheck(state["stage1"], signal, risk)
        passed = all(item.get("passed") for item in (signal, risk, order))
        trace = list(state.get("trace", []))
        trace.append("stage2_precheck:passed" if passed else "stage2_precheck:failed")
        return {
            "signal_precheck": signal,
            "risk_precheck": risk,
            "order_method_precheck": order,
            "stage2_route": "llm" if passed else "terminal",
            "trace": trace,
        }

    @staticmethod
    def _route_stage2_precheck(state: DemoAnalysisState) -> str:
        """全部前置硬门通过才允许调用第二个 LLM。"""
        return "llm" if state.get("stage2_route") == "llm" else "terminal"

    async def _stage2_llm(self, state: DemoAnalysisState) -> dict[str, Any]:
        """执行唯一的 Stage2 LLM 请求，只记录响应、用量和运行信息。"""
        attempt = int(state.get("stage2_attempt", 0)) + 1
        precheck_context = {
            "signal": state["signal_precheck"],
            "risk": state["risk_precheck"],
            "order_method": state["order_method_precheck"],
        }
        messages, stage1_json, strategy_files = build_original_stage2_messages(
            state["stage2_frame"], state["stage1"], precheck_context=precheck_context
        )
        run_id = await start_analysis_run(
            analysis_id=str(state["analysis_id"]),
            stage="stage2",
            attempt=attempt,
            mode=state["query"].analysis_mode,
            symbol=state["query"].symbol,
            period=state["query"].period.value,
        )
        retry_text = state.get("stage2_retry_text", "")
        try:
            response = await self._call_llm_for_stage(
                "stage2",
                messages[0]["content"],
                {"_user_prompt": messages[1]["content"] + retry_text, "_preserve_raw": True},
                analysis_id=str(state["analysis_id"]),
            )
        except Exception as exc:
            await update_analysis_run(
                run_id, status="failed", validation_errors=[f"{type(exc).__name__}: {exc}"]
            )
            response = None
        trace = list(state.get("trace", []))
        trace.append(f"stage2_llm:attempt_{attempt}")
        update: dict[str, Any] = {
            "stage2_attempt": attempt,
            "stage2_messages": messages,
            "stage2_stage1_json": stage1_json,
            "stage2_strategy_files": strategy_files,
            "stage2_response": response,
            "stage2_run_id": run_id,
            "stage2_model_called": bool(response) or state.get("stage2_model_called", False),
            "trace": trace,
        }
        if response is not None:
            await attach_llm_response(run_id, response)
            self._record_usage(state, response, f"stage2_attempt_{attempt}")
        else:
            await update_analysis_run(run_id, status="failed", validation_errors=["当前没有可用的 Stage2 模型响应"])
        return update

    async def _stage2_parse(self, state: DemoAnalysisState) -> dict[str, Any]:
        """只解析 Stage2 原始 JSON，不执行 normalizer、决策计算或业务校验。"""
        response = state.get("stage2_response")
        raw = coalesce_model_json_text(
            response.raw_content if response is not None else "",
            response.reasoning_content if response is not None else "",
        )
        try:
            text = str(raw or "").strip()
            # 去除 markdown 代码块包裹
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            candidate = json.loads(text.strip())
            if not isinstance(candidate, dict):
                raise ValueError("顶层 JSON 必须是对象")
        except (json.JSONDecodeError, ValueError) as exc:
            # 语法解析失败：记录错误并生成重试反馈
            error = ValidationError(category="a", stage="stage2", raw_text=str(raw or ""), message=str(exc))
            await update_analysis_run(
                state.get("stage2_run_id"),
                status="validation_failed",
                validation_errors=[error.message],
            )
            retry_text = "\n\n" + build_retry_feedback(
                error,
                stage="stage2",
                attempt=state.get("stage2_attempt", 1),
                max_attempts=2,
                frame=state["stage2_frame"],
                previous_raw=str(raw or ""),
            )
            return {"stage2_validation_error": error, "stage2_retry_text": retry_text, "stage2_route": "retry"}
        return {"stage2_candidate": candidate, "stage2_validation_error": None, "stage2_route": "validate"}

    @staticmethod
    def _route_stage2_parse(state: DemoAnalysisState) -> str:
        """解析成功进入统一校验；失败最多回到同一 Stage2 LLM 一次。"""
        if state.get("stage2_route") == "validate":
            return "validate"
        return "retry" if state.get("stage2_attempt", 0) < 2 else "terminal"

    async def _stage2_valid(self, state: DemoAnalysisState) -> dict[str, Any]:
        """统一复算并校验 Stage2 的信号、三价、风险、订单方式和跨字段语义。"""
        response = state.get("stage2_response")
        raw = coalesce_model_json_text(
            response.raw_content if response is not None else "",
            response.reasoning_content if response is not None else "",
        )
        result = validate_original_stage2(
            str(raw or ""), frame=state["stage2_frame"], stage1_json=state["stage2_stage1_json"]
        )
        # 额外校验：下单方式必须在程序允许集合内
        if isinstance(result, Ok):
            allowed = set(state.get("order_method_precheck", {}).get("allowed_order_types", []))
            decision = result.obj.get("decision") if isinstance(result.obj.get("decision"), dict) else {}
            order_type = str(decision.get("order_type") or "不下单")
            if order_type != "不下单" and order_type not in allowed:
                result = ValidationError(
                    category="c",
                    stage="stage2",
                    raw_text=str(raw or ""),
                    invalid_fields=["decision.order_type"],
                    message=f"下单方式 {order_type} 不在程序允许集合 {sorted(allowed)} 中",
                )
        if isinstance(result, Ok):
            # 校验通过：更新运行记录并进入组装
            await update_analysis_run(state.get("stage2_run_id"), status="completed", normalized_output=result.obj)
            return {"stage2_candidate": result.obj, "stage2_validation_error": None, "stage2_route": "finalize"}
        # 校验失败：记录错误并生成重试反馈
        errors = result.invalid_fields or result.missing_fields or [result.message]
        await update_analysis_run(state.get("stage2_run_id"), status="validation_failed", validation_errors=errors)
        retry_text = "\n\n" + build_retry_feedback(
            result,
            stage="stage2",
            attempt=state.get("stage2_attempt", 1),
            max_attempts=2,
            frame=state["stage2_frame"],
            previous_raw=str(raw or ""),
        )
        return {"stage2_validation_error": result, "stage2_retry_text": retry_text, "stage2_route": "retry"}

    @staticmethod
    def _route_stage2_validation(state: DemoAnalysisState) -> str:
        """统一校验成功进入纯组装；失败最多重试一次，耗尽后终止。"""
        if state.get("stage2_route") == "finalize":
            return "finalize"
        return "retry" if state.get("stage2_attempt", 0) < 2 else "terminal"

    async def _stage2_finalize(self, state: DemoAnalysisState) -> dict[str, Any]:
        """只把已通过统一校验的候选组装成最终 Stage2Result 和审计轨迹。"""
        stage2 = build_stage2_result(
            state["stage2_candidate"], self._build_continuity(state.get("previous_context"))
        )
        # 将 decision_trace 项写入决策执行审计
        execution = list(state.get("decision_execution", []))
        for item in stage2.decision_trace:
            if isinstance(item, dict):
                execution.append({"phase": "decision", "status": "evaluated", **item})
        trace = list(state.get("trace", []))
        trace.extend([
            "stage2:strategies=" + ",".join(state.get("stage2_strategy_files", [])),
            f"stage2:{stage2.result_kind}",
        ])
        return {"stage2": stage2, "decision_execution": execution, "trace": trace}

    async def _stage2_terminal(self, state: DemoAnalysisState) -> dict[str, Any]:
        """组装 Stage2 前置失败或两次模型校验失败产生的统一 WAIT。"""
        # 收集所有失败的硬门原因
        failed_checks = [
            item.get("reason")
            for item in (
                state.get("signal_precheck", {}),
                state.get("risk_precheck", {}),
                state.get("order_method_precheck", {}),
            )
            if item and not item.get("passed")
        ]
        reason = next((text for text in failed_checks if text), None)
        if reason is None:
            reason = "Stage 2 模型连续返回无效结果，请在分析记录中查看原始响应"
        stage2 = Stage2Result(
            result_kind="short_circuit" if failed_checks else "live",
            decision=Decision(),
            continuity=self._build_continuity(state.get("previous_context")),
            terminal=TerminalResult(outcome="wait", reason=reason, terminal_node="stage2_precheck" if failed_checks else "stage2"),
        )
        trace = list(state.get("trace", []))
        trace.append(f"stage2:{stage2.result_kind}")
        return {"stage2": stage2, "trace": trace}

    async def _run_stage2_fallback(self, state: DemoAnalysisState) -> dict[str, Any]:
        """缺少 LangGraph 时按同一组真实节点顺序执行 Stage2，不复制业务逻辑。"""
        working: DemoAnalysisState = dict(state)
        working["graph_trail"] = list(state.get("graph_trail", []))
        working.update(await self._stage2_context(working))
        self._append_trail(working, "stage2_context")
        working.update(await self._stage2_precheck(working))
        self._append_trail(working, "stage2_precheck")
        # 前置硬门不通过则直接终止
        if self._route_stage2_precheck(working) == "terminal":
            update = await self._stage2_terminal(working)
            self._append_trail(working, "stage2_terminal")
            update["graph_trail"] = working["graph_trail"]
            return update
        # 最多重试 2 次：LLM -> 解析 -> 校验
        while int(working.get("stage2_attempt", 0)) < 2:
            working.update(await self._stage2_llm(working))
            self._append_trail(working, "stage2_llm")
            working.update(await self._stage2_parse(working))
            self._append_trail(working, "stage2_parse")
            parse_route = self._route_stage2_parse(working)
            if parse_route == "terminal":
                update = await self._stage2_terminal(working)
                self._append_trail(working, "stage2_terminal")
                update["graph_trail"] = working["graph_trail"]
                return update
            if parse_route == "retry":
                continue
            working.update(await self._stage2_valid(working))
            self._append_trail(working, "stage2_valid")
            validation_route = self._route_stage2_validation(working)
            if validation_route == "finalize":
                update = await self._stage2_finalize(working)
                self._append_trail(working, "stage2_finalize")
                update["graph_trail"] = working["graph_trail"]
                return update
            if validation_route == "terminal":
                update = await self._stage2_terminal(working)
                self._append_trail(working, "stage2_terminal")
                update["graph_trail"] = working["graph_trail"]
                return update
        # 重试耗尽后终止
        update = await self._stage2_terminal(working)
        self._append_trail(working, "stage2_terminal")
        update["graph_trail"] = working["graph_trail"]
        return update

    @staticmethod
    def _stage1_payload(snapshot: AnalysisSnapshot, stage1: Stage1Result) -> dict[str, Any]:
        """构造 Stage1 LLM 的完整请求 payload，包含输出格式说明、程序结果和行情数据。"""
        payload = {
            "required_output": {
                "cycle_position": "string",
                "direction": "bullish|bearish|neutral",
                "confidence": "integer 0-100",
                "detected_patterns": ["string"],
                "support_levels": ["number"],
                "resistance_levels": ["number"],
                "bar_by_bar_summary": [{"bar_ref": {"bar_timestamp": "ISO-8601", "timeframe": "1m|5m|...", "session": "CME|US_EQUITY_RTH", "day_index": "integer"}, "bar_type": "必须服从程序值", "role": "structure|signal|entry|confirmation|noise|trap|climax|test", "context_effect": "strengthens_bull|weakens_bull|strengthens_bear|weakens_bear|neutral|transition", "summary": "string"}],
                "gate_trace": [{"node_id": "严格依次1.2,not_extreme_chaos,direction_decidable,background_near_term_coherent,momentum_enough", "question": "string", "answer": "是|否|中性|等待|不适用", "reason": "string", "bar_range": {"start": "bar_ref", "end": "bar_ref"}}],
                "gate_result": "proceed|wait|unknown",
                "summary": "string",
                "risk_warning": "string",
                "override_requests": [{"node_id": "program_direction|always_in", "answer": "枚举", "override_reason": "string", "evidence": ["具体时间戳和日内开盘序号证据"]}],
            },
            "program_result": stage1.model_dump(mode="json"),
            "kline_data_newest_first": snapshot.market.indicators.get("per_bar", []),
            "indicators": {key: value for key, value in snapshot.market.indicators.items() if key != "per_bar"},
            "previous_context": snapshot.previous_context.model_dump(mode="json") if snapshot.previous_context else None,
            "symbol": snapshot.market.contract,
            "period": snapshot.market.period,
        }
        return payload

    @staticmethod
    def _stage2_payload(snapshot: AnalysisSnapshot, stage1: Stage1Result, previous: PreviousContext | None) -> dict[str, Any]:
        """构造 Stage2 LLM 的完整请求 payload，包含决策格式说明和 Stage1 结果。"""
        return {
            "required_output": {"outcome": "trade|reject|wait", "reason": "string", "order_type": "市价单|限价单|突破单|不下单", "direction": "long|short|null", "entry_price": "number|null", "stop_loss_price": "number|null", "take_profit_price": "number|null", "take_profit_price_2": "number|null", "estimated_win_rate": "integer|null", "entry_reason": "string|null"},
            "stage1": stage1.model_dump(mode="json"),
            "kline_data_newest_first": snapshot.market.indicators.get("per_bar", []),
            "indicators": {key: value for key, value in snapshot.market.indicators.items() if key != "per_bar"},
            "previous_context": previous.model_dump(mode="json") if previous else None,
        }

    @classmethod
    def _build_llm_stage2(cls, content: dict[str, Any], previous: PreviousContext | None) -> Stage2Result:
        """从 LLM 原始 JSON 构建 Stage2Result，包含三价合法性校验。"""
        outcome = content.get("outcome") if content.get("outcome") in {"trade", "reject", "wait"} else "wait"
        order_type = str(content.get("order_type") or "不下单")
        decision = Decision()
        # 只在 outcome=trade 且有有效下单方式时构建决策
        if outcome == "trade" and order_type != "不下单":
            decision = Decision(
                order_type=order_type, direction=content.get("direction"), entry_price=content.get("entry_price"),
                stop_loss_price=content.get("stop_loss_price"), take_profit_price=content.get("take_profit_price"),
                take_profit_price_2=content.get("take_profit_price_2"), estimated_win_rate=content.get("estimated_win_rate"),
                entry_reason=content.get("entry_reason"),
            )
            # 校验四价是否齐全及方向逻辑是否正确
            prices = [decision.entry_price, decision.stop_loss_price, decision.take_profit_price, decision.take_profit_price_2]
            valid = all(price is not None for price in prices)
            if decision.direction == "long":
                # 多头：止损 < 入场 < 止盈1 < 止盈2
                valid = valid and decision.stop_loss_price < decision.entry_price < decision.take_profit_price < decision.take_profit_price_2
            elif decision.direction == "short":
                # 空头：止盈2 < 止盈1 < 入场 < 止损
                valid = valid and decision.take_profit_price_2 < decision.take_profit_price < decision.entry_price < decision.stop_loss_price
            else:
                valid = False
            # 三价不合法时降级为 reject
            if not valid:
                outcome, order_type, decision = "reject", "不下单", Decision()
        return Stage2Result(
            result_kind="live", decision=decision, continuity=cls._build_continuity(previous),
            terminal=TerminalResult(outcome=outcome, reason=str(content.get("reason") or "模型未提供理由")[:1200], terminal_node="prohibition_scan"),
        )

    @staticmethod
    def _record_usage(state: DemoAnalysisState, result: LLMResponse, stage: str) -> None:
        """记录 LLM token 用量到全局统计。"""
        append_usage(TokenUsageRecord(
            analysis_id=str(state["analysis_id"]), model_id=result.model_id, model=result.model,
            mode=f"{state['query'].analysis_mode}:{stage}", symbol=state["query"].symbol, period=state["query"].period.value,
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
        ))

    async def _write_memory(self, state: DemoAnalysisState) -> dict[str, Any]:
        """将本轮 Stage1/Stage2 结果保存到记忆存储，供下一轮恢复。"""
        summary = {
            "stage1": state["stage1"].model_dump(mode="json"),
            "stage2": state["stage2"].model_dump(mode="json"),
            "bars_since_previous": 1,
        }
        memory_key = state["memory_key"]
        await self.memory_store.save(memory_key, summary)
        trace = list(state.get("trace", []))
        trace.append("memory:saved")
        return {"memory_summary": str(summary), "trace": trace}

    @staticmethod
    def _memory_key(query: HistoricalQuery) -> str:
        """根据查询参数生成记忆存储的唯一键。"""
        return f"{query.dataset}:{query.symbol}:{query.period.value}:{query.start.isoformat()}:{query.end.isoformat()}"

    @staticmethod
    def _build_stage1(
        analysis: BasicAnalysis,
        bars: list[Bar],
        previous: PreviousContext | None,
        indicators: dict[str, Any] | None = None,
        *,
        symbol: str,
        timeframe: str,
    ) -> Stage1Result:
        """基于本地程序特征构建初始 Stage1Result，包含预检、周期判断、gate_trace 等。"""
        minimum_bars = 20  # 最少需要 20 根已收盘 K 线
        program_features = (indicators or {}).get("program_features", {})
        current_ema = program_features.get("ema20_current")
        current_atr = program_features.get("atr14_current")
        # 数据不足时返回失败的 Stage1Result
        if len(bars) < minimum_bars or current_ema is None or current_atr is None:
            failure_type = "bar_count_insufficient" if len(bars) < minimum_bars else "indicators_all_nan"
            reason = f"已收盘 K 线不足 {minimum_bars} 根" if len(bars) < minimum_bars else "EMA20 与 ATR14 指标预热不足"
            return Stage1Result(
                result_kind="failed",
                precheck=PrecheckResult(
                    passed=False,
                    failure_type=failure_type,
                    reason=reason,
                    closed_bar_count=len(bars),
                ),
                confidence=0,
                gate_result="unknown",
                failure_subtype="insufficient_information",
            )

        # 计算效率比率以判断周期位置
        price_range = analysis.period_high - analysis.period_low
        body_move = abs(analysis.latest_close - analysis.first_open)
        efficiency = body_move / price_range if price_range else 0
        if efficiency >= 0.55:
            cycle_position = "trend"
        elif efficiency <= 0.15:
            cycle_position = "trading_range"
        else:
            cycle_position = "normal_channel"
        confidence = min(90, max(50, round(50 + efficiency * 40)))
        # 读取程序 gate 结果
        program_gate = program_features.get("program_gate", {})
        direction_node = program_gate.get("program_direction", {})
        always_in_node = program_gate.get("always_in", {})
        program_direction = direction_node.get("direction")
        # 程序方向覆盖本地分析方向
        if program_direction in {"bullish", "bearish", "neutral"}:
            analysis.direction = Direction(program_direction)
        # 与上一轮对比检测变化字段
        old_stage1 = previous.stage1 if previous else None
        changed_fields = []
        if old_stage1:
            if old_stage1.get("direction") != analysis.direction.value:
                changed_fields.append("direction")
            if old_stage1.get("cycle_position") != cycle_position:
                changed_fields.append("cycle_position")
        refs = assign_timestamp_refs(
            [bar.timestamp for bar in bars],
            symbol=symbol,
            timeframe=timeframe,
        )

        def recent_range(count: int) -> BarRange:
            """取最近 count 根 K 线的时间戳范围。"""
            selected = refs[-min(count, len(refs)):]
            return BarRange(start=selected[0], end=selected[-1])

        bar_range = recent_range(len(refs))
        # 构建 gate_trace：混合程序节点和 AI 节点
        trace = [
            GateTraceItem(node_id="data_sufficiency", question="数据是否满足分析要求", answer="是", bar_range=bar_range, source="program"),
            GateTraceItem(node_id="cycle_identifiable", question="当前市场周期是否可识别", answer="是", bar_range=bar_range, source="ai"),
            GateTraceItem(node_id="not_extreme_chaos", question="市场是否并非极端混乱", answer="是", bar_range=recent_range(5), source="ai"),
            GateTraceItem(node_id="direction_decidable", question="当前方向是否可判断", answer="中性" if analysis.direction.value == "neutral" else "是", bar_range=recent_range(5), source="ai"),
            GateTraceItem(node_id="program_direction", question="程序方向判断是否成立", answer=direction_node.get("answer", "中性"), reason=f"程序五信号方向分数={direction_node.get('score', 0)}", bar_range=recent_range(8), source="program"),
            GateTraceItem(node_id="always_in", question="Always-In 背景是否明确", answer=always_in_node.get("answer", "中性"), reason=f"程序 Always-In={always_in_node.get('always_in', 'neutral')}", bar_range=recent_range(20), source="program"),
            GateTraceItem(node_id="background_near_term_coherent", question="长程背景与近期方向是否可协调", answer="中性", bar_range=bar_range, source="ai"),
            GateTraceItem(node_id="momentum_enough", question="当前惯性是否足以继续评估", answer="中性", reason="惯性不作为闸门阻断条件，进入阶段二提高交易门槛", bar_range=recent_range(5), source="ai"),
        ]
        return Stage1Result(
            result_kind="live",
            precheck=PrecheckResult(passed=True, closed_bar_count=len(bars)),
            cycle_position=cycle_position,
            direction=analysis.direction,
            confidence=confidence,
            support_levels=[analysis.period_low],
            resistance_levels=[analysis.period_high],
            gate_trace=trace,
            gate_result="proceed",
            incremental_delta=IncrementalDelta(
                changed=bool(changed_fields),
                summary="、".join(changed_fields) + "发生变化" if changed_fields else ("与上一轮核心结构一致" if old_stage1 else "首次分析，无上一轮结果"),
                changed_fields=changed_fields,
            ),
        )

    @staticmethod
    def _build_continuity(previous: PreviousContext | None) -> dict[str, Any]:
        """构建连续性上下文，记录上一轮交易是否仍活跃等信息。"""
        return {
            "previous_plan_active": bool(previous and previous.stage2 and previous.stage2.get("terminal", {}).get("outcome") == "trade"),
            "bars_since_previous": previous.bars_since_previous if previous else None,
            "guard_triggered": False,
            "reason": None,
        }

    async def _assemble_reviews(self, state: DemoAnalysisState) -> dict[str, Any]:
        """组装交易复盘结果。"""
        review_result = self._build_reviews(state["snapshot"], state["stage2"])
        trace = list(state.get("trace", []))
        trace.append("reviews:built")
        return {"review_result": review_result, "trace": trace}

    @staticmethod
    def _build_reviews(snapshot: AnalysisSnapshot, stage2: Stage2Result) -> list[TradeReviewResult]:
        """逐笔构建交易复盘，计算 MFE/MAE/捕获率并发现问题与优势。"""
        results: list[TradeReviewResult] = []
        for trade in snapshot.trades:
            # 筛选持仓区间内的 K 线
            bars = [bar for bar in snapshot.market.bars if trade.entered_at <= bar.timestamp <= trade.exited_at]
            if not bars:
                results.append(TradeReviewResult(
                    trade_id=trade.trade_id,
                    execution_metrics={"pnl": trade.reported_pnl, "mfe": None, "mae": None},
                    comparison={"recommended_direction": stage2.decision.direction, "matched_plan": False},
                    issues=[{"type": "missing_market_window", "severity": "high", "evidence": "交易持仓区间内没有可用 K 线"}],
                    improvements=["确认交易时间与行情时区一致"],
                    summary="缺少交易持仓区间行情，无法完成价格路径复盘。",
                ))
                continue
            # 根据方向计算最大有利波动(MFE)和最大不利波动(MAE)
            if trade.direction == "long":
                mfe = max(bar.high - trade.entry_price for bar in bars)
                mae = min(bar.low - trade.entry_price for bar in bars)
                realized = trade.exit_price - trade.entry_price
            else:
                mfe = max(trade.entry_price - bar.low for bar in bars)
                mae = min(trade.entry_price - bar.high for bar in bars)
                realized = trade.entry_price - trade.exit_price
            capture_ratio = realized / mfe if mfe > 0 else None
            issues = []
            improvements = []
            strengths = []
            # 不利波动大于有利波动
            if mae < 0 and abs(mae) > max(mfe, 0):
                issues.append({"type": "adverse_excursion", "severity": "high", "evidence": "最大不利波动大于最大有利波动"})
                improvements.append("入场前以结构位限定最大允许风险")
            # 捕获率不足 30%
            if capture_ratio is not None and capture_ratio < 0.3:
                issues.append({"type": "low_capture", "severity": "medium", "evidence": "实际收益不足最大有利波动的 30%"})
                improvements.append("预先定义移动止损或分批止盈规则")
            if realized > 0:
                strengths.append("交易实现正向价格收益")
            results.append(TradeReviewResult(
                trade_id=trade.trade_id,
                execution_metrics={
                    "pnl": trade.reported_pnl,
                    "price_pnl": round(realized * trade.size, 6),
                    "mfe": round(mfe, 6),
                    "mae": round(mae, 6),
                    "capture_ratio": round(capture_ratio, 4) if capture_ratio is not None else None,
                },
                comparison={
                    "actual_direction": trade.direction,
                    "recommended_direction": stage2.decision.direction,
                    "matched_plan": stage2.terminal.outcome == "trade" and stage2.decision.direction == trade.direction,
                },
                issues=issues,
                strengths=strengths,
                improvements=improvements,
                summary="；".join(item["evidence"] for item in issues) or "未发现明显的执行效率问题。",
            ))
        return results

    def _to_response(self, state: DemoAnalysisState) -> DemoAnalysisResponse:
        """将最终状态转换为 API 响应对象，并写入审计记录。"""
        completed_at = datetime.now(timezone.utc)
        append_stage1_audit({
            "analysis_id": str(state["analysis_id"]),
            "started_at": state["started_at"].isoformat(),
            "completed_at": completed_at.isoformat(),
            "mode": state["query"].analysis_mode,
            "symbol": state["resolved_symbol"],
            "period": state["query"].period.value,
            "stage1_model_called": state.get("stage1_model_called", False),
            "stage1": state["stage1"].model_dump(mode="json"),
        })
        return DemoAnalysisResponse(
            query=state["query"],
            resolved_symbol=state["resolved_symbol"],
            analysis=state["analysis"],
            bars=state["bars"],
            analysis_id=str(state["analysis_id"]),
            status="completed",
            snapshot=state["snapshot"],
            stage1=state["stage1"],
            stage2=state["stage2"],
            review_result=state.get("review_result") or None,
            audit=AnalysisAudit(
                started_at=state["started_at"],
                completed_at=completed_at,
                stage1_model_called=state.get("stage1_model_called", False),
                stage2_model_called=state.get("stage2_model_called", False),
                warnings=[] if state.get("stage1_model_called") else ["未配置当前大模型，使用确定性分析"],
                graph_trail=list(state.get("graph_trail", [])),
            ),
        )


async def run_demo_analysis_workflow(
    provider: MassiveHistoricalProvider,
    query: HistoricalQuery,
    memory_store: AnalysisMemoryStore = DEFAULT_MEMORY_STORE,
) -> DemoAnalysisResponse:
    """创建并同步执行工作流，返回最终分析响应。"""
    workflow = DemoAnalysisWorkflow(provider=provider, memory_store=memory_store)
    return await workflow.invoke(query)


async def stream_demo_analysis_workflow(
    provider: MassiveHistoricalProvider,
    query: HistoricalQuery,
    memory_store: AnalysisMemoryStore = DEFAULT_MEMORY_STORE,
):
    """创建并流式执行工作流，逐个 yield 进度/推理/结果事件。"""
    workflow = DemoAnalysisWorkflow(provider=provider, memory_store=memory_store)
    async for event in workflow.stream(query):
        yield event


async def build_stage1_debug_preview(
    provider: MassiveHistoricalProvider,
    query: HistoricalQuery,
    memory_store: AnalysisMemoryStore = DEFAULT_MEMORY_STORE,
) -> DebugPreview:
    """构建 Stage1 调试预览，展示 LLM 输入和预计 token 数量，供调试确认。"""
    resolved_symbol = resolve_contract_symbol(query.symbol, query.start, query.end)
    # 扩展 50 根预热 K 线
    warmup_start = query.start - timedelta(seconds=PERIOD_SECONDS[query.period] * 50)
    warmup_query = query.model_copy(update={"symbol": resolved_symbol, "start": warmup_start})
    indicator_source_bars = aggregate_bars(await provider.get_range(warmup_query), query.period)
    bars = [bar for bar in indicator_source_bars if query.start <= bar.timestamp <= query.end]
    analysis = analyze_bars(bars)
    previous_raw = await memory_store.load(DemoAnalysisWorkflow._memory_key(query))
    previous = PreviousContext.model_validate(previous_raw) if previous_raw else None
    snapshot = AnalysisSnapshot(
        analysis_id=uuid.uuid4(), mode=AnalysisMode(query.analysis_mode),
        trigger=AnalysisTrigger(occurred_at=datetime.now(timezone.utc)), query=query,
        market=MarketSnapshot(
            symbol=query.symbol, contract=resolved_symbol, period=query.period.value, bars=bars,
            indicators=build_market_indicators(
                    indicator_source_bars, bars,
                    symbol=resolved_symbol, timeframe=query.period.value
            ),
        ),
        trades=[TradeSnapshot.model_validate(trade.model_dump()) for trade in query.trades],
        previous_context=previous, generated_at=datetime.now(timezone.utc),
    )
    stage1 = DemoAnalysisWorkflow._build_stage1(
        analysis,
        bars,
        previous,
        snapshot.market.indicators,
        symbol=resolved_symbol,
        timeframe=query.period.value,
    )
    frame = build_stage1_frame(
        indicator_source_bars,
        resolved_symbol,
        query.period.value,
        visible_count=len(bars),
    )
    messages = build_original_stage1_messages(frame)
    llm_input = {"stage": "stage1", "messages": messages}
    serialized = json.dumps(llm_input, ensure_ascii=False, separators=(",", ":"))
    settings = get_public_settings()
    model = next((item for item in settings.models if item.id == settings.active_model_id), None)
    return DebugPreview(
        confirmation_id=str(uuid.uuid4()), requires_confirmation=settings.debug_enabled, model=model,
        llm_input=llm_input, estimated_prompt_tokens=max(1, (len(serialized) + 3) // 4),
    )
