from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.errors import AppError
from app.replay.contracts import ReplayCommand, ReplayOrder, SessionCreate, TrainingConfig
from app.replay.models import ReplayFillRecord, ReplayOrderRecord, ReplaySession
from app.replay.saved import SavedReplays
from test_replay_repository import T, replay


@pytest.mark.anyio
@pytest.mark.parametrize('side,quantity,price', [('buy', 2, '101.25'), ('sell', -2, '100.75')])
async def test_market_fills_at_visible_price_without_advancing_or_reading_future(replay, side, quantity, price):
    created = await replay.create('local', SessionCreate(mode='training', start=T,
        config=TrainingConfig(fee_per_side='2', slippage_ticks=1)))

    async def no_future(*args):
        raise AssertionError('Market orders must not request future or additional market data')

    replay.market.load = replay.market.next_minute = no_future
    command = ReplayCommand(type='place_order', side=side, quantity=2, operation_id='market', expected_revision=0)
    placed = await replay.command('local', created['id'], command)
    assert placed['state']['position']['quantity'] == quantity
    assert placed['state']['position']['average_price'] == price
    assert placed['state']['cursor'] == created['state']['cursor']
    assert placed['state']['last_price'] == created['state']['last_price']
    assert placed['state']['orders'][0]['status'] == 'filled'
    account = placed['state']['account']
    assert Decimal(account['cash']) == Decimal('99996')
    assert Decimal(account['fees']) == Decimal('4')
    assert Decimal(account['unrealized_pnl']) == Decimal('-25')
    assert Decimal(account['equity']) == Decimal('99971')
    events = (await replay.repository.events('local', created['id'], 200, 0))['items']
    assert len(events) == 1
    assert len(events[0]['fills']) == 1
    fill = events[0]['fills'][0]
    assert datetime.fromisoformat(fill['timestamp']) == T
    assert fill['price'] == price and fill['reason'] == 'entry'
    assert events[0]['minute'] is None and events[0]['minutes'] == []

    assert await replay.command('local', created['id'], command) == placed
    with pytest.raises(AppError) as conflict:
        await replay.command('local', created['id'], command.model_copy(update={'operation_id': 'stale'}))
    assert conflict.value.code == 'replay_revision_conflict'
    with pytest.raises(AppError) as reused:
        await replay.command('local', created['id'], command.model_copy(update={'quantity': 1}))
    assert reused.value.code == 'replay_operation_conflict'
    assert (await replay.get('local', created['id']))['state'] == placed['state']
    async with replay.repository.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReplayFillRecord)) == 1
        order = await db.get(ReplayOrderRecord, placed['state']['orders'][0]['id'])
        assert order.payload['status'] == 'filled'


@pytest.mark.anyio
@pytest.mark.parametrize('side,opposite,stop,profit', [('buy', 'sell', '97', '104'), ('sell', 'buy', '104', '97')])
async def test_same_cursor_market_add_reduce_close_preserves_protection_and_accounting(replay, side, opposite, stop, profit):
    current = await replay.create('local', SessionCreate(mode='training', start=T,
        config=TrainingConfig(fee_per_side='2', slippage_ticks=1)))
    initial_cursor = current['state']['cursor']

    async def send(**fields):
        nonlocal current
        current = await replay.command('local', current['id'], ReplayCommand(type='place_order',
            operation_id=f"order-{current['revision']}", expected_revision=current['revision'], **fields))
        assert current['state']['cursor'] == initial_cursor
        return current['state']['position']

    position = await send(side=side, quantity=2, stop_loss=stop, take_profit=profit)
    assert abs(position['quantity']) == 2
    position = await send(side=side, quantity=1)
    assert abs(position['quantity']) == 3
    assert position['stop_loss'] == stop and position['take_profit'] == profit
    position = await send(side=opposite, quantity=1, reduce_only=True)
    assert abs(position['quantity']) == 2
    assert position['stop_loss'] == stop and position['take_profit'] == profit
    assert Decimal(position['entry_fees']) == Decimal('4')
    position = await send(side=opposite, quantity=2, reduce_only=True)
    assert position['quantity'] == 0
    assert position['stop_loss'] is None and position['take_profit'] is None
    assert Decimal(position['average_price']) == 0
    account = current['state']['account']
    assert Decimal(account['realized_pnl']) == Decimal('-75')
    assert Decimal(account['fees']) == Decimal('12')
    assert Decimal(account['cash']) == Decimal('99913')
    assert account['equity'] == account['cash']
    fills = [fill for event in (await replay.repository.events('local', current['id'], 200, 0))['items'] for fill in event['fills']]
    assert len(fills) == 4
    assert Decimal(fills[2]['realized_pnl']) == Decimal('-29')
    assert Decimal(fills[3]['realized_pnl']) == Decimal('-58')
    ended = await replay.command('local', current['id'], ReplayCommand(type='finish', operation_id='finish', expected_revision=4))
    assert ended['status'] == 'completed'


