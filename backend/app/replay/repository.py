from __future__ import annotations

from collections.abc import Callable
from typing import Any
import uuid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.database import SessionFactory
from app.core.errors import AppError
from app.replay.models import ReplaySession, ReplayEvent, ReplayFillRecord, ReplayOrderRecord, ReplayTradeSnapshot
from app.replay.contracts import ReplayCommand


def session_view(row: ReplaySession) -> dict[str, Any]:
    return dict(id=row.id, mode=row.mode, symbol=row.symbol, status=row.status, revision=row.revision,
                state=row.state, config=row.config, created_at=row.created_at.isoformat())


class ReplayRepository:
    def __init__(self, factory: async_sessionmaker = SessionFactory):
        self.factory = factory

    async def create(self, row: ReplaySession, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        async with self.factory.begin() as db:
            db.add(row)
            await db.flush()
            for snapshot in snapshots:
                db.add(ReplayTradeSnapshot(session_id=row.id, payload=snapshot))
            result = session_view(row)
        return result

    async def get(self, scope: str, session_id: str) -> dict[str, Any]:
        async with self.factory() as db:
            row = await db.scalar(select(ReplaySession).where(ReplaySession.id == session_id, ReplaySession.scope == scope))
            if row is None:
                raise AppError('replay_not_found', '回放场次不存在', 404)
            return session_view(row)

    async def list(self, scope: str, mode: str, limit: int, offset: int) -> list[dict[str, Any]]:
        async with self.factory() as db:
            rows = await db.scalars(select(ReplaySession).where(ReplaySession.scope == scope, ReplaySession.mode == mode)
                                    .order_by(ReplaySession.created_at.desc(), ReplaySession.id).offset(offset).limit(limit))
            return [session_view(row) for row in rows]

    async def snapshots(self, scope: str, session_id: str) -> list[dict[str, Any]]:
        await self.get(scope, session_id)
        async with self.factory() as db:
            return list(await db.scalars(select(ReplayTradeSnapshot.payload).where(ReplayTradeSnapshot.session_id == session_id)))

    async def previous(self, scope: str, session_id: str, command: ReplayCommand) -> dict[str, Any] | None:
        await self.get(scope, session_id)
        async with self.factory() as db:
            event = await db.scalar(select(ReplayEvent).where(ReplayEvent.session_id==session_id, ReplayEvent.operation_id==command.operation_id))
            if event:
                if {'period': '1m', **event.payload} != command.model_dump(mode='json'):
                    raise AppError('replay_operation_conflict', '同一操作标识不能用于不同指令', 409)
                return event.result
        return None

    async def transact(self, scope: str, session_id: str, command: ReplayCommand,
                       change: Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]) -> dict[str, Any]:
        async with self.factory.begin() as db:
            row = await db.scalar(select(ReplaySession).where(ReplaySession.id==session_id, ReplaySession.scope==scope).with_for_update())
            if row is None:
                raise AppError('replay_not_found', '回放场次不存在', 404)
            prior = await db.scalar(select(ReplayEvent).where(ReplayEvent.session_id==session_id, ReplayEvent.operation_id==command.operation_id))
            if prior:
                if {'period': '1m', **prior.payload} != command.model_dump(mode='json'):
                    raise AppError('replay_operation_conflict', '同一操作标识不能用于不同指令', 409)
                return prior.result
            if row.revision != command.expected_revision:
                raise AppError('replay_revision_conflict', '场次已更新，请刷新后操作', 409)
            changed, fills, evidence = change(session_view(row))
            revision = row.revision + 1
            # CAS also protects adapters that do not implement SELECT FOR UPDATE.
            count = await db.execute(update(ReplaySession).where(ReplaySession.id==session_id, ReplaySession.revision==row.revision)
                                     .values(state=changed['state'], status=changed['status'], revision=revision).execution_options(synchronize_session=False))
            if count.rowcount != 1:
                raise AppError('replay_revision_conflict', '场次已更新，请刷新后操作', 409)
            changed['revision'] = revision
            db.add(ReplayEvent(session_id=session_id, operation_id=command.operation_id, revision=revision,
                               payload=command.model_dump(mode='json'), result=changed, evidence=evidence))
            for order in changed['state']['orders']:
                record = await db.get(ReplayOrderRecord, order['id'])
                if record:
                    record.payload = order
                else:
                    db.add(ReplayOrderRecord(id=order['id'], session_id=session_id, payload=order))
            for fill in fills:
                db.add(ReplayFillRecord(session_id=session_id, revision=revision, payload=fill))
            return changed

    async def events(self, scope: str, session_id: str, limit: int, offset: int) -> dict[str, Any]:
        await self.get(scope, session_id)
        async with self.factory() as db:
            rows = list(await db.scalars(select(ReplayEvent).where(ReplayEvent.session_id==session_id)
                      .order_by(ReplayEvent.revision, ReplayEvent.id).offset(offset).limit(limit+1)))
            items = [dict(revision=r.revision, type=r.payload['type'], cursor=r.result['state']['cursor'],
                          equity=r.result['state']['account']['equity'], **r.evidence) for r in rows[:limit]]
            return dict(items=items, next_offset=offset+limit if len(rows)>limit else None)

    async def record_failure(self, scope: str, session_id: str, operation_id: str, error_code: str, trace_id: str) -> None:
        async with self.factory.begin() as db:
            row = await db.scalar(select(ReplaySession).where(ReplaySession.id==session_id, ReplaySession.scope==scope))
            if row is None:
                return
            db.add(ReplayEvent(session_id=session_id, operation_id=f'error-{uuid.uuid4()}', revision=row.revision,
                               payload={'type':'error'}, result=session_view(row),
                               evidence=dict(fills=[],error_code=error_code,trace_id=trace_id,failed_operation_id=operation_id)))
