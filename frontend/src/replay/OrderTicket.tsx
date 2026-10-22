import { canMarketTrade } from '../tradingHours';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ReplayCommandInput, ReplayOrder, ReplaySession } from '../types';

export interface ChartOrderPrefill {id:string;side:ReplayOrder['side'];kind:ReplayOrder['kind'];price:string}
export interface ChartOrderPlacement {container:HTMLElement;anchor:{x:number;y:number}}

export function OrderTicket({session,disabled,command,editing,onDone,prefill,placement,onClose,error,newOrderQuantity,onNewOrderQuantityChange}:{session:ReplaySession;disabled:boolean;command:(c:ReplayCommandInput)=>Promise<boolean>;editing?:ReplayOrder;onDone?:()=>void;prefill?:ChartOrderPrefill;placement?:ChartOrderPlacement;onClose?:()=>void;error?:string;newOrderQuantity?:number;onNewOrderQuantityChange?:(quantity:number)=>void}) {
  const [side,setSide]=useState<'buy'|'sell'>(editing?.side??'buy');
  const [kind,setKind]=useState<ReplayOrder['kind']>(editing?.kind??'market');
  const [localQuantity,setLocalQuantity]=useState(editing?.quantity??1);
  const quantity=editing?localQuantity:(newOrderQuantity??localQuantity);
  const setQuantity=(value:number)=>{setLocalQuantity(value);if(!editing)onNewOrderQuantityChange?.(value);};
  const [limit,setLimit]=useState(editing?.limit_price??'');
  const [trigger,setTrigger]=useState(editing?.stop_price??'');
  const [stop,setStop]=useState(editing?.stop_loss??'');
  const [profit,setProfit]=useState(editing?.take_profit??'');
  const [reduce,setReduce]=useState(editing?.reduce_only??false);
  const popup=useRef<HTMLDivElement>(null);
  const close=useRef(onClose);close.current=onClose;
  useEffect(()=>{
    if(!prefill||editing)return;
    setSide(prefill.side);setKind(prefill.kind);setLimit(prefill.price);setTrigger(prefill.price);
    setStop('');setProfit('');setReduce(false);
  },[prefill,editing]);
  useEffect(()=>{
    if(!placement)return;
    const dismiss=(event:PointerEvent)=>{if(event.target instanceof Node&&!popup.current?.contains(event.target))close.current?.();};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')close.current?.();};
    popup.current?.querySelector('input')?.focus({preventScroll:true});
    document.addEventListener('pointerdown',dismiss);
    document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',dismiss);document.removeEventListener('keydown',escape);};
  },[placement]);
  const marketBlocked=kind==="market"&&!canMarketTrade(session);
  const tick=session.config.instrument.tick_size;
  const ticket=<form className="replay-ticket" onKeyDown={e=>{if(e.key==='Enter'&&e.target instanceof HTMLInputElement){e.preventDefault();if(!disabled)e.currentTarget.requestSubmit();}}} onSubmit={async e=>{e.preventDefault();if(disabled||marketBlocked)return;const submitter=(e.nativeEvent as SubmitEvent).submitter;const direction=submitter?.getAttribute('data-side');const orderSide=direction==='buy'||direction==='sell'?direction:side;if(await command({type:editing?'amend_order':'place_order',order_id:editing?.id,side:orderSide,kind,quantity,limit_price:kind==='limit'||kind==='stop_limit'?limit:null,stop_price:kind.startsWith('stop_')?trigger:null,stop_loss:stop||null,take_profit:profit||null,reduce_only:reduce}))onDone?.();}}>
    <h3>{editing?'修改挂单':'下单'}</h3>
    <fieldset disabled={disabled}>
      <label>方向<select aria-label="下单方向" value={side} onChange={e=>setSide(e.target.value as 'buy'|'sell')}><option value="buy">买入 / 做多</option><option value="sell">卖出 / 做空</option></select></label>
      <label>类型<select aria-label="订单类型" value={kind} onChange={e=>setKind(e.target.value as ReplayOrder['kind'])}><option value="market">市价</option><option value="limit">限价</option><option value="stop_market">止损市价</option><option value="stop_limit">止损限价</option></select></label>
      <label>手数<input aria-label="下单手数" type="number" min="1" max="100000" step="1" required value={quantity} onChange={e=>setQuantity(Number(e.target.value))}/></label>
      {kind.startsWith('stop_')&&<label>触发价<input aria-label="触发价" type="number" step={tick} required value={trigger} onChange={e=>setTrigger(e.target.value)}/></label>}
      {(kind==='limit'||kind==='stop_limit')&&<label>限价<input aria-label="限价" type="number" step={tick} required value={limit} onChange={e=>setLimit(e.target.value)}/></label>}
      <label><input type="checkbox" checked={reduce} onChange={e=>setReduce(e.target.checked)}/>只减仓</label>
      {!reduce&&!session.state.position.quantity&&<><label>止损<input aria-label="开仓止损" type="number" step={tick} value={stop} onChange={e=>setStop(e.target.value)}/></label><label>止盈<input aria-label="开仓止盈" type="number" step={tick} value={profit} onChange={e=>setProfit(e.target.value)}/></label></>}
      <button className="primary-action" type="submit" disabled={marketBlocked} data-side="buy" onClick={()=>setSide('buy')}>{editing?'买入改单':'买入'}</button><button className="replay-sell" type="submit" disabled={marketBlocked} data-side="sell" onClick={()=>setSide('sell')}>{editing?'卖出改单':'卖出'}</button>
      {editing&&<button type="button" onClick={onDone}>取消修改</button>}
    </fieldset>
    {marketBlocked&&<p role="status">请推进至 RTH 后交易</p>}
    <small>{kind==='market'?'按当前回放价立即成交':'从下一分钟开始按条件撮合'} · tick {tick} · 乘数 {session.config.instrument.multiplier}</small>
  </form>;
  if(!placement)return ticket;
  const bounds=placement.container.getBoundingClientRect();
  const width=Math.min(320,Math.max(180,bounds.width-16));
  const left=Math.max(8,Math.min(placement.anchor.x,bounds.width-width-8));
  const top=Math.max(8,Math.min(placement.anchor.y,bounds.height-380));
  return createPortal(<div ref={popup} role="dialog" aria-label="图表下单" className="chart-order-ticket" style={{left,top,width,maxHeight:Math.max(160,bounds.height-top-8)}} onPointerDown={e=>e.stopPropagation()} onPointerUp={e=>e.stopPropagation()} onContextMenu={e=>{e.preventDefault();e.stopPropagation();}} onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();onClose?.();}}}>
    <header><strong>图表下单</strong><button type="button" aria-label="关闭图表下单" onClick={onClose}>×</button></header>{ticket}{error&&<p role="alert">{error}</p>}
  </div>,placement.container);
}