@pytest.mark.anyio
@pytest.mark.parametrize('unavailable', ['no_price', 'historical'])
async def test_market_rejection_preserves_state_without_fill(replay, unavailable):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    changed = deepcopy(created['state'])
    if unavailable == 'no_price':
        changed['last_price'] = None
    async with replay.repository.factory.begin() as db:
        await db.execute(update(ReplaySession).where(ReplaySession.id == created['id']).values(
            state=changed, mode='historical' if unavailable == 'historical' else 'training'))
    before = await replay.get('local', created['id'])
    with pytest.raises(AppError) as error:
        await replay.command('local', created['id'], ReplayCommand(type='place_order', operation_id='market', expected_revision=0))
    assert error.value.code == 'replay_invalid_command'
    after = await replay.get('local', created['id'])
    assert after['state'] == before['state'] and after['revision'] == 0
    assert all(not event['fills'] for event in (await replay.repository.events('local', created['id'], 200, 0))['items'])


@pytest.mark.anyio
async def test_market_fill_persistence_failure_rolls_back_order_position_and_cash(replay, monkeypatch):
    import app.replay.repository as repository_module
    created = await replay.create('local', SessionCreate(mode='training', start=T))

    def fail_fill_record(**kwargs):
        raise AppError('fill_write_failed', 'test fill persistence failure', 500)

    with monkeypatch.context() as patch:
        patch.setattr(repository_module, 'ReplayFillRecord', fail_fill_record)
        with pytest.raises(AppError) as error:
            await replay.command('local', created['id'], ReplayCommand(type='place_order', operation_id='market', expected_revision=0))
        assert error.value.code == 'fill_write_failed'
    after = await replay.get('local', created['id'])
    assert after['state'] == created['state'] and after['revision'] == 0
    async with replay.repository.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ReplayFillRecord)) == 0
        assert await db.scalar(select(func.count()).select_from(ReplayOrderRecord)) == 0
    retried = await replay.command('local', created['id'], ReplayCommand(type='place_order', operation_id='market', expected_revision=0))
    assert retried['state']['position']['quantity'] == 1


@pytest.mark.anyio
async def test_immediate_market_position_and_fill_survive_save_resume(replay):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    placed = await replay.command('local', created['id'], ReplayCommand(type='place_order', operation_id='market',
        expected_revision=0, stop_loss='97', take_profit='104'))
    assert placed['state']['position']['quantity'] == 1
    saved = SavedReplays(replay.repository)
    version = await saved.save('local', created['id'], 'immediate', 'save', 1)
    restored = await saved.resume('local', version['id'], 'resume')
    assert restored['state']['position'] == placed['state']['position']
    assert restored['state']['orders'][0]['status'] == 'filled'
    events = (await replay.repository.events('local', restored['id'], 200, 0))['items']
    assert len([fill for event in events for fill in event['fills']]) == 1
    advanced = await replay.command('local', restored['id'], ReplayCommand(type='advance', operation_id='next', expected_revision=0))
    assert advanced['state']['position']['quantity'] == 1
    assert Decimal(advanced['state']['position']['average_price']) == Decimal('101')


@pytest.mark.anyio
async def test_legacy_pending_market_still_fills_when_replay_advances(replay):
    created = await replay.create('local', SessionCreate(mode='training', start=T))
    state = deepcopy(created['state'])
    state['orders'] = [ReplayOrder(id='legacy-market', side='buy', quantity=2, created_at=T).model_dump(mode='json')]
    async with replay.repository.factory.begin() as db:
        await db.execute(update(ReplaySession).where(ReplaySession.id == created['id']).values(state=state))
    advanced = await replay.command('local', created['id'], ReplayCommand(type='advance', operation_id='next', expected_revision=0))
    assert advanced['state']['position']['quantity'] == 2
    assert Decimal(advanced['state']['position']['average_price']) == Decimal('100')
    assert advanced['state']['orders'][0]['status'] == 'filled'


@pytest.mark.anyio
@pytest.mark.parametrize('close_position', [False, True])
async def test_market_execution_does_not_consume_pending_order_capacity(replay, close_position):
    current = await replay.create('local', SessionCreate(mode='training', start=T))
    if close_position:
        current = await replay.command('local', current['id'], ReplayCommand(type='place_order',
            operation_id='open', expected_revision=0))
    state = deepcopy(current['state'])
    state['orders'].extend(ReplayOrder(id=f'pending-{i}', side='buy', quantity=1, kind='limit',
        limit_price='90', created_at=T).model_dump(mode='json') for i in range(100))
    async with replay.repository.factory.begin() as db:
        await db.execute(update(ReplaySession).where(ReplaySession.id == current['id']).values(state=state))
    result = await replay.command('local', current['id'], ReplayCommand(type='place_order',
        side='sell' if close_position else 'buy', reduce_only=close_position,
        operation_id='market-at-capacity', expected_revision=current['revision']))
    assert result['state']['position']['quantity'] == (0 if close_position else 1)
    assert result['state']['orders'][-1]['status'] == 'filled'
    assert sum(order['status'] == 'pending' for order in result['state']['orders']) == 100
    for kind, fields in [('limit', {'limit_price':'90'}), ('stop_market', {'stop_price':'110'}),
                         ('stop_limit', {'stop_price':'110', 'limit_price':'110'})]:
        with pytest.raises(AppError, match='100'):
            await replay.command('local', current['id'], ReplayCommand(type='place_order', kind=kind,
                operation_id=f'over-capacity-{kind}', expected_revision=result['revision'], **fields))
    after = await replay.get('local', current['id'])
    assert after['state'] == result['state'] and after['revision'] == result['revision']
