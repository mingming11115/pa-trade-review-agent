import { useState } from 'react';
import { ChartWorkspace } from '../chartWorkspace/ChartWorkspace';
import type { ReplayOrder, TradeMarker } from '../types';
import { useReplaySession } from './useReplaySession';
import { OrderTicket } from './OrderTicket';
import { PositionPanel } from './PositionPanel';
import { ReplayResults } from './ReplayResults';
import { ReplayControls } from './ReplayControls';
import { endTraining } from './replayActions';

export function TrainingWorkspace({sessionId,userScope='local'}:{sessionId:string;userScope?:string}) {
  const r=useReplaySession(sessionId);
  const [editing,setEditing]=useState<ReplayOrder>();
  const s=r.session;
  if(!s)return <p role="status">{r.error||'正在恢复训练…'}{r.error&&<button onClick={()=>void r.refresh()}>重试</button>}</p>;
  const fills=r.events.items.flatMap(e=>'fills'in e?e.fills:[]);
  const visibleFills=fills.filter(f=>!r.observedCursor||Date.parse(f.timestamp)<=Date.parse(r.observedCursor));
  const markers:TradeMarker[]=visibleFills.map(f=>({timestamp:new Date(Date.parse(f.timestamp)-60000).toISOString(),position:f.side==='buy'?'belowBar':'aboveBar',shape:f.side==='buy'?'arrowUp':'arrowDown',color:f.side==='buy'?'#137e61':'#bd493c',text:`${f.side==='buy'?'买':'卖'} ${f.quantity} @${f.price}`}));
  async function finish() {
    r.setPlaying(false);
    await endTraining(s!.state.orders,s!.state.position.quantity,r.command);
  }
  return <><header className="replay-session-heading"><strong>{s.symbol} · {s.status==='active'?'训练中':s.status==='completed'?'已结束':'已放弃'}</strong><span>已自动保存 · 修订 {s.revision}</span><button disabled={!r.canTrade} onClick={()=>void finish()}>{s.state.position.quantity?'全部平仓并结束':'结束训练'}</button><button disabled={!r.canTrade} onClick={()=>{r.setPlaying(false);void r.command({type:'abandon'});}}>放弃场次</button></header>
    <p className="replay-notice">市价单按当前回放价立即成交，限价/条件单按分钟撮合；同分钟止损止盈顺序不明时止损优先。无保证金强平、排队和部分成交模拟。每手每边手续费 {s.state.config.fee_per_side} USD，滑点 {s.state.config.slippage_ticks} tick。</p>
    {r.observedCursor&&<p className="replay-notice">正在回看，成交与资金仍为最新训练状态；回到训练进度后可下单。</p>}
    {r.error&&<div role="alert">{r.error} <button onClick={()=>void(r.retryAvailable?r.command():r.refresh())}>{r.retryAvailable?'重试确认提交结果':'刷新'}</button></div>}
    {r.busy&&<p role="status">正在加载或保存…</p>}
    <ReplayControls replay={r}/><ReplayResults session={s} events={r.events}/>
    <div className="replay-trading-grid"><div><ChartWorkspace symbol={s.symbol} userScope={userScope} bars={r.bars} chartKey={`training:${sessionId}`} period={r.period} locked timezoneOffsetMinutes={480} markers={markers}
      replay={{id:sessionId,cursor:r.observedCursor??s.state.cursor,dataCursor:r.dataCursor,barsByPeriod:r.barsByPeriod,loading:r.loading,disabled:r.busy||r.retryAvailable,changeDisplayPeriods:r.changeDisplayPeriods,onPause:()=>r.setPlaying(false)}}/></div>
      <aside><OrderTicket key={editing?.id??'new'} session={s} disabled={!r.canTrade} command={r.command} editing={editing} onDone={()=>setEditing(undefined)}/><PositionPanel key={`${s.state.position.stop_loss}:${s.state.position.take_profit}:${s.state.position.quantity}`} session={s} disabled={!r.canTrade} command={r.command}/></aside></div>
    <h3>订单</h3><div className="replay-table"><table><thead><tr><th>方向</th><th>类型</th><th>数量</th><th>限价</th><th>状态</th><th>操作</th></tr></thead><tbody>{s.state.orders.map(o=><tr key={o.id}><td>{o.side==='buy'?'买':'卖'}{o.reduce_only?' · 平仓':''}</td><td>{o.kind==='market'?'市价':'限价'}</td><td>{o.quantity}</td><td>{o.limit_price??'—'}</td><td>{o.status==='pending'?'等待成交':o.status==='filled'?'已成交':`已撤销 ${o.reason}`}</td><td>{o.status==='pending'&&<><button disabled={!r.canTrade} onClick={()=>void r.command({type:'cancel_order',order_id:o.id})}>撤单</button>{o.kind==='limit'&&<button disabled={!r.canTrade} onClick={()=>setEditing(o)}>修改</button>}</>}</td></tr>)}</tbody></table></div>
    <h3>成交记录</h3><div className="replay-table"><table><thead><tr><th>时间</th><th>成交</th><th>价格</th><th>费用</th><th>说明</th></tr></thead><tbody>{visibleFills.map((f,i)=><tr key={i}><td>{new Date(f.timestamp).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</td><td>{f.side==='buy'?'买':'卖'} {f.quantity}</td><td>{f.price}</td><td>{f.fee}</td><td>{f.reason}{f.ambiguous?' · 顺序不明，止损优先':''}</td></tr>)}</tbody></table></div>
  </>;
}
