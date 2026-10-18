import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { TradingChart } from '../TradingChart';
import { ChartWorkspace } from '../chartWorkspace/ChartWorkspace';
import type { ReplayCommandInput, ReplayOrder, TradeMarker } from '../types';
import { OrderTicket, type ChartOrderPlacement, type ChartOrderPrefill } from './OrderTicket';
import { ReplayTradeDetails } from './ReplayTradeDetails';
import { ReplayControls } from './ReplayControls';
import { ReplayExitDialog } from './ReplayExitDialog';
import type { ReplayTradingController } from './useReplayTradingController';
import { ReplayChartOverlay, type ChartOrderRequest } from './ReplayChartOverlay';
import './replayLayout.css';

export { orderLabels } from './ReplayTradeDetails';
export function ChartReplayTrading({controller:c,symbol,userScope='local',...chart}:ComponentProps<typeof TradingChart>&{controller:ReplayTradingController;symbol:string;userScope?:string}) {
 const r=c.replay,s=r.session;
 const [editing,setEditing]=useState<ReplayOrder>();
 const [chartOrder,setChartOrder]=useState<ChartOrderPlacement & {sessionId:string;revision:number}>();
 const [prefill,setPrefill]=useState<ChartOrderPrefill>();
 const [chartError,setChartError]=useState('');
 const [quickQuantity,setQuickQuantity]=useState(1);
 const [detailsOpen,setDetailsOpen]=useState(false);
 const [drawingEditing,setDrawingEditing]=useState(false);
 const chartFrame=useRef<HTMLDivElement>(null);
 const seed=useRef(0),latest=useRef(c);latest.current=c;
 const contextKey=`${c.id}:${s?.revision}:${r.period}:${r.observedCursor??''}`;
 const closeChartOrder=()=>{setChartOrder(undefined);setEditing(undefined);setPrefill(undefined);setChartError('');};
 useEffect(()=>{closeChartOrder();},[contextKey,c.exitOpen]);
 useEffect(()=>{setDetailsOpen(false);},[c.id]);
 function openChartOrder(request:ChartOrderRequest){
  const current=latest.current;
  if(!current.replay.canTrade||current.exitOpen||!current.replay.session)return;
  current.replay.setPlaying(false);setEditing(undefined);setChartError('');
  setPrefill({id:`chart-${++seed.current}`,side:request.side,kind:request.kind,price:request.price});
  setChartOrder({...request,sessionId:current.replay.session.id,revision:current.replay.session.revision});
 }
 function openAmendment(order:ReplayOrder){
  const current=latest.current,session=current.replay.session,container=chartFrame.current;
  if(!current.replay.canTrade||current.exitOpen||!session||!container)return;
  const pending=session.state.orders.find(candidate=>candidate.id===order.id&&candidate.status==='pending'&&candidate.kind!=='market');
  if(!pending)return;
  current.replay.setPlaying(false);setEditing(pending);setPrefill(undefined);setChartError('');
  setChartOrder({container,anchor:{x:12,y:64},sessionId:session.id,revision:session.revision});
  container.scrollIntoView?.({block:'start',behavior:'smooth'});
 }
 async function chartCommand(input:ReplayCommandInput,expected?:{sessionId:string;revision:number}){
  const current=latest.current,session=current.replay.session;
  if(!current.replay.canTrade||current.exitOpen||!session||
    (expected&&(session.id!==expected.sessionId||session.revision!==expected.revision))){
   setChartError('回放状态已变化，请在最新进度重新操作。');return false;
  }
  current.replay.setPlaying(false);setChartError('');
  return current.replay.command(input);
 }
 async function closeChartPosition(expected:{sessionId:string;revision:number}){
  const position=latest.current.replay.session?.state.position;
  if(!position?.quantity)return false;
  return chartCommand({type:'place_order',kind:'market',reduce_only:true,
   side:position.quantity>0?'sell':'buy',quantity:Math.abs(position.quantity)},expected);
 }
 const [cash,setCash]=useState('100000'),[fee,setFee]=useState('0'),[slip,setSlip]=useState(0),[profitTarget,setProfitTarget]=useState(''),[lossLimit,setLossLimit]=useState('');
 const validCash=cash.trim()!==''&&Number.isFinite(Number(cash))&&Number(cash)>0;
 const fills=r.events.items.flatMap(e=>'fills'in e?e.fills:[]);
 const visible=fills.filter(f=>!r.observedCursor||Date.parse(f.timestamp)<=Date.parse(r.observedCursor));
 const markers:TradeMarker[]=visible.map(f=>({timestamp:new Date(Date.parse(f.timestamp)-60000).toISOString(),position:f.side==='buy'?'belowBar':'aboveBar',shape:f.side==='buy'?'arrowUp':'arrowDown',color:f.side==='buy'?'#137e61':'#bd493c',text:`${f.side==='buy'?'买':'卖'} ${f.quantity} @${f.price}`}));
 return <div className={`chart-replay-trading${c.id&&s?' chart-replay-trading-active':''}`} data-replay-interactive>
  {!c.id ? <ChartWorkspace {...chart} symbol={symbol} userScope={userScope}
    onStartTradingReplay={start=>{
     if(!validCash)return;
     void c.start({mode:'training',symbol,start,random_start:!start,config:{initial_cash:cash,fee_per_side:fee,slippage_ticks:slip,profit_target:profitTarget||null,loss_limit:lossLimit||null}},'1m');
    }}
    replayStartDisabled={!validCash}
    replayStarting={c.busy} replayStartingSeconds={c.startingSeconds} onCancelTradingReplay={()=>c.requestLeave()}
    replaySetup={<div className="replay-start-settings">
     <label>初始资金 USD<input aria-label="初始资金 USD" required type="number" min="0.01" step="0.01" value={cash} disabled={c.busy} aria-invalid={!validCash} aria-describedby={!validCash?'replay-cash-error':undefined} onChange={e=>setCash(e.target.value)}/><span className="replay-required-label">必填</span></label>
     {!validCash&&<p id="replay-cash-error" role="alert">请输入大于 0 的初始资金。</p>}
     <details className="replay-settings"><summary>选填设置</summary>
      <div className="replay-settings-grid">
       <label>盈利目标 USD<input aria-label="盈利目标 USD" type="number" min="0.01" step="0.01" value={profitTarget} disabled={c.busy} onChange={e=>setProfitTarget(e.target.value)} placeholder="不限制"/></label>
       <label>亏损限制 USD<input aria-label="亏损限制 USD" type="number" min="0.01" step="0.01" value={lossLimit} disabled={c.busy} onChange={e=>setLossLimit(e.target.value)} placeholder="不限制"/></label>
       <label>每手每边费用<input type="number" min="0" step="0.01" value={fee} disabled={c.busy} onChange={e=>setFee(e.target.value)}/></label>
       <label>滑点 tick<input type="number" min="0" step="1" value={slip} disabled={c.busy} onChange={e=>setSlip(Number(e.target.value))}/></label>
      </div>
      <p className="replay-settings-help">盈亏目标可留空；达到后仅提示，回放和交易继续。</p>
     </details>
    </div>}
   /> : !s ? <div>{r.loading&&<p role="status">正在恢复回放… {r.loadingSeconds} 秒</p>}<button onClick={()=>c.requestLeave()}>退出加载</button></div> : <>
    <div ref={chartFrame} className="replay-chart-frame" aria-busy={r.busy}>
     <ChartWorkspace symbol={symbol} userScope={userScope} bars={r.bars} chartKey={`replay:${c.id}`} period={r.period} locked timezoneOffsetMinutes={chart.timezoneOffsetMinutes} markers={markers}
       onDrawingInteractionChange={setDrawingEditing}
       replay={{id:c.id,cursor:r.observedCursor??s.state.cursor,dataCursor:r.dataCursor,barsByPeriod:r.barsByPeriod,loading:r.loading,disabled:r.busy||r.retryAvailable||c.exitOpen||!!chartOrder,changeDisplayPeriods:r.changeDisplayPeriods,onPause:()=>r.setPlaying(false)}}
       renderOverlay={context=><ReplayChartOverlay context={context} session={s} period={r.period} stale={!!r.dataCursor&&r.dataCursor!==s.state.cursor} disabled={!r.canTrade||c.exitOpen||!!chartOrder||drawingEditing} hidden={!!r.observedCursor||s.mode!=='training'} contextKey={contextKey} pending={r.retryAvailable} quantity={quickQuantity} onQuantityChange={setQuickQuantity} onMarketOrder={(side,quantity,expected)=>chartCommand({type:'place_order',kind:'market',side,quantity,reduce_only:false},expected)} onPause={()=>r.setPlaying(false)} onOrder={openChartOrder} onClosePosition={closeChartPosition} onProtection={(input,expected)=>chartCommand({type:'update_protection',...input},expected)}/>}/>
     {r.loading&&(!r.bars.length||r.loadingSeconds>=1)&&<p className="replay-loading-status" role="status">正在加载行情及成交记录… {r.loadingSeconds} 秒 · 加载完成前不可下单 <button onClick={r.cancelLoad}>取消加载</button></p>}
    </div>
    <ReplayControls replay={r} compact trailing={<>
      {!r.historyExpanded&&<button className="replay-control-history" disabled={r.busy} aria-label="加载更多历史（最多 500 根）" title="加载更多历史（最多 500 根）" onClick={()=>void r.loadMoreHistory()}><span aria-hidden="true">＋</span> 历史</button>}
      <button className="replay-control-exit" aria-label="退出回放" title="退出回放，选择是否保存" onClick={()=>c.requestLeave()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg><span>退出</span></button>
    </>}/>
     {chartOrder&&<OrderTicket key={editing?.id??'new'} session={s} disabled={!r.canTrade||c.exitOpen} command={input=>chartCommand(input,chartOrder)} editing={editing} newOrderQuantity={quickQuantity} onNewOrderQuantityChange={setQuickQuantity} prefill={prefill} placement={chartOrder} error={chartError||r.error} onClose={closeChartOrder} onDone={closeChartOrder}/>}
    {chartError&&<p className="replay-inline-alert" role="alert">{chartError}</p>}
    {(s.state.account.profit_target_reached||s.state.account.loss_limit_reached)&&<p className="replay-limit-alert" role="status">{[
      s.state.account.profit_target_reached&&`已达到回放盈利目标 ${s.state.config.profit_target} USD`,
      s.state.account.loss_limit_reached&&`已达到回放亏损限制 ${s.state.config.loss_limit} USD`,
    ].filter(Boolean).join('；')}。回放和交易仍可继续。</p>}
    {r.observedCursor&&<p className="replay-observer-notice">正在回看，持仓与资金为最新回放进度；回到进度后可下单。</p>}
    <section className="replay-trade-details">
     <div className="replay-trade-details-heading"><button aria-label="交易明细" aria-expanded={detailsOpen} aria-controls={`replay-details-${c.id}`} onClick={()=>setDetailsOpen(open=>!open)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d={detailsOpen?'m5 12 5-5 5 5':'m5 8 5 5 5-5'}/></svg>交易明细</button><span>{s.state.position.quantity?`${s.state.position.quantity>0?'多':'空'} ${Math.abs(s.state.position.quantity)} 手`:'空仓'}<span className="replay-details-equity"> · 权益 {Number(s.state.account.equity).toLocaleString('en-US',{maximumFractionDigits:2})} USD</span></span></div>
     {detailsOpen&&<div id={`replay-details-${c.id}`} className="replay-trade-details-body">
      <ReplayTradeDetails key={s.id} session={s} events={r.events} visibleFills={visible} disabled={!r.canTrade||c.exitOpen} command={chartCommand} onAmend={openAmendment} onPause={()=>r.setPlaying(false)}/>
     </div>}
    </section>
   </>}
   {(c.error||r.error)&&<p role="alert">{c.error||r.error}{c.id&&<button disabled={r.busy} onClick={()=>void(r.retryAvailable?r.command():r.refresh())}>{r.retryAvailable?'重试确认提交结果':'重试加载'}</button>}</p>}
   {c.exitOpen&&<ReplayExitDialog controller={c}/>}
 </div>;
}
