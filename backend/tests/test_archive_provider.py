from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ProviderError
from app.market.provider import MassiveHistoricalProvider

START = datetime(2026,9,30,10,50,tzinfo=UTC)
END = datetime(2026,9,30,11,tzinfo=UTC)

def provider(handler):
    settings = Settings(app_env="development", hist_base_url="http://provider.test", hist_api_key="test-key", hist_allow_insecure_http=True, hist_timeout_seconds=1, hist_max_retries=0, frontend_origin="http://localhost")
    return MassiveHistoricalProvider(settings, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_continuous_request_retains_instrument_and_integer_precision():
    def handler(request):
        payload = parse_qs(request.content.decode())
        assert payload["symbols"] == ["MES.c.0"]
        assert payload["stype_in"] == ["continuous"]
        assert payload["schema"] == ["ohlcv-1m"]
        return httpx.Response(200,json=[{"hd":{"ts_event":"1790765400000000000","instrument_id":123,"publisher_id":1},"open":"100000000001","high":"102000000001","low":"99000000001","close":"101000000001","volume":7}])
    batch = await provider(handler).get_archive_range("MES", START, END)
    assert batch.covered_until == END
    assert batch.records[0].open_raw == 100000000001
    assert batch.records[0].instrument_id == 123


@pytest.mark.asyncio
async def test_successful_empty_window_advances_coverage():
    batch = await provider(lambda r:httpx.Response(200,json=[])).get_archive_range("MGC",START,END)
    assert batch.records == [] and batch.covered_until == END


@pytest.mark.asyncio
async def test_unpublished_window_does_not_advance_coverage():
    batch = await provider(lambda r:httpx.Response(422,json={"detail":{"case":"data_start_after_available_end"}})).get_archive_range("MES",START,END)
    assert batch.records == [] and batch.covered_until is None


@pytest.mark.asyncio
async def test_clamped_response_only_advances_complete_published_minutes():
    replies = iter([httpx.Response(422,json={"detail":{"case":"data_end_after_available_end","payload":{"available_end":"2026-09-30T10:55:30Z"}}}),httpx.Response(200,json=[])])
    batch = await provider(lambda r:next(replies)).get_archive_range("MES",START,END)
    assert batch.covered_until == datetime(2026,9,30,10,55,tzinfo=UTC)


@pytest.mark.asyncio
async def test_auth_failure_is_not_an_empty_success():
    with pytest.raises(ProviderError) as exc:
        await provider(lambda r:httpx.Response(401)).get_archive_range("MES",START,END)
    assert exc.value.code == "provider_auth_failed"


@pytest.mark.asyncio
async def test_fractional_end_requests_only_complete_minutes():
    """A provider honoring the requested end must not trigger our range guard."""
    start = datetime(2026, 1, 22, 3, 45, 58, 787000, tzinfo=UTC)
    end = datetime(2026, 1, 22, 13, 45, 58, 787000, tzinfo=UTC)
    complete = datetime(2026, 1, 22, 13, 44, tzinfo=UTC)
    incomplete = datetime(2026, 1, 22, 13, 45, tzinfo=UTC)

    def handler(request):
        payload = parse_qs(request.content.decode())
        requested_end = datetime.fromisoformat(payload["end"][0])
        return httpx.Response(200, json=[
            {"hd": {"ts_event": str(int(timestamp.timestamp()) * 1_000_000_000),
                    "instrument_id": 123, "publisher_id": 1},
             "open": "100000000000", "high": "102000000000",
             "low": "99000000000", "close": "101000000000", "volume": 7}
            for timestamp in (complete, incomplete) if timestamp < requested_end
        ])

    batch = await provider(handler).get_archive_range("ES", start, end)
    assert [record.timestamp_utc for record in batch.records] == [complete]
    assert batch.covered_until == incomplete


@pytest.mark.asyncio
async def test_window_without_complete_minute_does_not_fetch_or_advance():
    def handler(request):
        pytest.fail("No upstream request is needed for an empty complete-minute window")

    batch = await provider(handler).get_archive_range(
        "ES", datetime(2026, 1, 22, 13, 45, 10, tzinfo=UTC),
        datetime(2026, 1, 22, 13, 45, 58, tzinfo=UTC),
    )
    assert batch.records == []
    assert batch.covered_until is None
