from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Query
from app.auth.service import UserPublic, current_user
from app.core.models import Period, TradingHours
from app.replay.contracts import ReplayCommand, SessionCreate
from app.replay.service import ReplayService

router = APIRouter(prefix='/api/v1/replay', tags=['replay'])


def get_replay_service() -> ReplayService:
    return ReplayService()


def scope(user: UserPublic) -> str:
    return str(user.id) if user.id else 'local'


@router.post('/sessions')
async def create(payload: SessionCreate, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return await service.create(scope(user), payload)


@router.get('/sessions')
async def sessions(mode: Literal['training', 'historical'], limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                   user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return {'items': await service.repository.list(scope(user), mode, limit, offset)}


@router.get('/sessions/{session_id}')
async def get(session_id: str, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return await service.get(scope(user), session_id)


@router.post('/sessions/{session_id}/commands')
async def command(session_id: str, payload: ReplayCommand, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return await service.command(scope(user), session_id, payload)


@router.get('/sessions/{session_id}/bars')
async def bars(session_id: str, period: Period = Period.minute_1, cursor: datetime | None = None, limit: int = Query(500, ge=1, le=2000),
               trading_hours: TradingHours | None = None,
               user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    if cursor and cursor.tzinfo is None:
        from app.replay.service import reject
        reject('时间必须包含时区')
    return await service.bars(scope(user), session_id, period.value, cursor, limit, trading_hours=trading_hours)


@router.get('/sessions/{session_id}/events')
async def events(session_id: str, limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0),
                 user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    view = await service.get(scope(user), session_id)
    if view['mode']=='historical':
        history = await service.history(scope(user), session_id)
        all_events = history.pop('events')
        return dict(items=all_events[offset:offset+limit], next_offset=offset+limit if len(all_events)>offset+limit else None, history=history)
    return await service.repository.events(scope(user), session_id, limit, offset)


from app.replay.contracts import ReplaySaveRequest, ReplayDiscardRequest, ReplayResumeRequest
from app.replay.saved import SavedReplays


@router.post('/sessions/{session_id}/save')
async def save_replay(session_id: str, payload: ReplaySaveRequest, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return await SavedReplays(service.repository).save(scope(user), session_id, payload.name, payload.operation_id, payload.expected_revision)


@router.post('/sessions/{session_id}/discard')
async def discard_replay(session_id: str, payload: ReplayDiscardRequest, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    await SavedReplays(service.repository).discard(scope(user), session_id, payload.expected_revision)
    return {'ok': True}


@router.get('/saved')
async def saved_replays(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return {'items': await SavedReplays(service.repository).list(scope(user), limit, offset)}


@router.get('/drafts')
async def replay_drafts(user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return {'items': await SavedReplays(service.repository).drafts(scope(user))}


@router.get('/saved/{version_id}')
async def saved_replay(version_id: str, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return await SavedReplays(service.repository).get(scope(user), version_id)


@router.post('/saved/{version_id}/resume')
async def resume_replay(version_id: str, payload: ReplayResumeRequest, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return await SavedReplays(service.repository).resume(scope(user), version_id, payload.operation_id)


from app.replay.analysis import ReplayAnalysis
from app.replay.contracts import ReplayAnalysisRequest
from app.analysis.routes import get_analysis_run_manager
from app.auth.service import limit_expensive
from app.core.logging_context import get_trace_id
import uuid


@router.post('/saved/{version_id}/analysis', status_code=202)
async def analyze_saved_replay(version_id: str, payload: ReplayAnalysisRequest, user: UserPublic = Depends(limit_expensive),
                               service: ReplayService = Depends(get_replay_service)) -> dict:
    result = await ReplayAnalysis(service).prepare(scope(user), user.id, version_id, payload.operation_id, payload.periods)
    manager = get_analysis_run_manager()
    for run in result['runs']:
        manager.start(uuid.UUID(run['run_id']), get_trace_id())
    return result


@router.get('/saved/{version_id}/analysis')
async def saved_replay_analyses(version_id: str, user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return {'items': await ReplayAnalysis(service).list(scope(user), version_id)}


@router.get('/legacy')
async def legacy_replays(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: UserPublic = Depends(current_user), service: ReplayService = Depends(get_replay_service)) -> dict:
    return {'items': await SavedReplays(service.repository).legacy(scope(user), limit, offset)}
