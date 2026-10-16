from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from app.auth.service import UserPublic, current_user
from app.main import app
from test_knowledge_management import service
from test_knowledge_import import knowledge


@pytest.mark.anyio
async def test_personal_api_save_history_and_rollback(service):
    from app.knowledge.routes import get_knowledge_service
    owner = uuid.uuid4()
    app.dependency_overrides[current_user] = lambda: UserPublic(id=owner, username='normal', role='user', auth_required=True)
    app.dependency_overrides[get_knowledge_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            params = {'filename': '市场诊断框架.txt'}
            assert (await client.get('/api/v1/knowledge/orchestration')).status_code == 200
            document = (await client.get('/api/v1/knowledge/prompt', params=params)).json()
            result = await client.put('/api/v1/knowledge/prompt', params=params, json={'content':'本人修改', 'expected_version':document['version']})
            assert result.status_code == 200 and result.json()['revision'] == 2
            assert result.headers['x-trace-id']
            history = (await client.get('/api/v1/knowledge/versions', params=params)).json()
            baseline = history['items'][-1]
            restored = await client.post(f"/api/v1/knowledge/versions/{baseline['id']}/rollback", params=params, json={'expected_version':result.json()['version']})
            assert restored.status_code == 200 and restored.json()['content'] == document['content']
            assert (await client.put('/api/v1/knowledge/prompt', params=params, json={'content':'过期', 'expected_version':document['version']})).status_code == 409
            assert (await client.put('/api/v1/knowledge/prompt', params=params, json={'content':' ', 'expected_version':document['version']})).status_code == 422
            assert (await client.get('/api/v1/knowledge/prompt', params={'filename':'../secret'})).status_code == 404
            assert (await client.get('/api/v1/knowledge/versions', params={**params, 'limit':51})).status_code == 422
            app.dependency_overrides[current_user] = lambda: UserPublic(id=uuid.uuid4(), username='other', role='user', auth_required=True)
            assert (await client.post(f"/api/v1/knowledge/versions/{baseline['id']}/rollback", params=params, json={'expected_version':document['version']})).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_knowledge_service, None)


@pytest.mark.anyio
async def test_summary_lists_effective_versions_without_private_content(service):
    from app.knowledge.routes import get_knowledge_service
    app.dependency_overrides[current_user] = lambda: UserPublic(id=None, username='local', role='user', auth_required=False)
    app.dependency_overrides[get_knowledge_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/v1/knowledge/prompts')
            assert response.status_code == 200
            entries = response.json()
            assert len(entries) == 29
            assert all('content' not in entry and entry['source'] == 'default' for entry in entries)
            assert all(entry['stages'] for entry in entries)
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_knowledge_service, None)


@pytest.mark.anyio
async def test_knowledge_requires_authentication():
    def denied():
        from app.core.errors import AppError
        raise AppError('unauthorized', '请登录', 401)
    app.dependency_overrides[current_user] = denied
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            for path in ['/api/v1/knowledge/orchestration', '/api/v1/knowledge/prompts', '/api/v1/knowledge/prompt?filename=x']:
                assert (await client.get(path)).status_code == 401
    finally:
        app.dependency_overrides.pop(current_user, None)
