from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.models import HistoricalQuery
from app.main import app, get_provider
from app.auth.service import current_user, limit_expensive


@pytest.mark.parametrize('path', ['/api/v1/demo/analyze', '/api/v1/demo/analyze/stream', '/api/v1/analysis/debug-preview'])
def test_public_analysis_requires_initialized_knowledge(monkeypatch, path):
    app.dependency_overrides[get_provider] = lambda: None
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=None)
    app.dependency_overrides[limit_expensive] = lambda: SimpleNamespace(id=None)
    async def unavailable(owner):
        raise AppError('knowledge_not_initialized', '请先导入提示词知识库', 503)
    async def unavailable_run(run_id, owner):
        return await unavailable(owner)
    monkeypatch.setattr('app.main.get_or_create_run_prompt_snapshot', unavailable_run, raising=False)
    monkeypatch.setattr('app.main.resolve_prompt_snapshot', unavailable, raising=False)
    try:
        response = TestClient(app).post(path, json={
            'symbol':'ES', 'period':'1m', 'start':'2022-01-01T00:00:00Z', 'end':'2022-01-02T00:00:00Z'})
    finally:
        for dep in (get_provider, current_user, limit_expensive):
            app.dependency_overrides.pop(dep, None)
    assert response.status_code == 503
    assert response.json()['code'] == 'knowledge_not_initialized'


@pytest.mark.anyio
async def test_nonstream_and_preview_pass_authenticated_snapshot(monkeypatch):
    from app.main import demo_analyze, analysis_debug_preview
    owner = uuid.uuid4()
    snapshot = object()
    user = SimpleNamespace(id=owner)
    output = object()
    query = HistoricalQuery.model_validate({'symbol':'ES', 'period':'1m', 'start':'2022-01-01T00:00:00Z', 'end':'2022-01-02T00:00:00Z'})
    async def resolve(user_id):
        assert user_id == owner
        return snapshot
    async def workflow(provider, input_query, *, prompt_snapshot, **kwargs):
        assert prompt_snapshot is snapshot and input_query is query
        return output
    async def persist(value):
        assert value is output
    async def resolve_run(run_id, user_id):
        return await resolve(user_id)
    monkeypatch.setattr('app.main.get_or_create_run_prompt_snapshot', resolve_run)
    monkeypatch.setattr('app.main.resolve_prompt_snapshot', resolve, raising=False)
    monkeypatch.setattr('app.main.run_demo_analysis_workflow', workflow)
    monkeypatch.setattr('app.main.build_stage1_debug_preview', workflow)
    monkeypatch.setattr('app.main.persist_analysis_result', persist)
    assert await demo_analyze(query, None, user) is output
    assert await analysis_debug_preview(query, None, user) is output
