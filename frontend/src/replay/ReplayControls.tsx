import type { ReactNode } from 'react';
import type { Period, TradingHours } from '../types';
import type { useReplaySession } from './useReplaySession';

export function ReplayControls({replay,compact=false,trailing}:{replay:ReturnType<typeof useReplaySession>;compact?:boolean;trailing?:ReactNode}) {
  const s=replay.session!;
  const playbackName=replay.playing?'暂停':'播放';
  return <div className={`replay-controls${compact?' replay-controls-compact':''}`} role="group" aria-label="回放控制">
    {compact&&<span className="replay-controls-caption">回放</span>}
    <button className={compact?'replay-control-icon':undefined} aria-label={playbackName} title={playbackName} disabled={replay.busy||!replay.canTrade} onClick={()=>replay.setPlaying(!replay.playing)}>{compact?<svg viewBox="0 0 24 24" aria-hidden="true">{replay.playing?<><path d="M8 5v14M16 5v14"/></>:<path d="m7 4 13 8-13 8Z"/>}</svg>:playbackName}</button>
    <button className={compact?'replay-control-icon':undefined} aria-label="下一根" title="下一根" disabled={!replay.canTrade} onClick={()=>{replay.setPlaying(false);void replay.command({type:'advance',period:replay.period});}}>{compact?<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 4 11 8-11 8ZM20 4v16"/></svg>:'下一根'}</button>
    <label className="replay-control-period">推进周期<select disabled={replay.busy} aria-label="回放图表周期" title="每次推进的周期，不改变分屏显示周期" value={replay.period} onChange={e=>void replay.changePeriod(e.target.value as Period)}>{['1m','5m','15m','30m','1h','4h','1d'].map(p=><option key={p}>{p}</option>)}</select></label>
    <label className="replay-control-period"><select disabled={!replay.canTrade} aria-label="回放交易时段" title="回放交易时段" value={s.state.config?.trading_hours??"ETH"} onChange={e=>void replay.changeTradingHours(e.target.value as TradingHours)}><option value="ETH">ETH 全时段</option><option value="RTH">RTH 常规时段</option></select></label>
    <span className="replay-control-time">北京时间 {new Date(replay.observedCursor??s.state.cursor).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</span>
    {s.mode==='historical'?<><button disabled={replay.busy} onClick={()=>void replay.command({type:'seek',target:'previous'})}>上一笔成交</button><button disabled={replay.busy} onClick={()=>void replay.command({type:'seek',target:'next'})}>下一笔成交</button></>:replay.observedCursor&&<button disabled={replay.busy} onClick={()=>void replay.observe()}>回到最新进度</button>}
    {trailing&&<div className="replay-control-trailing">{trailing}</div>}
  </div>;
}
