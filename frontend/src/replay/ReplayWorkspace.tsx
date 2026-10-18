import { useEffect, useState } from 'react';
import { createReplaySession, getRecentTrades, listReplaySessions } from '../api';
import type { ReplayMode, ReplaySession, Trade } from '../types';
import { TrainingWorkspace } from './TrainingWorkspace';
import { HistoricalWorkspace } from './HistoricalWorkspace';
import { errorText } from './useReplaySession';

export function ReplayWorkspace({mode,initialTradeIds=[],onClose,closeLabel='返回工作台',userScope='local'}:{mode:ReplayMode;initialTradeIds?:string[];onClose:()=>void;closeLabel?:string;userScope?:string}) {
  const [sessions,setSessions]=useState<ReplaySession[]>([]);
  const [sessionId,setSessionId]=useState<string|null>(null);
  const [trades,setTrades]=useState<Trade[]>([]);
  const [selected,setSelected]=useState(initialTradeIds);
  const [symbol,setSymbol]=useState('ES');
  const [time,setTime]=useState('');
  const [end,setEnd]=useState('');
  const [random,setRandom]=useState(false);
  const [cash,setCash]=useState('100000');
  const [fee,setFee]=useState('0');
  const [slippage,setSlippage]=useState(0);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [offset,setOffset]=useState(0);
  const title=mode==='training'?'模拟交易训练':'真实交易回放';
  useEffect(()=>{
    const controller=new AbortController();
    setBusy(true);setError('');
    Promise.all([listReplaySessions(mode,offset,controller.signal),mode==='historical'?getRecentTrades():Promise.resolve([])])
      .then(([page,rows])=>{if(controller.signal.aborted)return;setSessions(page.items);setTrades(rows);const chosen=rows.find(t=>initialTradeIds.includes(t.id));if(chosen)setSymbol(chosen.symbol_root);})
      .catch(e=>{if(!controller.signal.aborted)setError(errorText(e));})
      .finally(()=>{if(!controller.signal.aborted)setBusy(false);});
    return ()=>controller.abort();
  },[mode,offset,sessionId]);
  async function create() {
    setBusy(true);setError('');
    try {
      const s=await createReplaySession({mode,symbol,start:time?new Date(`${time}+08:00`).toISOString():undefined,end:end?new Date(`${end}+08:00`).toISOString():undefined,
        random_start:mode==='training'&&random,trade_ids:mode==='historical'?selected:[],config:{initial_cash:cash,fee_per_side:fee,slippage_ticks:slippage}});
      setSessionId(s.id);
    } catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  return <section className="replay-workspace" aria-label={title}><header className="replay-heading"><div><small>PA / REPLAY</small><h2>{title}</h2></div><div>{sessionId&&<button onClick={()=>setSessionId(null)}>返回场次</button>}<button onClick={onClose}>{closeLabel}</button></div></header>
    {sessionId?(mode==='training'?<TrainingWorkspace key={sessionId} sessionId={sessionId} userScope={userScope}/>:<HistoricalWorkspace key={sessionId} sessionId={sessionId} userScope={userScope}/>):<>
      {error&&<p role="alert">{error}</p>}
      <div className="replay-setup-grid"><form className="replay-create" onSubmit={e=>{e.preventDefault();void create();}}><h3>{mode==='training'?'开始一次训练':'选择交易回放'}</h3><fieldset disabled={busy}>
        <label>品种<select aria-label="回放品种" value={symbol} onChange={e=>{setSymbol(e.target.value);setSelected([]);}}>{['ES','NQ','MES','MNQ','MGC'].map(s=><option key={s}>{s}</option>)}</select></label>
        <label>开始时间（北京时间）<input aria-label="开始时间（北京时间）" type="datetime-local" required={mode==='training'&&!random} disabled={random} value={time} onChange={e=>setTime(e.target.value)}/></label>
        {mode==='training'?<><label><input type="checkbox" checked={random} onChange={e=>setRandom(e.target.checked)}/>随机起点（2023 年至今）</label>
          <label>初始资金 USD<input aria-label="初始资金" required type="number" min="0.01" step="0.01" value={cash} onChange={e=>setCash(e.target.value)}/></label>
          <label>每手每边手续费<input aria-label="每手每边手续费" required type="number" min="0" step="0.01" value={fee} onChange={e=>setFee(e.target.value)}/></label>
          <label>滑点 tick<input aria-label="滑点 tick" required type="number" min="0" step="1" value={slippage} onChange={e=>setSlippage(Number(e.target.value))}/></label>
        </>:<><label>结束时间（北京时间）<input type="datetime-local" value={end} onChange={e=>setEnd(e.target.value)}/></label><small>时间留空时自动覆盖所选交易前后各 60 分钟。</small>
          <div className="replay-trade-picker">{!busy&&!trades.some(t=>t.symbol_root===symbol)&&<p>当前品种暂无交易记录，可切换品种，或先在「交易记录 → 真实交易」中导入。</p>}{trades.filter(t=>t.symbol_root===symbol).map(t=><label key={t.id}><input type="checkbox" checked={selected.includes(t.id)} onChange={e=>setSelected(old=>e.target.checked?[...old,t.id]:old.filter(id=>id!==t.id))}/><span>{t.contract_name} · {new Date(t.entered_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · {t.direction==='long'?'多':'空'} {t.size}</span></label>)}</div></>}
        <button className="primary-action" disabled={mode==='historical'&&!selected.length}>{busy?'加载中…':mode==='training'?'开始训练':'开始真实回放'}</button>
      </fieldset></form><section className="replay-sessions"><h3>已保存场次</h3>{sessions.length?sessions.map(s=><button className="replay-session-card" key={s.id} onClick={()=>setSessionId(s.id)}><strong>{s.symbol} · {s.status==='active'?'继续':s.status==='completed'?'已结束':'已放弃'}</strong><span>{new Date(s.state.cursor).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</span></button>):<p>{mode==='training'?'暂无训练记录':'暂无回放记录'}</p>}
        <div><button disabled={!offset||busy} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</button><button disabled={sessions.length<20||busy} onClick={()=>setOffset(offset+20)}>下一页</button></div>
      </section></div>
    </>}
  </section>;
}
