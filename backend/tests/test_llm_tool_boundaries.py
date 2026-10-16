from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.auth import service as auth
from app.core.models import Bar
from app.llm import tools
from app.market import service as market

UTC = timezone.utc


@pytest.mark.parametrize('minute', [5, 7])
def test_latest_bars_contains_complete_last_closed_bucket(monkeypatch, minute: int) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 30, 10, minute, tzinfo=UTC)

    origin = datetime(2026, 9, 30, 9, 55, tzinfo=UTC)
    source = [Bar(timestamp=origin + timedelta(minutes=i), open=i, high=i+1, low=i, close=i+1, volume=1) for i in range(15)]

    async def read(symbol, period, start, end):
        return [bar for bar in source if start <= bar.timestamp < end]

    monkeypatch.setattr(tools, 'datetime', Clock)
    monkeypatch.setattr(tools, '_provider', lambda: SimpleNamespace(get_range=AsyncMock()))
    monkeypatch.setattr(market, 'read_stored_bars', read)
    monkeypatch.setattr('app.market.archive.archive_range_is_covered', AsyncMock(return_value=False))
    result = asyncio.run(tools.MarketGetLatestBarsTool().execute(symbol='ES', period='5m', count=2))
    assert result['count'] == 2
    assert [bar['timestamp'] for bar in result['bars']] == ['2026-09-30T09:55:00Z', '2026-09-30T10:00:00Z']
    assert [bar['volume'] for bar in result['bars']] == [5, 5]
    assert result['bars'][-1]['close'] == 10


def test_history_tools_inherit_authenticated_user_and_ignore_model_identity(monkeypatch) -> None:
    owner = uuid.uuid4()
    outsider = uuid.uuid4()
    user = SimpleNamespace(id=owner, username='owner', role='user', active=True)

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, model, key):
            if model is auth.UserSession:
                return SimpleNamespace(user_id=owner, expires_at=datetime.now(UTC) + timedelta(hours=1))
            return user

    async def history_list(*, user_id=None, **kwargs):
        return [SimpleNamespace(model_dump=lambda **kwargs: {'run_id': 'owned-run'})] if user_id == owner else []

    async def history_detail(run_id, *, user_id=None):
        return {'owner': str(user_id)}

    monkeypatch.setattr(auth, 'get_settings', lambda: SimpleNamespace(auth_required=True))
    monkeypatch.setattr(auth, 'ensure_schema', AsyncMock())
    monkeypatch.setattr(auth, 'SessionFactory', Session)
    monkeypatch.setattr(tools, 'list_analysis_history', history_list)
    monkeypatch.setattr(tools, 'get_analysis_history', history_detail)

    async def run():
        await auth.current_user('valid-token')
        listing = await tools.AnalysisListHistoryTool().execute(user_id=str(outsider))
        detail = await tools.AnalysisGetDetailTool().execute(run_id='owned-run', user_id=str(outsider))
        assert listing['items'] == [{'run_id': 'owned-run'}]
        assert detail['result'] == {'owner': str(owner)}

    asyncio.run(run())


def test_history_tool_identity_is_isolated_across_concurrent_tasks(monkeypatch) -> None:
    from app.core.user_context import reset_current_user_id, set_current_user_id

    async def history_detail(run_id, *, user_id=None):
        await asyncio.sleep(0)
        return {'owner': str(user_id)}

    monkeypatch.setattr(tools, 'get_analysis_history', history_detail)

    async def task(owner):
        token = set_current_user_id(owner)
        try:
            await asyncio.sleep(0)
            # A background task created after authentication inherits its owner.
            child = asyncio.create_task(tools.AnalysisGetDetailTool().execute(run_id='run'))
            return (await child)['result']['owner']
        finally:
            reset_current_user_id(token)

    async def run():
        owners = [uuid.uuid4(), uuid.uuid4(), None]
        results = await asyncio.gather(*(task(owner) for owner in owners))
        assert results == [str(owner) for owner in owners]
        assert (await tools.AnalysisGetDetailTool().execute(run_id='run'))['result']['owner'] == 'None'

    asyncio.run(run())


@pytest.mark.parametrize('auth_required', [False, True])
def test_authentication_clears_previous_tool_identity(monkeypatch, auth_required: bool) -> None:
    from app.core.errors import AppError
    from app.core.user_context import get_current_user_id, reset_current_user_id, set_current_user_id

    monkeypatch.setattr(auth, 'get_settings', lambda: SimpleNamespace(auth_required=auth_required))

    async def run():
        token = set_current_user_id(uuid.uuid4())
        try:
            if auth_required:
                with pytest.raises(AppError):
                    await auth.current_user(None)
            else:
                assert (await auth.current_user(None)).id is None
            assert get_current_user_id() is None
        finally:
            reset_current_user_id(token)

    asyncio.run(run())
