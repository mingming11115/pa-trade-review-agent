from __future__ import annotations

import json
import logging
import uuid
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import sys
from time import perf_counter

from datetime import datetime

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Settings, get_settings
from app.core.errors import AppError, redact_text
from app.core.logging_context import (
    TraceContextFilter,
    normalize_trace_id,
    reset_trace_context,
    set_trace_context,
)
from app.core.models import DemoAnalysisResponse, HealthResponse, HistoricalQuery, Period
from app.market.service import (
    CollectionStatus,
    LocalFirstMarketProvider,
    MarketRange,
    MinuteCollector,
    PERIOD_MINUTES,
    backfill_range,
    collection_statuses,
    validate_kline_symbol,
    query_market_range,
    AlertPublic,
    AlertRuleInput,
    AlertRulePublic,
    create_alert_rule,
    list_alert_rules,
    list_alerts,
    mark_alert_read,
    remove_alert_rule,
    update_alert_rule,
)
from app.personal.service import (
    DebugPreview,
    PersonalSettingsPublic,
    PersonalSettingsUpdate,
    TokenUsageSummary,
    get_public_settings,
    get_usage,
    save_settings,
)
from app.market.provider import MassiveHistoricalProvider
from app.market.live import RealtimeTradeCollector
from app.analysis.workflow.graph import build_stage1_debug_preview, run_demo_analysis_workflow, stream_demo_analysis_workflow
from app.followup.service import FollowupRequest, stream_followup_turn, list_followup_history, FollowupMessagePublic
from app.llm.langfuse_tracer import shutdown as shutdown_langfuse
from app.llm.tools import list_llm_tools
from app.core.database import ensure_schema, get_session
from app.admin.prompts import (
    OrchestrationView,
    PromptFileDocument,
    PromptFileUpdate,
    get_orchestration,
    get_prompt_file,
    save_prompt_file,
    PromptVersionPublic,
    list_prompt_versions,
    record_prompt_version,
    rollback_prompt_version,
    PromptVersionDiff,
    diff_prompt_versions,
)
from app.analysis.execution.runs import list_analysis_runs
from app.analysis.tasks.models import AnalysisRunPublic
from app.analysis.history.service import (
    AnalysisHistorySummary,
    get_analysis_history,
    list_analysis_history,
    persist_analysis_result,
)
from app.auth.service import LoginInput, UserPublic, audit, bootstrap_admin, current_user, limit_expensive, limiter, login, logout, require_admin
from app.analysis.routes import router as analysis_task_router
from app.trades.service import TradeCreate, TradeImportBatchResponse, TradeImportResult, TradePreview, TradeResponse, TradeUpdate, create_trade, delete_trade, import_trades, list_import_batches, list_recent_trades, parse_trade_file, query_trades, update_trade
from sqlalchemy.ext.asyncio import AsyncSession


settings = get_settings()


