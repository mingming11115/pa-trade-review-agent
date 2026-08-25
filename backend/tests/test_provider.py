from __future__ import annotations

import base64
from datetime import datetime, timezone

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ProviderError
from app.core.models import HistoricalQuery
from app.market.provider import MassiveHistoricalProvider


def settings(**overrides) -> Settings:
    values = {
        "app_env": "development",
        "hist_base_url": "http://provider.test",
        "hist_api_key": "test-key",
        "hist_allow_insecure_http": True,
        "hist_timeout_seconds": 1.0,
        "hist_max_retries": 0,
        "frontend_origin": "http://localhost:5173",
    }
    values.update(overrides)
    return Settings(**values)


def query() -> HistoricalQuery:
    return HistoricalQuery(
        dataset="GLBX.MDP3",
        symbol="ESM2",
        schema="ohlcv-1m",
        start=datetime(2022, 6, 6, 0, 0, tzinfo=timezone.utc),
        end=datetime(2022, 6, 6, 1, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_provider_normalizes_json_records_and_uses_basic_auth() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        expected = base64.b64encode(b"test-key:").decode()
        assert request.headers["Authorization"] == f"Basic {expected}"
        return httpx.Response(
            200,
            json=[
                {
                    "hd": {"ts_event": "1654473660000000000"},
                    "open": "101000000000",
                    "high": "103000000000",
                    "low": "100000000000",
                    "close": "102000000000",
                    "volume": "12",
                },
                {
                    "ts_event": "2022-06-06T00:00:00Z",
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "volume": 10,
                },
            ],
        )

    provider = MassiveHistoricalProvider(settings(), httpx.MockTransport(handler))
    bars = await provider.get_range(query())

    assert len(bars) == 2
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[-1].close == 102


@pytest.mark.parametrize(
    "raw_timestamp",
    [1654473600, 1654473600000, 1654473600000000, 1654473600000000000],
)
def test_provider_parses_epoch_timestamp_precisions(raw_timestamp: int) -> None:
    parsed = MassiveHistoricalProvider._parse_timestamp(
        {"timestamp": raw_timestamp}
    )

    assert parsed == datetime(2022, 6, 6, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_provider_supports_ndjson() -> None:
    body = "\n".join(
        [
            '{"timestamp":"2022-06-06T00:00:00Z","open":100,"high":102,"low":99,"close":101}',
            '{"timestamp":"2022-06-06T00:01:00Z","open":101,"high":103,"low":100,"close":102}',
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    provider = MassiveHistoricalProvider(settings(), transport)

    bars = await provider.get_range(query())

    assert len(bars) == 2


@pytest.mark.asyncio
async def test_provider_returns_empty_list_for_no_data() -> None:
    provider = MassiveHistoricalProvider(
        settings(),
        httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    assert await provider.get_range(query()) == []


@pytest.mark.asyncio
async def test_provider_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = MassiveHistoricalProvider(settings(), httpx.MockTransport(handler))

    with pytest.raises(ProviderError) as caught:
        await provider.get_range(query())

    assert caught.value.code == "provider_timeout"


@pytest.mark.asyncio
async def test_provider_retries_transient_timeout() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=[])

    provider = MassiveHistoricalProvider(
        settings(hist_max_retries=1), httpx.MockTransport(handler)
    )

    assert await provider.get_range(query()) == []
    assert attempts == 2


@pytest.mark.asyncio
async def test_provider_does_not_inherit_ambient_proxy_settings(monkeypatch) -> None:
    class CapturingClient:
        def __init__(self, **kwargs) -> None:
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs) -> httpx.Response:
            return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "AsyncClient", CapturingClient)

    provider = MassiveHistoricalProvider(settings())

    assert await provider.get_range(query()) == []


@pytest.mark.asyncio
async def test_provider_does_not_retry_auth_failure() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"message": "unauthorized"})

    provider = MassiveHistoricalProvider(
        settings(hist_max_retries=2), httpx.MockTransport(handler)
    )

    with pytest.raises(ProviderError) as caught:
        await provider.get_range(query())

    assert caught.value.code == "provider_auth_failed"
    assert attempts == 1


@pytest.mark.asyncio
async def test_provider_rejects_insecure_http_without_local_override() -> None:
    provider = MassiveHistoricalProvider(
        settings(hist_allow_insecure_http=False),
        httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    with pytest.raises(ProviderError) as caught:
        await provider.get_range(query())

    assert caught.value.code == "insecure_provider_transport"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (301, "provider_redirected"),
        (401, "provider_auth_failed"),
        (429, "provider_rate_limited"),
        (503, "provider_unavailable"),
    ],
)
async def test_provider_maps_upstream_errors(status_code: int, error_code: str) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code, json={"message": "upstream"})
    )
    provider = MassiveHistoricalProvider(settings(), transport)

    with pytest.raises(ProviderError) as caught:
        await provider.get_range(query())

    assert caught.value.code == error_code
    assert "test-key" not in caught.value.message


@pytest.mark.asyncio
async def test_provider_clamps_unpublished_end_and_retries_once() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.content.decode())
        if len(requests) == 1:
            return httpx.Response(422, json={
                "detail": {
                    "case": "data_end_after_available_end",
                    "payload": {
                        "available_end": "2022-06-06T00:40:00Z",
                        "end": "2022-06-06T01:00:00Z",
                    },
                }
            })
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    provider = MassiveHistoricalProvider(settings(), transport)

    assert await provider.get_range(query()) == []
    assert len(requests) == 2
    assert "end=2022-06-06T00%3A40%3A00Z" in requests[1]


@pytest.mark.asyncio
async def test_provider_returns_empty_when_start_after_available_end() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(422, json={
        "detail": {
            "case": "data_start_after_available_end",
            "payload": {
                "available_end": "2022-06-06T00:00:00Z",
                "start": "2022-06-06T00:00:00Z",
                "end": "2022-06-06T01:00:00Z",
            },
        }
    }))
    provider = MassiveHistoricalProvider(settings(), transport)

    assert await provider.get_range(query()) == []


@pytest.mark.asyncio
async def test_provider_logs_rejected_query_context_without_credentials(caplog) -> None:
    """422 日志丢失请求范围或泄露密钥时，本测试必须失败。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(422, json={
        "detail": {"case": "data_start_before_available_start", "payload": {"available_start": "2022-06-01T00:00:00Z"}}
    }))
    provider = MassiveHistoricalProvider(settings(), transport)

    with caplog.at_level("WARNING", logger="app.market.provider"), pytest.raises(ProviderError):
        await provider.get_range(query())

    text = caplog.text
    assert "data_start_before_available_start" in text
    assert "ESM2" in text
    assert "2022-06-06T00:00:00+00:00" in text
    assert "test-key" not in text


@pytest.mark.asyncio
async def test_provider_rejects_invalid_ohlc() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "timestamp": "2022-06-06T00:00:00Z",
                    "open": 100,
                    "high": 99,
                    "low": 98,
                    "close": 101,
                }
            ],
        )
    )
    provider = MassiveHistoricalProvider(settings(), transport)

    with pytest.raises(ProviderError) as caught:
        await provider.get_range(query())

    assert caught.value.code == "provider_invalid_data"
