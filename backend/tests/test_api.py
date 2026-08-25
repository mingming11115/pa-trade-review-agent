from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import logging
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from app.analysis.workflow.graph import AnalysisMemoryStore, run_demo_analysis_workflow
from app.main import app, get_provider, settings
from app.llm.tools import MARKET_BARS_TOOL_NAME
from app.core.models import Bar


@pytest.fixture(autouse=True)
def disable_real_llm(monkeypatch):
    async def no_model(*args, **kwargs):
        return None
    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", no_model)


class StubProvider:
    async def get_range(self, query):
        return [
            Bar(
                timestamp=datetime(2022, 6, 6, 0, 0, tzinfo=timezone.utc),
                open=100,
                high=102,
                low=99,
                close=101,
                volume=10,
            ),
            Bar(
                timestamp=datetime(2022, 6, 6, 0, 1, tzinfo=timezone.utc),
                open=101,
                high=103,
                low=100,
                close=102,
                volume=11,
            ),
        ]


def test_health_does_not_expose_provider_key() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert "hist_api_key" not in response.text
    assert "provider_configured" in response.json()
    assert response.json()["storage_status"] == "postgresql_configured"


def test_live_market_bars_serve_local_data_without_synchronous_backfill(monkeypatch) -> None:
    from app.market.service import Coverage, MarketRange

    class CountingProvider:
        calls = 0

        async def get_range(self, _query):
            type(self).calls += 1
            return []

    async def local_range(symbol, period, start, end, **_kwargs):
        return MarketRange(
            symbol=symbol,
            period=period,
            bars=[],
            coverage=Coverage(
                source_period="1m",
                expected_bars=400,
                actual_bars=398,
                complete=False,
                missing_buckets=[],
            ),
        )

    async def no_schema_work():
        return None

    monkeypatch.setattr("app.main.query_market_range", local_range)
    monkeypatch.setattr("app.main.ensure_schema", no_schema_work)
    app.dependency_overrides[get_provider] = CountingProvider
    try:
        response = TestClient(app).get(
            "/api/v1/market/bars",
            params={
                "symbol": "ES",
                "period": "5m",
                "start": "2026-08-18T00:27:33Z",
                "end": "2026-08-18T07:07:33Z",
                "include_partial": "true",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert CountingProvider.calls == 0


def test_market_bars_logs_correlated_request_summary(monkeypatch, caplog) -> None:
    from app.market.service import Coverage, MarketRange

    async def local_range(symbol, period, start, end, **_kwargs):
        return MarketRange(
            symbol=symbol,
            period=period,
            bars=[],
            coverage=Coverage(source_period="1m", expected_bars=400, actual_bars=398, complete=False),
        )

    async def no_schema_work():
        return None

    monkeypatch.setattr("app.main.query_market_range", local_range)
    monkeypatch.setattr("app.main.ensure_schema", no_schema_work)
    caplog.set_level(logging.INFO, logger="pa-demo")

    response = TestClient(app).get(
        "/api/v1/market/bars",
        params={
            "symbol": "ES",
            "period": "5m",
            "start": "2026-08-18T00:27:33Z",
            "end": "2026-08-18T07:07:33Z",
            "include_partial": "true",
        },
        headers={"X-Request-ID": "frontend-request-id", "X-Market-Request-Kind": "live_poll"},
    )

    assert response.status_code == 200
    assert "market_bars request_finished request_id=frontend-request-id kind=live_poll status=200" in caplog.text
    assert "symbol=ES period=5m" in caplog.text
    assert "bars=0" in caplog.text
    assert "duration_ms=" in caplog.text


def test_request_trace_is_returned_and_logged(caplog) -> None:
    caplog.set_level(logging.INFO, logger="pa-demo")

    response = TestClient(app).get(
        "/api/v1/health",
        headers={"X-Trace-ID": "web-trace-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == "web-trace-1"
    assert response.headers["X-Request-ID"] == "web-trace-1"
    assert any(
        getattr(record, "trace_id", None) == "web-trace-1"
        and record.getMessage().startswith("http_request_finished")
        for record in caplog.records
    )


def test_legacy_request_id_is_used_as_trace_id() -> None:
    response = TestClient(app).get(
        "/api/v1/health",
        headers={"X-Request-ID": "legacy-request-1"},
    )

    assert response.headers["X-Trace-ID"] == "legacy-request-1"
    assert response.headers["X-Request-ID"] == "legacy-request-1"


def test_concurrent_requests_keep_trace_ids_isolated() -> None:
    def request(trace_id: str) -> str:
        return TestClient(app).get("/api/v1/health", headers={"X-Trace-ID": trace_id}).headers["X-Trace-ID"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        observed = list(pool.map(request, ["concurrent-trace-a", "concurrent-trace-b"]))

    assert observed == ["concurrent-trace-a", "concurrent-trace-b"]


def test_unsafe_trace_id_is_replaced_without_echoing_input(caplog) -> None:
    caplog.set_level(logging.INFO, logger="pa-demo")

    response = TestClient(app).get(
        "/api/v1/health",
        headers={"X-Trace-ID": "unsafe trace value"},
    )

    generated = response.headers["X-Trace-ID"]
    assert str(UUID(generated)) == generated
    assert response.headers["X-Request-ID"] == generated
    assert "unsafe trace value" not in caplog.text


def test_validation_error_contains_trace_and_compatible_request_id() -> None:
    response = TestClient(app).get(
        "/api/v1/market/bars",
        headers={"X-Trace-ID": "error-trace-1"},
    )

    assert response.status_code == 422
    assert response.json()["trace_id"] == "error-trace-1"
    assert response.json()["request_id"] == "error-trace-1"


def test_cors_exposes_trace_headers_to_browser_clients() -> None:
    response = TestClient(app).get(
        "/api/v1/health",
        headers={"Origin": settings.frontend_origin},
    )

    exposed = response.headers.get("Access-Control-Expose-Headers", "").lower()
    assert "x-trace-id" in exposed
    assert "x-request-id" in exposed


def test_unhandled_error_contains_trace_id(monkeypatch) -> None:
    async def fail_query(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.main.query_market_range", fail_query)
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/market/bars",
        params={"symbol": "ES", "period": "5m", "start": "2026-08-18T00:00:00Z", "end": "2026-08-18T01:00:00Z"},
        headers={"X-Trace-ID": "error-500-trace"},
    )

    assert response.status_code == 500
    assert response.json()["trace_id"] == "error-500-trace"


def test_llm_tools_endpoint_lists_market_tool() -> None:
    response = TestClient(app).get("/api/v1/llm/tools")

    assert response.status_code == 200
    tools = response.json()
    assert any(tool["function"]["name"] == MARKET_BARS_TOOL_NAME for tool in tools)


def test_demo_analysis_workflow_persists_memory() -> None:
    memory_store = AnalysisMemoryStore()
    provider = StubProvider()
    from app.core.models import HistoricalQuery

    query = HistoricalQuery(
        dataset="GLBX.MDP3",
        symbol="ES",
        period="5m",
        start=datetime(2022, 6, 6, 0, 0, tzinfo=timezone.utc),
        end=datetime(2022, 6, 6, 1, 0, tzinfo=timezone.utc),
    )

    import anyio

    async def run_once():
        return await run_demo_analysis_workflow(
            provider,
            query,
            memory_store=memory_store,
        )

    first = anyio.run(run_once)
    second = anyio.run(run_once)

    assert first.analysis.bar_count == 1
    assert second.analysis.bar_count == 1
    assert first.stage1.gate_result == "unknown"
    assert first.stage1.precheck.failure_type == "bar_count_insufficient"
    assert first.stage2.result_kind == "short_circuit"
    assert first.stage2.terminal.outcome == "wait"
    assert first.snapshot.previous_context is None
    assert second.snapshot.previous_context is not None
    assert anyio.run(
        memory_store.load,
        "GLBX.MDP3:ES:5m:2022-06-06T00:00:00+00:00:2022-06-06T01:00:00+00:00",
    )


def test_analysis_contract_proceeds_with_sufficient_bars() -> None:
    class SufficientProvider:
        async def get_range(self, query):
            return [
                Bar(
                    timestamp=datetime(2022, 6, 6, 0, minute, tzinfo=timezone.utc),
                    open=100 + minute,
                    high=102 + minute,
                    low=99 + minute,
                    close=101 + minute,
                    volume=10,
                )
                for minute in range(40)
            ]

    app.dependency_overrides[get_provider] = SufficientProvider
    try:
        response = TestClient(app).post(
            "/api/v1/demo/analyze",
            json={
                "symbol": "ES",
                "period": "1m",
                "start": "2022-06-06T00:00:00Z",
                "end": "2022-06-06T01:00:00Z",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    latest = response.json()["bars"][-1]
    assert latest["timeframe"] == "1m"
    assert latest["session"] == "CME"
    assert latest["day_index"] >= 1
    body = response.json()
    assert body["stage1"]["gate_result"] == "proceed"
    assert body["stage2"]["result_kind"] == "live"
    assert body["stage2"]["terminal"]["outcome"] == "wait"
    assert body["audit"]["stage2_model_called"] is False


def test_trade_review_returns_execution_metrics() -> None:
    class ReviewProvider:
        async def get_range(self, query):
            return [
                Bar(timestamp=datetime(2022, 6, 6, 0, minute, tzinfo=timezone.utc), open=100, high=103, low=99, close=102, volume=10)
                for minute in range(20)
            ]

    app.dependency_overrides[get_provider] = ReviewProvider
    try:
        response = TestClient(app).post("/api/v1/demo/analyze", json={
            "symbol": "ES", "period": "1m", "start": "2022-06-06T00:00:00Z", "end": "2022-06-06T01:00:00Z",
            "analysis_mode": "trade_review",
            "trades": [{
                "trade_id": "trade-1", "symbol": "ES", "entered_at": "2022-06-06T00:00:00Z", "exited_at": "2022-06-06T00:04:00Z",
                "direction": "long", "entry_price": 100, "exit_price": 102, "size": 1, "reported_pnl": 100,
            }],
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    review = response.json()["review_result"][0]
    assert review["execution_metrics"]["mfe"] == 3
    assert review["execution_metrics"]["mae"] == -1


def test_debug_preview_contains_real_stage1_bars() -> None:
    app.dependency_overrides[get_provider] = StubProvider
    try:
        response = TestClient(app).post("/api/v1/analysis/debug-preview", json={
            "symbol": "ES", "period": "1m", "start": "2022-06-06T00:00:00Z", "end": "2022-06-06T01:00:00Z",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    llm_input = response.json()["llm_input"]
    assert llm_input["stage"] == "stage1"
    assert [item["role"] for item in llm_input["messages"]] == ["system", "user"]
    assert "提示词大纲_人设与思维方式" in llm_input["messages"][0]["content"]
    assert "品种:ES" in llm_input["messages"][1]["content"]
    assert "100.0000" in llm_input["messages"][1]["content"]
    assert "EMA20" in llm_input["messages"][1]["content"]
    assert "程序结构辅助特征" in llm_input["messages"][1]["content"]


def test_demo_analysis_validates_time_range() -> None:
    response = TestClient(app).post(
        "/api/v1/demo/analyze",
        json={
            "symbol": "ES",
            "period": "1m",
            "start": "2022-06-06T01:00:00Z",
            "end": "2022-06-06T00:00:00Z",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.headers["X-Request-ID"]