def _configure_logging() -> None:
    """配置全局日志：同时输出到控制端和按小时轮转的日志文件。"""
    root_logger = logging.getLogger()
    if getattr(root_logger, "_pa_logging_configured", False):
        return

    level = getattr(logging, settings.log_level, logging.INFO)  # 根据配置解析日志级别
    root_logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s "
        "trace_id=%(trace_id)s task_id=%(task_id)s "
        "analysis_id=%(analysis_id)s execution_id=%(execution_id)s %(message)s"
    )

    # 控制台日志处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.addFilter(TraceContextFilter())
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 按小时轮转的文件日志处理器，保留最近 48 个文件
    log_path = Path(settings.log_file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="H",
        interval=1,
        backupCount=48,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setLevel(level)
    file_handler.addFilter(TraceContextFilter())
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    setattr(root_logger, "_pa_logging_configured", True)  # 标记已配置，防止重复


_configure_logging()
logger = logging.getLogger("pa-demo")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期上下文：启动时初始化数据库与后台采集任务，关闭时优雅停止。"""
    await ensure_schema()
    await bootstrap_admin(settings)
    collector = MinuteCollector(settings)
    if settings.collector_enabled:
        # 历史分钟级 K 线采集器
        historical_task = asyncio.create_task(
            collector.run_forever(), name="pa-minute-collector"
        )
        logger.info(
            "minute collector started symbols=%s lookback=%sm catchup=%sm",
            ",".join(settings.collector_symbols),
            settings.collector_lookback_minutes,
            settings.collector_max_catchup_minutes,
        )
    else:
        historical_task = None
        logger.warning("minute collector disabled (COLLECTOR_ENABLED=false)")
    realtime_collector = RealtimeTradeCollector(settings)
    # 实时逐笔交易采集器（仅在配置启用且存在 API Key 时启动）
    realtime_task = asyncio.create_task(
        realtime_collector.run_forever(), name="pa-realtime-trade-collector"
    ) if settings.live_ws_enabled and settings.hist_api_key else None
    try:
        yield
    finally:
        # 优雅关闭所有后台任务
        if historical_task:
            collector.stop()
        if realtime_task:
            realtime_collector.stop()
        tasks = [task for task in (historical_task, realtime_task) if task]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for task, result in zip(tasks, results, strict=True):
                if isinstance(result, BaseException):
                    logger.error(
                        "background task stopped name=%s error=%s",
                        task.get_name(),
                        type(result).__name__,
                    )
        # Flush and shut down Langfuse client to avoid losing buffered traces
        shutdown_langfuse()

app = FastAPI(title="PA Market Analysis Demo", version="0.2.0", lifespan=lifespan)
app.include_router(analysis_task_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Trace-ID", "X-Request-ID", "X-Market-Request-Kind"],
    expose_headers=["X-Trace-ID", "X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Bind a safe application trace id for the complete HTTP request."""
    trace_id = normalize_trace_id(
        request.headers.get("X-Trace-ID") or request.headers.get("X-Request-ID")
    )
    request.state.trace_id = trace_id
    request.state.request_id = trace_id
    tokens = set_trace_context(trace_id)
    is_market_bars = request.url.path == "/api/v1/market/bars"
    started_at = perf_counter()
    try:
        if is_market_bars:
            logger.info(
                "market_bars request_started request_id=%s kind=%s symbol=%s period=%s start=%s end=%s include_partial=%s",
                trace_id,
                request.headers.get("X-Market-Request-Kind", "unknown"),
                request.query_params.get("symbol"),
                request.query_params.get("period"),
                request.query_params.get("start"),
                request.query_params.get("end"),
                request.query_params.get("include_partial", "false"),
            )
        logger.info(
            "http_request_started method=%s path=%s",
            request.method,
            request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "http_request_failed method=%s path=%s duration_ms=%d error_type=%s",
                request.method,
                request.url.path,
                round((perf_counter() - started_at) * 1000),
                type(exc).__name__,
            )
            if is_market_bars:
                logger.exception(
                    "market_bars request_id=%s kind=%s status=exception duration_ms=%d symbol=%s period=%s start=%s end=%s include_partial=%s error_type=%s",
                    trace_id,
                    request.headers.get("X-Market-Request-Kind", "unknown"),
                    round((perf_counter() - started_at) * 1000),
                    request.query_params.get("symbol"),
                    request.query_params.get("period"),
                    request.query_params.get("start"),
                    request.query_params.get("end"),
                    request.query_params.get("include_partial", "false"),
                    type(exc).__name__,
                )
            return JSONResponse(
                status_code=500,
                content={
                    "code": "internal_error",
                    "message": "服务器内部错误",
                    "trace_id": trace_id,
                    "request_id": trace_id,
                },
                headers={"X-Trace-ID": trace_id, "X-Request-ID": trace_id},
            )
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Request-ID"] = trace_id
        duration_ms = round((perf_counter() - started_at) * 1000)
        if is_market_bars:
            logger.info(
                "market_bars request_finished request_id=%s kind=%s status=%d duration_ms=%d symbol=%s period=%s start=%s end=%s include_partial=%s bars=%s",
                trace_id,
                request.headers.get("X-Market-Request-Kind", "unknown"),
                response.status_code,
                duration_ms,
                request.query_params.get("symbol"),
                request.query_params.get("period"),
                request.query_params.get("start"),
                request.query_params.get("end"),
                request.query_params.get("include_partial", "false"),
                response.headers.get("X-Market-Bar-Count", "unknown"),
            )
        logger.info(
            "http_request_finished method=%s path=%s status=%d duration_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
    finally:
        reset_trace_context(tokens)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """处理业务层抛出的 AppError，返回结构化错误 JSON。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "trace_id": request.state.trace_id,
            "request_id": request.state.request_id,
            "details": exc.details,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理请求参数校验失败，返回字段级错误明细。"""
    details = [
        {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "invalid_request",
            "message": "Request validation failed",
            "trace_id": request.state.trace_id,
            "request_id": request.state.request_id,
            "details": details,
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底处理未捕获异常，避免泄露内部错误细节。"""
    safe_type = redact_text(type(exc).__name__, (settings.hist_api_key,))
    logger.exception("unhandled_error=%s", safe_type)
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "An unexpected server error occurred",
            "trace_id": request.state.trace_id,
            "request_id": request.state.request_id,
            "details": [],
        },
    )


def get_provider():
    """返回本地优先的市场数据提供者实例。"""
    return LocalFirstMarketProvider(MassiveHistoricalProvider(get_settings()))


def get_upstream_provider() -> MassiveHistoricalProvider:
    """返回上游历史数据提供者实例。"""
    return MassiveHistoricalProvider(get_settings())


@app.get("/api/v1/health", response_model=HealthResponse)
async def health(current: Settings = Depends(get_settings)) -> HealthResponse:
    """健康检查接口，返回服务与基础设施状态。"""
    transport = "https" if current.hist_base_url.startswith("https://") else "http"
    return HealthResponse(
        status="ok",
        api_version="v1",
        provider_configured=current.provider_configured,
        provider_transport=transport,
        storage_status="postgresql_configured" if current.database_url else "not_configured",
        auth_required=current.auth_required,
    )


@app.post("/api/v1/auth/login", response_model=UserPublic)
async def auth_login(payload: LoginInput, response: Response, request: Request) -> UserPublic:
    """用户登录接口，校验凭据并设置会话 Cookie。"""
    limiter.check(f"login:{request.client.host if request.client else 'unknown'}", 10, 60, datetime.now().timestamp())
    return await login(payload, response)


@app.post("/api/v1/auth/logout", status_code=204)
async def auth_logout(response: Response) -> None:
    """用户登出接口，清除会话 Cookie。"""
    await logout(response)


@app.get("/api/v1/auth/me", response_model=UserPublic)
async def auth_me(user: UserPublic = Depends(current_user)) -> UserPublic:
    """获取当前登录用户信息。"""
    return user


@app.get("/api/v1/market/status", response_model=list[CollectionStatus])
async def market_status() -> list[CollectionStatus]:
    """获取所有已配置标的的行情采集状态。"""
    await ensure_schema()
    return await collection_statuses()


@app.get("/api/v1/llm/tools")
async def llm_tools() -> list[dict[str, object]]:
    """列出所有已注册的 LLM 工具定义（OpenAI 兼容格式）。"""
    return list_llm_tools()


# Cap chart / API window size to avoid oversized upstream pulls (~2000 bars).
MARKET_BARS_MAX_COUNT = 2000


@app.get("/api/v1/market/bars", response_model=MarketRange)
async def market_bars(
    response: Response,
    symbol: str = Query(..., min_length=1, max_length=100),
    period: str = Query(..., pattern="^(1m|5m|15m|30m|1h|4h|1d)$"),
    start: datetime = Query(...),
    end: datetime = Query(...),
    include_partial: bool = Query(False),
    provider: LocalFirstMarketProvider = Depends(get_provider),
) -> MarketRange:
    """查询指定标的和时间段内的 K 线数据，包含覆盖元数据。"""
    if end <= start:
        raise AppError("invalid_market_range", "结束时间必须晚于开始时间", 422)
    minutes = PERIOD_MINUTES[period]
    span_minutes = (end - start).total_seconds() / 60
    if span_minutes > minutes * MARKET_BARS_MAX_COUNT:
        raise AppError(
            "market_range_too_large",
            f"单次查询最多约 {MARKET_BARS_MAX_COUNT} 根 {period} K 线",
            422,
        )
    await ensure_schema()
    try:
        resolved = validate_kline_symbol(symbol)
    except ValueError as exc:
        raise AppError("unsupported_kline_symbol", str(exc), 422) from exc
    query = HistoricalQuery(symbol=resolved, period=Period(period), start=start, end=end)
    if not include_partial:
        await provider.get_range(query)
    market_range = await query_market_range(resolved, period, start, end, include_partial=include_partial)
    response.headers["X-Market-Bar-Count"] = str(len(market_range.bars))
    return market_range


@app.post("/api/v1/market/backfill", response_model=MarketRange)
async def market_backfill(
    query: HistoricalQuery,
    provider: MassiveHistoricalProvider = Depends(get_upstream_provider),
    user: UserPublic = Depends(require_admin),
) -> MarketRange:
    """手动回填指定标的和时间段的历史 K 线数据（需管理员权限）。"""
    await ensure_schema()
    result = await backfill_range(provider, query)
    await audit(user, "market_backfill", f"{query.symbol}:{query.period.value}")
    return result


@app.get("/api/v1/alert-rules", response_model=list[AlertRulePublic])
async def alert_rules(_: UserPublic = Depends(current_user)) -> list[AlertRulePublic]:
    """列出所有已配置的价格预警规则。"""
    return await list_alert_rules()


@app.post("/api/v1/alert-rules", response_model=AlertRulePublic)
async def add_alert_rule(payload: AlertRuleInput, _: UserPublic = Depends(current_user)) -> AlertRulePublic:
    """创建新的价格预警规则。"""
    return await create_alert_rule(payload)


@app.put("/api/v1/alert-rules/{rule_id}", response_model=AlertRulePublic)
async def edit_alert_rule(rule_id: uuid.UUID, payload: AlertRuleInput, _: UserPublic = Depends(current_user)) -> AlertRulePublic:
    """更新指定 ID 的价格预警规则。"""
    return await update_alert_rule(rule_id, payload)


@app.delete("/api/v1/alert-rules/{rule_id}", status_code=204)
async def delete_alert_rule(rule_id: uuid.UUID, _: UserPublic = Depends(current_user)) -> None:
    """删除指定 ID 的价格预警规则。"""
    await remove_alert_rule(rule_id)


@app.get("/api/v1/alerts", response_model=list[AlertPublic])
async def alerts(limit: int = Query(200, ge=1, le=500), _: UserPublic = Depends(current_user)) -> list[AlertPublic]:
    """查询最近的触发预警记录。"""
    return await list_alerts(limit)


@app.patch("/api/v1/alerts/{alert_id}/read", response_model=AlertPublic)
async def read_alert(alert_id: uuid.UUID, _: UserPublic = Depends(current_user)) -> AlertPublic:
    """将指定预警标记为已读。"""
    return await mark_alert_read(alert_id)


@app.post("/api/v1/demo/analyze")
async def demo_analyze(
    query: HistoricalQuery,
    provider: MassiveHistoricalProvider = Depends(get_provider),
    _: UserPublic = Depends(limit_expensive),
):
    """执行完整的两阶段价格行为分析（非流式），并持久化结果。"""
    result = await run_demo_analysis_workflow(provider, query)
    await persist_analysis_result(result)
    return result


@app.post("/api/v1/demo/analyze/stream")
async def demo_analyze_stream(
    query: HistoricalQuery,
    provider: MassiveHistoricalProvider = Depends(get_provider),
    _: UserPublic = Depends(limit_expensive),
):
    """以流式方式执行两阶段分析，逐步推送分析进度和最终结果。"""
    async def events():
        try:
            async for event in stream_demo_analysis_workflow(provider, query):
                if event["type"] == "result":
                    await persist_analysis_result(DemoAnalysisResponse.model_validate(event["result"]))
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        except AppError as exc:
            yield json.dumps({"type": "error", "code": exc.code, "message": exc.message, "details": exc.details}, ensure_ascii=False) + "\n"
        except Exception as exc:
            logger.exception("streaming analysis failed")
            yield json.dumps({"type": "error", "code": "internal_error", "message": f"分析失败：{type(exc).__name__}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/analyses", response_model=list[AnalysisHistorySummary])
async def analyses(
    limit: int = Query(100, ge=1, le=500),
    symbol: str | None = Query(None, max_length=100),
    period: str | None = Query(None, pattern="^(1m|5m|15m|30m|1h|4h|1d)$"),
    mode: str | None = Query(None, pattern="^(trade_review|historical|realtime)$"),
    _: UserPublic = Depends(current_user),
) -> list[AnalysisHistorySummary]:
    """列出分析历史摘要，支持按标的、周期、模式过滤。"""
    return await list_analysis_history(limit=limit, symbol=symbol, period=period, mode=mode)


@app.get("/api/v1/analyses/{analysis_id}")
async def analysis_detail(analysis_id: str, _: UserPublic = Depends(current_user)):
    """根据 analysis_id 获取单次分析的完整详情。"""
    return await get_analysis_history(analysis_id)


@app.post("/api/v1/analyses/{analysis_id}/followup/stream")
async def analysis_followup_stream(
    analysis_id: str,
    payload: FollowupRequest,
    _: UserPublic = Depends(limit_expensive),
):
    """对已有分析结果进行流式追问对话，实时返回 LLM 回复片段。"""
    result = await get_analysis_history(analysis_id)

    async def events():
        try:
            async for event in stream_followup_turn(analysis_id=analysis_id, result=result, request=payload):
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        except AppError as exc:
            yield json.dumps(
                {"type": "error", "code": exc.code, "message": exc.message, "details": exc.details},
                ensure_ascii=False,
            ) + "\n"
        except Exception:
            logger.exception("followup stream failed")
            yield json.dumps(
                {"type": "error", "code": "internal_error", "message": "追问失败"},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/analyses/{analysis_id}/followup/history", response_model=list[FollowupMessagePublic])
async def analysis_followup_history(analysis_id: str, _: UserPublic = Depends(current_user)) -> list[FollowupMessagePublic]:
    """获取指定分析的追问对话历史消息。"""
    return await list_followup_history(analysis_id)


@app.post("/api/v1/analysis/debug-preview", response_model=DebugPreview)
async def analysis_debug_preview(
    query: HistoricalQuery,
    provider: MassiveHistoricalProvider = Depends(get_provider),
    _: UserPublic = Depends(current_user),
) -> DebugPreview:
    """生成阶段一调试预览数据（不调用 LLM），用于检查输入数据质量。"""
    return await build_stage1_debug_preview(provider, query)


@app.get("/api/v1/personal/settings", response_model=PersonalSettingsPublic)
async def personal_settings(_: UserPublic = Depends(current_user)) -> PersonalSettingsPublic:
    """获取当前用户的个人模型配置。"""
    return get_public_settings()


@app.put("/api/v1/personal/settings", response_model=PersonalSettingsPublic)
async def update_personal_settings(update: PersonalSettingsUpdate, user: UserPublic = Depends(current_user)) -> PersonalSettingsPublic:
    """更新当前用户的个人模型配置，并记录审计日志。"""
    saved = save_settings(update)
    await audit(user, "model_settings_update", user.username, {"model_count": len(saved.models)})
    return saved


@app.get("/api/v1/personal/token-usage", response_model=TokenUsageSummary)
async def personal_token_usage(limit: int = Query(200, ge=1, le=1000), _: UserPublic = Depends(current_user)) -> TokenUsageSummary:
    """获取当前用户的 Token 用量统计。"""
    return get_usage(limit)


@app.get("/api/v1/admin/orchestration", response_model=OrchestrationView)
async def admin_orchestration(_: UserPublic = Depends(require_admin)) -> OrchestrationView:
    """获取编排视图（管理员专用），展示系统配置与提示词编排概览。"""
    return get_orchestration()


@app.get("/api/v1/admin/prompt-file", response_model=PromptFileDocument)
async def admin_prompt_file(filename: str = Query(..., min_length=1, max_length=200), _: UserPublic = Depends(require_admin)) -> PromptFileDocument:
    """读取指定提示词文件的内容（管理员专用）。"""
    return get_prompt_file(filename)


@app.put("/api/v1/admin/prompt-file", response_model=PromptFileDocument)
async def update_admin_prompt_file(
    update: PromptFileUpdate,
    filename: str = Query(..., min_length=1, max_length=200),
    user: UserPublic = Depends(require_admin),
) -> PromptFileDocument:
    """更新指定提示词文件的内容，并记录版本（管理员专用）。"""
    document = save_prompt_file(filename, update)
    await record_prompt_version(document, user.username)
    await audit(user, "prompt_update", filename, {"version": document.version})
    return document


@app.get("/api/v1/admin/prompt-versions", response_model=list[PromptVersionPublic])
async def admin_prompt_versions(filename: str = Query(..., min_length=1, max_length=200), _: UserPublic = Depends(require_admin)) -> list[PromptVersionPublic]:
    """列出指定提示词文件的历史版本（管理员专用）。"""
    return await list_prompt_versions(filename)


@app.post("/api/v1/admin/prompt-versions/{version_id}/rollback", response_model=PromptFileDocument)
async def admin_prompt_rollback(version_id: uuid.UUID, filename: str = Query(..., min_length=1, max_length=200), user: UserPublic = Depends(require_admin)) -> PromptFileDocument:
    """将指定提示词文件回滚到指定历史版本（管理员专用）。"""
    document = await rollback_prompt_version(filename, version_id, user.username)
    await audit(user, "prompt_rollback", filename, {"version": document.version})
    return document


@app.get("/api/v1/admin/prompt-versions/diff/{left_id}/{right_id}", response_model=PromptVersionDiff)
async def admin_prompt_diff(left_id: uuid.UUID, right_id: uuid.UUID, filename: str = Query(..., min_length=1, max_length=200), _: UserPublic = Depends(require_admin)) -> PromptVersionDiff:
    """对比两个提示词文件版本之间的差异（管理员专用）。"""
    return await diff_prompt_versions(filename, left_id, right_id)


@app.get("/api/v1/admin/analysis-runs", response_model=list[AnalysisRunPublic])
async def admin_analysis_runs(
    limit: int = Query(100, ge=1, le=500),
    analysis_id: str | None = Query(None, max_length=64),
) -> list[AnalysisRunPublic]:
    """列出分析运行记录（管理员专用），可按 analysis_id 过滤。"""
    return await list_analysis_runs(limit, analysis_id)


async def _read_trade_upload(file: UploadFile) -> bytes:
    """读取上传的交易文件内容，校验文件大小不超过 10MB。"""
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise AppError("trade_file_too_large", "交易文件不能超过 10MB", 413)
    return content


@app.post("/api/v1/trades/import/preview", response_model=TradePreview)
async def preview_trade_import(file: UploadFile = File(...)) -> TradePreview:
    """预览交易文件解析结果，不入库。"""
    return parse_trade_file(file.filename or "trades.xlsx", await _read_trade_upload(file))


@app.post("/api/v1/trades/import/confirm", response_model=TradeImportResult)
async def confirm_trade_import(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> TradeImportResult:
    """确认导入交易文件，将解析结果写入数据库。"""
    await ensure_schema()
    return await import_trades(session, file.filename or "trades.xlsx", await _read_trade_upload(file))


@app.get("/api/v1/trades", response_model=list[TradeResponse])
async def list_trades(
    start: datetime = Query(...),
    end: datetime = Query(...),
    symbol: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[TradeResponse]:
    """按时间范围和标的查询交易记录。"""
    if end <= start:
        raise AppError("invalid_trade_range", "结束时间必须晚于开始时间", 422)
    await ensure_schema()
    return [
        TradeResponse.model_validate(trade)
        for trade in await query_trades(session, start, end, symbol)
    ]


@app.get("/api/v1/trades/recent", response_model=list[TradeResponse])
async def recent_trades(
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[TradeResponse]:
    """查询最近的交易记录。"""
    await ensure_schema()
    return [TradeResponse.model_validate(trade) for trade in await list_recent_trades(session, limit)]


@app.post("/api/v1/trades", response_model=TradeResponse)
async def add_trade(payload: TradeCreate, session: AsyncSession = Depends(get_session)) -> TradeResponse:
    """手动创建单笔交易记录。"""
    await ensure_schema()
    return TradeResponse.model_validate(await create_trade(session, payload))


@app.patch("/api/v1/trades/{trade_id}", response_model=TradeResponse)
async def edit_trade(trade_id: uuid.UUID, payload: TradeUpdate, session: AsyncSession = Depends(get_session)) -> TradeResponse:
    """更新指定交易记录。"""
    await ensure_schema()
    return TradeResponse.model_validate(await update_trade(session, trade_id, payload))


@app.delete("/api/v1/trades/{trade_id}", status_code=204)
async def remove_trade(trade_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: UserPublic = Depends(current_user)) -> None:
    """删除指定交易记录，并记录审计日志。"""
    await ensure_schema()
    await delete_trade(session, trade_id)
    await audit(user, "trade_delete", str(trade_id))


@app.get("/api/v1/trades/imports", response_model=list[TradeImportBatchResponse])
async def trade_import_history(limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_session)) -> list[TradeImportBatchResponse]:
    """查询交易文件的导入批次历史。"""
    await ensure_schema()
    return [TradeImportBatchResponse.model_validate(item) for item in await list_import_batches(session, limit)]
