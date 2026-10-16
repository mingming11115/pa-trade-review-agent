import pytest
import uuid
from app.replay.analysis import ReplayAnalysis
from app.replay.contracts import ReplayCommand, SessionCreate
from app.replay.saved import SavedReplays
from app.core.models import HistoricalQuery
from test_replay_repository import replay, T


@pytest.mark.anyio
async def test_analysis_freezes_open_position_without_fake_exit_and_is_idempotent(replay):
    from app.replay.analysis import ReplayAnalysis
    s=await replay.create('local',SessionCreate(mode='training',start=T))
    s=await replay.command('local',s['id'],ReplayCommand(type='place_order',operation_id='o',expected_revision=0))
    s=await replay.command('local',s['id'],ReplayCommand(type='advance',operation_id='a',expected_revision=1))
    v=await SavedReplays(replay.repository).save('local',s['id'],'test','save',2)
    analysis=ReplayAnalysis(replay)
    result=await analysis.prepare('local',None,v['id'],'analyze',['1m'])
    repeated=await analysis.prepare('local',None,v['id'],'analyze',['1m'])
    assert result==repeated
    from app.analysis.tasks.models import AnalysisRun
    async with replay.repository.factory() as db:
        run=await db.get(AnalysisRun,uuid.UUID(result['runs'][0]['run_id']))
        query=HistoricalQuery.model_validate(run.query_json['query'])
        assert query.replay_context['position']['quantity']==1
        assert len(query.replay_context['fills'])==1
        assert query.trades==[]
        assert query.end.isoformat().startswith('2025-01-01T00:01:00')
        assert all(bar['timestamp']<s['state']['cursor'] for bar in run.bars_json)
    await replay.command('local',s['id'],ReplayCommand(type='advance',operation_id='b',expected_revision=2))
    assert await analysis.prepare('local',None,v['id'],'another-click',['1m'])==result


@pytest.mark.anyio
async def test_no_trade_analysis_retains_empty_context_and_scope(replay):
    from app.replay.analysis import ReplayAnalysis
    from app.core.errors import AppError
    s=await replay.create('local',SessionCreate(mode='training',start=T))
    v=await SavedReplays(replay.repository).save('local',s['id'],'empty','save',0)
    analysis=ReplayAnalysis(replay)
    assert (await analysis.prepare('local',None,v['id'],'a',['5m']))['runs']
    with pytest.raises(AppError):
        await analysis.prepare('other',None,v['id'],'b',['5m'])


@pytest.mark.anyio
@pytest.mark.parametrize('fails',[False,True])
async def test_saved_analysis_uses_durable_input_and_persists_terminal(replay,monkeypatch,fails):
    from app.analysis.execution import manager as module
    from app.analysis.tasks.repository import AnalysisTaskRepository
    from app.analysis.tasks.models import AnalysisRun
    s=await replay.create('local',SessionCreate(mode='training',start=T))
    v=await SavedReplays(replay.repository).save('local',s['id'],'empty','save',0)
    result=await ReplayAnalysis(replay).prepare('local',None,v['id'],'a',['1m'])
    async def prompts(*args):
        return None
    async def workflow(bars,query,on_delta,run_id,**kwargs):
        assert query.replay_context['saved_version_id']==v['id']
        assert query.replay_context['position']['quantity']==0
        if fails:
            raise RuntimeError('test failure')
        return dict(query=query.model_dump(mode='json'),resolved_symbol='ES',bars=[b.model_dump(mode='json') for b in bars],
            run_id=str(run_id),status='completed',snapshot={},stage1={},stage2={},audit={},
            analysis=dict(bar_count=len(bars),start=query.start,end=query.end,first_open=100,latest_close=101,period_high=102,
                          period_low=99,change_percent=1,bullish_bars=1,bearish_bars=0,neutral_bars=0,direction='bullish',method='fixture'))
    monkeypatch.setattr(module,'get_or_create_run_prompt_snapshot',prompts)
    monkeypatch.setattr(module,'run_streamed_analysis',workflow)
    manager=module.AnalysisRunManager(AnalysisTaskRepository(replay.repository.factory))
    rid=uuid.UUID(result['runs'][0]['run_id'])
    await manager.run(rid,'replay-analysis-test')
    async with replay.repository.factory() as db:
        run=await db.get(AnalysisRun,rid)
        assert run.status==('failed' if fails else 'completed')
        assert run.completed_at is not None
        if not fails:
            assert run.result_json['query']['replay_context']['saved_version_id']==v['id']
