import { useState } from 'react';
import type { ReplaySession, ReplayCommandInput } from '../types';

export function PositionPanel({session,disabled,command}:{session:ReplaySession;disabled:boolean;command:(c:ReplayCommandInput)=>Promise<boolean>}) {
  const p=session.state.position;
  const [stop,setStop]=useState(p.stop_loss??'');
  const [profit,setProfit]=useState(p.take_profit??'');
  return <section className="replay-position"><h3>持仓</h3><p>{p.quantity?`${p.quantity>0?'多':'空'} ${Math.abs(p.quantity)} 手 · 均价 ${p.average_price}`:'空仓'}</p>
    <p>止损 {p.stop_loss??'未设'} / 止盈 {p.take_profit??'未设'}</p>
    {!!p.quantity&&<><form onSubmit={e=>{e.preventDefault();void command({type:'update_protection',stop_loss:stop||null,take_profit:profit||null});}}><fieldset disabled={disabled}>
      <label>持仓止损<input aria-label="持仓止损" type="number" step={session.config.instrument.tick_size} value={stop} onChange={e=>setStop(e.target.value)}/></label>
      <label>持仓止盈<input aria-label="持仓止盈" type="number" step={session.config.instrument.tick_size} value={profit} onChange={e=>setProfit(e.target.value)}/></label>
      <button>更新保护价</button></fieldset></form>
      <button disabled={disabled} onClick={()=>void command({type:'place_order',reduce_only:true,side:p.quantity>0?'sell':'buy',quantity:Math.abs(p.quantity)})}>提交全部平仓</button></>}
  </section>;
}
