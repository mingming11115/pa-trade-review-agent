import { TradingChart } from '../TradingChart';
import type { HistoricalReplayEvent, TradeMarker } from '../types';
import { useReplaySession } from './useReplaySession';
import { ReplayControls } from './ReplayControls';
import { mapReplayMarkers } from './replayActions';

export function HistoricalWorkspace({sessionId}:{sessionId:string}) {
  const r=useReplaySession(sessionId);
  if(!r.session)return <p role="status">{r.error||'正在恢复回放…'}{r.error&&<button onClick={()=>void r.refresh()}>重试</button>}</p>;
  const events=r.events.items.filter((e):e is HistoricalReplayEvent=>'timestamp'in e);
  const markers:TradeMarker[]=events.map(e=>{const buy=(e.type==='entry')===(e.direction==='long');return {timestamp:new Date(Math.floor(Date.parse(e.timestamp)/60000)*60000).toISOString(),position:buy?'belowBar':'aboveBar',shape:buy?'arrowUp':'arrowDown',color:buy?'#137e61':'#bd493c',text:`${e.type==='entry'?'开':'平'} ${e.size} @${e.price}`};});
  return <><h3>{r.session.symbol} · 真实交易回放</h3><p className="replay-notice">连续合约行情可能与原交易到期合约不同；成交标记使用原记录价格，浮动盈亏为参考估算。开平仓摘要无法还原未记录的逐次成交。</p>
    {r.error&&<div role="alert">{r.error}<button onClick={()=>void(r.retryAvailable?r.command():r.refresh())}>重试</button></div>}
    {r.busy&&<p role="status">正在加载…</p>}<ReplayControls replay={r}/>
    <div className="replay-stats"><div><small>净持仓</small><strong>{r.events.history?.quantity??'0'}</strong></div><div><small>已实现盈亏（报告 / 估算）</small><strong>{r.events.history?.realized_pnl??'0'}</strong></div><div><small>浮动盈亏（参考估算）</small><strong>{r.events.history?.unrealized_pnl??'0'}</strong></div></div>
    <TradingChart bars={r.bars} chartKey={`historical:${sessionId}`} locked period={r.period} timezoneOffsetMinutes={480} markers={mapReplayMarkers(markers,r.bars)}/>
    <h3>已发生的成交</h3><div className="replay-table"><table><thead><tr><th>实际合约</th><th>时间</th><th>事件</th><th>手数</th><th>原成交价</th><th>盈亏</th></tr></thead><tbody>{events.map(e=><tr key={`${e.id}:${e.type}`}><td>{e.contract_name}</td><td>{new Date(e.timestamp).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</td><td>{e.type==='entry'?'开仓':'平仓'} · {e.direction==='long'?'多':'空'}</td><td>{e.size}</td><td>{e.price}</td><td>{e.pnl===undefined?'—':`${e.pnl} ${e.estimated?'估算':'报告值'}`}</td></tr>)}</tbody></table></div>
  </>;
}
