import pytest
from app.core.errors import AppError
from app.replay.contracts import SessionCreate, ReplayCommand
from test_replay_repository import replay, T


@pytest.mark.anyio
async def test_save_resume_discard_preserves_snapshot(replay):
    from app.replay.saved import SavedReplays
    saved=SavedReplays(replay.repository)
    s=await replay.create('local',SessionCreate(mode='training',start=T))
    s=await replay.command('local',s['id'],ReplayCommand(type='place_order',kind='limit',limit_price='99',operation_id='o',expected_revision=0))
    v=await saved.save('local',s['id'],'练习', 'save',1)
    assert v['session']['state']['orders'][0]['status']=='pending'
    assert (await saved.save('local',s['id'],'练习','save',1))['id']==v['id']
    restored=await saved.resume('local',v['id'],'resume')
    assert restored['state']['orders'][0]['id']!=s['state']['orders'][0]['id']
    restored=await replay.command('local',restored['id'],ReplayCommand(type='advance',operation_id='next',expected_revision=0))
    assert restored['state']['position']['quantity']==1
    await saved.discard('local',restored['id'],1)
    assert (await saved.get('local',v['id']))['session']['state']['position']['quantity']==0
    assert len(await saved.list('local',20,0))==1
    with pytest.raises(AppError):
        await saved.get('other',v['id'])
    with pytest.raises(AppError):
        await saved.save('local',s['id'],'bad','stale',0)


@pytest.mark.anyio
async def test_save_with_open_position_and_draft_visibility(replay):
    from app.replay.saved import SavedReplays
    saved=SavedReplays(replay.repository)
    s=await replay.create('local',SessionCreate(mode='training',start=T))
    s=await replay.command('local',s['id'],ReplayCommand(type='place_order',operation_id='o',expected_revision=0))
    s=await replay.command('local',s['id'],ReplayCommand(type='advance',operation_id='a',expected_revision=1))
    v=await saved.save('local',s['id'],'open','save',2)
    assert v['session']['state']['position']['quantity']==1
    assert len(v['events'])==2
    await saved.discard('local',s['id'],2)
    assert not await saved.drafts('local')
    assert (await saved.get('local',v['id']))['session']['state']['position']['quantity']==1


@pytest.mark.anyio
async def test_completed_legacy_sessions_can_be_found_and_converted(replay):
    from app.replay.saved import SavedReplays
    saved=SavedReplays(replay.repository)
    s=await replay.create('local',SessionCreate(mode='training',start=T))
    s=await replay.command('local',s['id'],ReplayCommand(type='finish',operation_id='end',expected_revision=0))
    legacy=await saved.legacy('local',20,0)
    assert [v['id'] for v in legacy]==[s['id']]
    v=await saved.save('local',s['id'],'旧记录','legacy',1)
    assert v['session']['status']=='completed'
    assert await saved.legacy('local',20,0)==[]
