from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.database import Base
from app.core.errors import AppError
from app.replay.contracts import MinuteSnapshot, SessionCreate, ReplayCommand
from app.replay.repository import ReplayRepository
from app.replay.service import ReplayService
from app.trades.service import Trade

T = datetime(2025, 1, 1, tzinfo=UTC)


class Market:
    async def next_minute(self, symbol, cursor):
        return MinuteSnapshot(timestamp=cursor, open=100, high=102, low=99, close=101, instrument_id=1)

    async def load(self, symbol, start, end):
        return [MinuteSnapshot(timestamp=end-timedelta(minutes=1), open=100, high=102, low=99, close=101, instrument_id=1)]


@pytest.fixture
async def replay(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "replay.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield ReplayService(ReplayRepository(factory), Market())
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_persist_idempotent_trade_and_scope(replay):
    view = await replay.create('local', SessionCreate(mode='training', start=T))
    sid = view['id']
    command = ReplayCommand(type='place_order', operation_id='order', expected_revision=0, quantity=2)
    placed = await replay.command('local', sid, command)
    assert placed['revision'] == 1
    assert await replay.command('local', sid, command) == placed
    advanced = await replay.command('local', sid, ReplayCommand(type='advance', operation_id='step', expected_revision=1))
    assert advanced['state']['position']['quantity'] == 2
    assert (await replay.get('local', sid))['revision'] == 2
    with pytest.raises(AppError) as error:
        await replay.get('other', sid)
    assert error.value.status_code == 404
    with pytest.raises(AppError) as error:
        await replay.command('local', sid, ReplayCommand(type='advance', operation_id='stale', expected_revision=0))
    assert error.value.status_code == 409
    with pytest.raises(AppError):
        await replay.command('local', sid, command.model_copy(update={'quantity':3}))


@pytest.mark.anyio
async def test_finish_requires_flat_and_failure_preserves_revision(replay):
    v = await replay.create('local', SessionCreate(mode='training', start=T))
    sid = v['id']
    with pytest.raises(AppError):
        await replay.command('local', sid, ReplayCommand(type='place_order', operation_id='bad', expected_revision=0, kind='limit', limit_price='100.1'))
    assert (await replay.get('local', sid))['revision'] == 0
    v = await replay.command('local', sid, ReplayCommand(type='finish', operation_id='end', expected_revision=0))
    assert v['status'] == 'completed'
    with pytest.raises(AppError):
        await replay.command('local', sid, ReplayCommand(type='advance', operation_id='after', expected_revision=1))


@pytest.mark.anyio
async def test_amend_cancel_protection_and_terminal_state(replay):
    view=await replay.create('local',SessionCreate(mode='training',start=T))
    sid=view['id']
    async def send(command_type, **fields):
        nonlocal view
        view=await replay.command('local',sid,ReplayCommand(type=command_type,operation_id=f'op-{view["revision"]}-{command_type}',expected_revision=view['revision'],**fields))
        return view
    await send('place_order',kind='limit',limit_price='98')
    order_id=view['state']['orders'][0]['id']
    await send('amend_order',order_id=order_id,kind='limit',limit_price='99',quantity=2)
    await send('advance')
    assert view['state']['position']['quantity']==2
    await send('update_protection',stop_loss='97',take_profit='104')
    with pytest.raises(AppError):
        await send('finish')
    await send('place_order',kind='limit',limit_price='95')
    await send('cancel_order',order_id=view['state']['orders'][-1]['id'])
    await send('place_order',side='sell',quantity=1,reduce_only=True)
    await send('advance')
    assert view['state']['position']['quantity']==1
    await send('abandon')
    assert view['status']=='abandoned'
    assert view['state']['position']['quantity']==1


@pytest.mark.anyio
async def test_failure_is_persisted_without_advancing_trade_state(replay):
    v=await replay.create('local',SessionCreate(mode='training',start=T))
    async def broken(*args):
        raise AppError('provider_timeout','test timeout',504)
    replay.market.next_minute=broken
    with pytest.raises(AppError):
        await replay.command('local',v['id'],ReplayCommand(type='advance',operation_id='failed',expected_revision=0))
    unchanged=await replay.get('local',v['id'])
    assert unchanged['state']==v['state'] and unchanged['revision']==0
    events=await replay.repository.events('local',v['id'],200,0)
    assert events['items'][0]['error_code']=='provider_timeout'


@pytest.mark.anyio
async def test_same_operation_committing_between_lookup_and_revision_check(replay):
    v=await replay.create('local',SessionCreate(mode='training',start=T))
    cmd=ReplayCommand(type='advance',operation_id='same',expected_revision=0)
    previous=replay.repository.previous
    winner=None
    async def missed_then_committed(scope,sid,payload):
        nonlocal winner
        replay.repository.previous=previous
        winner=await replay.command(scope,sid,payload)
        return None
    replay.repository.previous=missed_then_committed
    result=await replay.command('local',v['id'],cmd)
    assert result==winner


@pytest.mark.anyio
async def test_historical_subminute_seek_normalizes_before_loading(replay):
    from app.replay.models import ReplaySession
    from sqlalchemy import update
    v=await replay.create('local',SessionCreate(mode='training',start=T))
    async with replay.repository.factory.begin() as db:
        await db.execute(update(ReplaySession).where(ReplaySession.id==v['id']).values(mode='historical'))
    ends=[]
    async def sample(symbol,start,end):
        ends.append(end)
        return []
    replay.market.load=sample
    result=await replay.command('local',v['id'],ReplayCommand(type='seek',cursor=T+timedelta(seconds=30),operation_id='seek',expected_revision=0))
    assert ends==[T]
    assert datetime.fromisoformat(result['state']['cursor'])==T


@pytest.mark.anyio
async def test_historical_snapshot_normalizes_database_datetimes(replay):
    from app.trades.service import Trade
    import uuid
    trade_id=uuid.uuid4()
    async with replay.repository.factory.begin() as db:
        db.add(Trade(id=trade_id,source_trade_id='snapshot',contract_name='ESH5',symbol_root='ES',
                     entered_at=T,exited_at=T+timedelta(minutes=2),entry_price=100,exit_price=102,direction='long',size=2,
                     reported_pnl=200,source_file_name='test',source_file_hash='test',raw_data={}))
    v=await replay.create('local',SessionCreate(mode='historical',symbol='ES',trade_ids=[str(trade_id)]))
    snapshots=await replay.repository.snapshots('local',v['id'])
    assert datetime.fromisoformat(snapshots[0]['entered_at']).utcoffset()==timedelta(0)
    assert datetime.fromisoformat(v['config']['start'])==T-timedelta(minutes=60)
    v=await replay.command('local',v['id'],ReplayCommand(type='seek',target='next',operation_id='entry',expected_revision=0))
    history=await replay.history('local',v['id'])
    assert history['quantity']=='2.00000000' and len(history['events'])==1
    assert 'pnl' not in history['events'][0]
    v=await replay.command('local',v['id'],ReplayCommand(type='seek',target='next',operation_id='exit',expected_revision=1))
    assert (await replay.history('local',v['id']))['realized_pnl']=='200.00000000'
    async with replay.repository.factory.begin() as db:
        trade=await db.get(Trade,trade_id)
        trade.reported_pnl=999
    assert (await replay.history('local',v['id']))['realized_pnl']=='200.00000000'


@pytest.mark.anyio
@pytest.mark.parametrize('side,other,limit',[('buy','sell','103'),('sell','buy','99')])
async def test_triggered_order_cannot_change_side(replay,side,other,limit):
    from app.replay.contracts import EngineState, InstrumentSpec, ReplayOrder
    from app.replay.service import ReplayService
    state=EngineState(cursor=T,last_price='100',orders=[ReplayOrder(id='triggered',side=side,kind='stop_limit',stop_price='101',limit_price='100',quantity=1,created_at=T,triggered_at=T)])
    with pytest.raises(AppError,match='触发'):
        ReplayService._edit_order(replay,state,ReplayCommand(type='amend_order',operation_id='amend',expected_revision=0,order_id='triggered',side=other,kind='stop_limit',stop_price='101',limit_price=limit),InstrumentSpec(symbol='ES',tick_size='0.25',multiplier='50'))
