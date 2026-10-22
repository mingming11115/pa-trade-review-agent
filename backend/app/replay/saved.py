from __future__ import annotations

from copy import deepcopy
from typing import Any
import uuid

from sqlalchemy import select
from app.core.errors import AppError
from app.replay.models import ReplayDraftState, ReplayEvent, ReplaySavedVersion, ReplaySession
from app.replay.repository import ReplayRepository, normalize_view, session_view


def version_view(row: ReplaySavedVersion) -> dict[str, Any]:
    snapshot = deepcopy(row.snapshot)
    snapshot['session'] = normalize_view(snapshot['session'])
    return dict(id=row.id, name=row.name, created_at=row.created_at.isoformat(),
                source_revision=row.source_revision, **snapshot)


class SavedReplays:
    def __init__(self, repository: ReplayRepository):
        self.repository = repository

    async def get(self, scope: str, version_id: str) -> dict[str, Any]:
        async with self.repository.factory() as db:
            row = await db.scalar(select(ReplaySavedVersion).where(ReplaySavedVersion.id==version_id, ReplaySavedVersion.scope==scope))
            if row is None:
                raise AppError('replay_not_found', '保存记录不存在', 404)
            return version_view(row)

    async def list(self, scope: str, limit: int, offset: int) -> list[dict[str, Any]]:
        async with self.repository.factory() as db:
            rows = await db.scalars(select(ReplaySavedVersion).where(ReplaySavedVersion.scope==scope)
                                    .order_by(ReplaySavedVersion.created_at.desc(), ReplaySavedVersion.id).limit(limit).offset(offset))
            return [dict(id=r.id, name=r.name, created_at=r.created_at.isoformat(), source_revision=r.source_revision,
                         session=normalize_view(r.snapshot['session'])) for r in rows]

    async def legacy(self, scope: str, limit: int, offset: int) -> list[dict[str, Any]]:
        async with self.repository.factory() as db:
            rows = await db.scalars(select(ReplaySession).where(
                ReplaySession.scope==scope, ReplaySession.mode=='training', ReplaySession.status!='active',
                ~select(ReplayDraftState.session_id).where(ReplayDraftState.session_id==ReplaySession.id).exists(),
                ~select(ReplaySavedVersion.id).where(ReplaySavedVersion.session_id==ReplaySession.id).exists())
                .order_by(ReplaySession.created_at.desc(),ReplaySession.id).limit(limit).offset(offset))
            return [session_view(row) for row in rows]

    async def drafts(self, scope: str) -> list[dict[str, Any]]:
        async with self.repository.factory() as db:
            rows = await db.scalars(select(ReplaySession).outerjoin(ReplayDraftState, ReplayDraftState.session_id==ReplaySession.id)
                                    .where(ReplaySession.scope==scope, ReplaySession.mode=='training', ReplaySession.status=='active',
                                           (ReplayDraftState.discarded.is_(None)) | (ReplayDraftState.discarded==0))
                                    .order_by(ReplaySession.created_at.desc()).limit(100))
            return [session_view(r) for r in rows]

    async def save(self, scope: str, session_id: str, name: str, operation_id: str, expected_revision: int) -> dict[str, Any]:
        async with self.repository.factory.begin() as db:
            row = await db.scalar(select(ReplaySession).where(ReplaySession.id==session_id, ReplaySession.scope==scope).with_for_update())
            if row is None:
                raise AppError('replay_not_found', '回放不存在', 404)
            prior = await db.scalar(select(ReplaySavedVersion).where(ReplaySavedVersion.session_id==session_id, ReplaySavedVersion.operation_id==operation_id))
            if prior:
                if prior.source_revision != expected_revision or prior.name != name:
                    raise AppError('replay_operation_conflict', '保存操作标识重复', 409)
                return version_view(prior)
            if row.revision != expected_revision:
                raise AppError('replay_revision_conflict', '回放已更新，请刷新后保存', 409)
            draft = await db.get(ReplayDraftState, session_id)
            if draft and draft.discarded:
                raise AppError('replay_discarded', '草稿已退出，请从保存记录继续', 409)
            events = list(await db.scalars(select(ReplayEvent).where(ReplayEvent.session_id==session_id)
                                          .order_by(ReplayEvent.revision, ReplayEvent.id)))
            v = ReplaySavedVersion(scope=scope, session_id=session_id, operation_id=operation_id,
                                   source_revision=row.revision, name=name,
                                   snapshot=dict(session=session_view(row), events=[dict(revision=e.revision, type=e.payload['type'],
                                        cursor=e.result['state']['cursor'], equity=e.result['state']['account']['equity'], **e.evidence) for e in events]))
            db.add(v)
            await db.flush()
            return version_view(v)

    async def discard(self, scope: str, session_id: str, expected_revision: int) -> None:
        async with self.repository.factory.begin() as db:
            row = await db.scalar(select(ReplaySession).where(ReplaySession.id==session_id, ReplaySession.scope==scope).with_for_update())
            if row is None:
                raise AppError('replay_not_found', '回放不存在', 404)
            if row.revision != expected_revision:
                raise AppError('replay_revision_conflict', '回放已更新，请刷新后退出', 409)
            draft = await db.get(ReplayDraftState, session_id)
            if draft is None:
                draft = ReplayDraftState(session_id=session_id, scope=scope)
                db.add(draft)
            draft.discarded = 1
            row.status = 'abandoned'

    async def resume(self, scope: str, version_id: str, operation_id: str) -> dict[str, Any]:
        async with self.repository.factory.begin() as db:
            version = await db.scalar(select(ReplaySavedVersion).where(ReplaySavedVersion.id==version_id, ReplaySavedVersion.scope==scope).with_for_update())
            if version is None:
                raise AppError('replay_not_found', '保存记录不存在', 404)
            prior = await db.scalar(select(ReplayDraftState).where(ReplayDraftState.scope==scope, ReplayDraftState.resume_operation_id==operation_id))
            if prior:
                if prior.origin_version_id != version_id:
                    raise AppError('replay_operation_conflict', '恢复操作标识重复', 409)
                return session_view(await db.get(ReplaySession, prior.session_id))
            original = version.snapshot['session']
            state = deepcopy(original['state'])
            for order in state['orders']:
                order['id'] = str(uuid.uuid4())
            row = ReplaySession(scope=scope, mode=original['mode'], symbol=original['symbol'], status='active',
                                revision=0, state=state, config=deepcopy(original['config']))
            db.add(row)
            await db.flush()
            db.add(ReplayDraftState(session_id=row.id, scope=scope, origin_version_id=version_id, resume_operation_id=operation_id))
            # Preserve past fills/statistics for the resumed chart without replaying them.
            fills = [fill for event in version.snapshot['events'] for fill in event.get('fills', [])]
            db.add(ReplayEvent(session_id=row.id, operation_id='restored', revision=0, payload={'type':'restored'},
                               result=session_view(row), evidence={'fills':fills}))
            return session_view(row)
