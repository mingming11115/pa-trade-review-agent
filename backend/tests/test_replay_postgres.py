"""Real PostgreSQL transaction checks in disposable isolated schemas."""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import Base
from app.core.errors import AppError
from app.replay.contracts import ReplayCommand, SessionCreate
from app.replay.models import ReplaySession, ReplayEvent, ReplayFillRecord, ReplayOrderRecord, ReplayTradeSnapshot, ReplaySavedVersion, ReplayDraftState
from app.replay.repository import ReplayRepository
from app.replay.service import ReplayService
from test_market_archive import archive_db, record
from test_replay_repository import Market, T


async def service_for(archive_db):
    archive, engine, _ = archive_db
    translated = engine.execution_options(schema_translate_map={None:archive.ARCHIVE_SCHEMA})
    tables = [ReplaySession.__table__,ReplayEvent.__table__,ReplayFillRecord.__table__,ReplayOrderRecord.__table__,ReplayTradeSnapshot.__table__,ReplaySavedVersion.__table__,ReplayDraftState.__table__]
    async with translated.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c,tables=tables))
    return ReplayService(ReplayRepository(async_sessionmaker(translated,expire_on_commit=False)),Market())


@pytest.mark.asyncio
@pytest.mark.parametrize('same_operation',[True,False])
@pytest.mark.parametrize('command_type',['advance','place_order'])
async def test_two_connections_serialize_commands_without_double_execution(archive_db,same_operation,command_type):
    service=await service_for(archive_db)
    created=await service.create('local',SessionCreate(mode='training',start=T))
    barrier=asyncio.Barrier(2)
    original=service.get
    async def synchronized(*args):
        current=await original(*args)
        await asyncio.wait_for(barrier.wait(),5)
        return current
    service.get=synchronized
    try:
        results=await asyncio.gather(*(service.command('local',created['id'],ReplayCommand(type=command_type,operation_id='same' if same_operation else f'op-{i}',expected_revision=0)) for i in range(2)),return_exceptions=True)
    finally:
        service.get=original
    if same_operation:
        assert results[0]==results[1] and isinstance(results[0],dict)
    else:
        assert sum(isinstance(r,AppError) and r.status_code==409 for r in results)==1
    saved=await service.get('local',created['id'])
    assert saved['revision']==1
    assert saved['state']['cursor']==('2025-01-01T00:01:00Z' if command_type=='advance' else created['state']['cursor'])
    if command_type=='place_order':
        assert saved['state']['position']['quantity']==1
        events=await service.repository.events('local',created['id'],200,0)
        assert sum(len(event['fills']) for event in events['items'])==1


@pytest.mark.asyncio
@pytest.mark.parametrize('command_type',['advance','place_order'])
async def test_fill_write_failure_rolls_back_cash_orders_and_cursor(archive_db,command_type):
    service=await service_for(archive_db)
    v=await service.create('local',SessionCreate(mode='training',start=T))
    if command_type=='advance':
        v=await service.command('local',v['id'],ReplayCommand(type='place_order',kind='limit',limit_price='99',operation_id='order',expected_revision=0))
    engine=archive_db[1]
    def fail_fill(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().startswith('INSERT INTO') and '.replay_fills ' in statement:
            raise RuntimeError('injected fill failure')
    event.listen(engine.sync_engine,'before_cursor_execute',fail_fill)
    try:
        with pytest.raises(RuntimeError,match='injected fill failure'):
            await service.command('local',v['id'],ReplayCommand(type=command_type,operation_id='failing',expected_revision=v['revision']))
    finally:
        event.remove(engine.sync_engine,'before_cursor_execute',fail_fill)
    saved=await service.get('local',v['id'])
    assert saved['revision']==v['revision'] and saved['state']==v['state']


@pytest.mark.asyncio
async def test_archive_provisional_row_is_not_skipped(archive_db,monkeypatch):
    import app.replay.market as module
    archive,_,factory=archive_db
    monkeypatch.setattr(module,'SessionFactory',factory)
    r=record()
    async with factory.begin() as db:
        await archive.write_archive_records(db,[replace(r,confirmed=False),replace(r,timestamp_utc=r.timestamp_utc+timedelta(minutes=1),ts_event_ns=r.ts_event_ns+60000000000)])
        await archive.write_archive_coverage(db,'MES',r.timestamp_utc,r.timestamp_utc+timedelta(minutes=50))
    with pytest.raises(AppError,match='未确认'):
        await module.ReplayMarket().next_minute('MES',r.timestamp_utc)


@pytest.mark.asyncio
async def test_save_and_advance_race_keeps_one_complete_revision(archive_db):
    from app.replay.saved import SavedReplays
    service=await service_for(archive_db)
    s=await service.create('local',SessionCreate(mode='training',start=T))
    saved=SavedReplays(service.repository)
    results=await asyncio.gather(saved.save('local',s['id'],'race','save',0),
        service.command('local',s['id'],ReplayCommand(type='advance',operation_id='a',expected_revision=0)),return_exceptions=True)
    version=results[0]
    if isinstance(version,AppError):
        assert version.code=='replay_revision_conflict'
    else:
        assert version['source_revision']==0
        assert version['events']==[]
        assert version['session']['state']['cursor']==s['state']['cursor']
    assert (await service.get('local',s['id']))['revision']==1
