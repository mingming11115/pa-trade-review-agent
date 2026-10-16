import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.auth.service import UserPublic, current_user
from app.replay.routes import get_replay_service
from test_replay_repository import replay, T


@pytest.mark.anyio
async def test_http_contract_auth_and_trace(replay, caplog):
    app.dependency_overrides[get_replay_service] = lambda: replay
    app.dependency_overrides[current_user] = lambda: UserPublic(id=None, username='local', role='user', auth_required=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            created = await client.post('/api/v1/replay/sessions', json=dict(mode='training', start=T.isoformat()))
            assert created.status_code == 200
            session = created.json()
            assert isinstance(session['state']['account']['cash'], str)
            sid = session['id']
            with caplog.at_level('INFO'):
                response = await client.post(f'/api/v1/replay/sessions/{sid}/commands', headers={'X-Trace-ID':'replay-test-trace'},
                    json=dict(type='advance',operation_id='one',expected_revision=0))
            assert response.status_code==200
            assert any(getattr(r,'session_id',None)==sid and getattr(r,'trace_id',None)=='replay-test-trace' for r in caplog.records)
            assert (await client.get(f'/api/v1/replay/sessions/{sid}/bars?limit=2001')).status_code==422
            assert (await client.post('/api/v1/replay/sessions',json=dict(mode='training',start=T.isoformat(),owner='other'))).status_code==422
            app.dependency_overrides[current_user] = lambda: UserPublic(id=uuid.uuid4(),username='other',role='user',auth_required=True)
            assert (await client.get(f'/api/v1/replay/sessions/{sid}')).status_code==404
            assert (await client.get(f'/api/v1/replay/sessions/{sid}/events')).status_code==404
    finally:
        app.dependency_overrides.pop(get_replay_service,None)
        app.dependency_overrides.pop(current_user,None)


@pytest.mark.anyio
async def test_saved_replay_http_lifecycle_and_analysis_trace(replay,monkeypatch,caplog):
    from types import SimpleNamespace
    import app.replay.routes as routes
    started=[]
    monkeypatch.setattr(routes,'get_analysis_run_manager',lambda:SimpleNamespace(start=lambda run_id,trace:started.append((run_id,trace))))
    app.dependency_overrides[get_replay_service]=lambda:replay
    app.dependency_overrides[current_user]=lambda:UserPublic(id=None,username='local',role='user',auth_required=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            s=(await client.post('/api/v1/replay/sessions',json=dict(mode='training',start=T.isoformat()))).json()
            version=await client.post(f"/api/v1/replay/sessions/{s['id']}/save",json=dict(name='HTTP',operation_id='save',expected_revision=0))
            assert version.status_code==200
            vid=version.json()['id']
            with caplog.at_level('INFO'):
                result=await client.post(f'/api/v1/replay/saved/{vid}/analysis',json=dict(operation_id='analyze',periods=['1m']),headers={'X-Trace-ID':'replay-analysis-http'})
            assert result.status_code==202
            assert started[0][1]=='replay-analysis-http'
            assert any(getattr(r,'version_id',None)==vid and getattr(r,'trace_id',None)=='replay-analysis-http' for r in caplog.records)
            history=(await client.get(f'/api/v1/replay/saved/{vid}/analysis')).json()
            assert history['items'][0]==result.json()
            assert (await client.post(f"/api/v1/replay/sessions/{s['id']}/discard",json={'expected_revision':0})).status_code==200
            assert (await client.get('/api/v1/replay/drafts')).json()['items']==[]
            assert len((await client.get('/api/v1/replay/saved')).json()['items'])==1
    finally:
        app.dependency_overrides.pop(get_replay_service,None)
        app.dependency_overrides.pop(current_user,None)
