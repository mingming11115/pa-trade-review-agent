import { useEffect, useRef, useState } from 'react';
import { createTraceId, saveReplay, listLegacyReplays, getSavedReplay, listReplayDrafts, listSavedReplays, resumeReplay } from '../api';
import type { ReplaySession, SavedReplay } from '../types';
import { errorText } from './useReplaySession';
import { SavedReplayDetail } from './SavedReplayDetail';
import { copyDrawingScope } from '../chartWorkspace/chartDrawings';

export function SavedReplayList({onClose,onResume,onResult,embedded=false,userScope='local'}:{onClose:()=>void;onResume:(id:string,symbol:string)=>void;onResult?:(taskId:string,runId:string)=>void;embedded?:boolean;userScope?:string}){
 const [items,setItems]=useState<SavedReplay[]>([]),[drafts,setDrafts]=useState<ReplaySession[]>([]),[selected,setSelected]=useState<SavedReplay|null>(null);
 const [legacy,setLegacy]=useState<ReplaySession[]>([]);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[offset,setOffset]=useState(0);
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const operation=useRef<{id:string;op:string}|null>(null);
 useEffect(()=>{let live=true;setBusy(true);Promise.all([listSavedReplays(offset),listReplayDrafts(),listLegacyReplays(offset)]).then(([a,b,l])=>{if(live){setItems(a.items??[]);setDrafts(b.items??[]);setLegacy(l.items??[]);}}).catch(e=>{if(live)setError(errorText(e));}).finally(()=>{if(live)setBusy(false);});return()=>{live=false;};},[offset]);
 async function importLegacy(s:ReplaySession){setBusy(true);setError('');try{const v=await saveReplay(s.id,`${s.symbol} 旧版回放`,'legacy-import',s.revision);if(mounted.current){setSelected(v);setLegacy(xs=>xs.filter(x=>x.id!==s.id));}}catch(e){setError(errorText(e));}finally{setBusy(false);}}
 async function view(id:string){setBusy(true);setError('');try{const v=await getSavedReplay(id);if(mounted.current)setSelected(v);}catch(e){setError(errorText(e));}finally{setBusy(false);}}
 async function resume(id:string){setBusy(true);setError('');try{if(operation.current?.id!==id)operation.current={id,op:createTraceId()};const s=await resumeReplay(id,operation.current!.op);if(mounted.current){copyDrawingScope(`${userScope}:${s.symbol}:saved:${id}`,`${userScope}:${s.symbol}:replay:${s.id}`);onResume(s.id,s.symbol);}}catch(e){setError(errorText(e));}finally{setBusy(false);}}
 return <section className={embedded?'replay-records-embedded':'replay-workspace'} aria-label="回放记录">{!embedded&&<header><h2>回放记录</h2><button onClick={onClose}>返回工作台</button></header>}
 {error&&<p role="alert">{error}</p>}{busy&&<p role="status">正在加载…</p>}
 {selected?<><button onClick={()=>setSelected(null)}>返回记录列表</button><SavedReplayDetail value={selected} onResume={()=>void resume(selected.id)} onResult={onResult} userScope={userScope}/></>:<>
 <p>在 K 线图点击「回放」即可买卖；退出时选择保存，记录会显示在这里。</p>
 {items.length===0&&!busy&&<p>暂无已保存回放</p>}
 {items.map(v=><article className="replay-saved-row" key={v.id}><div><strong>{v.name}</strong><p>{v.session.symbol} · {new Date(v.created_at).toLocaleString('zh-CN')} · 已实现盈亏 {v.session.state.account.realized_pnl} USD</p></div><button disabled={busy} onClick={()=>void view(v.id)}>查看 / 分析</button><button disabled={busy} onClick={()=>void resume(v.id)}>继续回放</button></article>)}
 <button disabled={busy||offset===0} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</button><button disabled={busy||items.length<20&&legacy.length<20} onClick={()=>setOffset(offset+20)}>下一页</button>
 {legacy.length>0&&<><h3>旧版已结束回放</h3>{legacy.map(s=><article className="replay-saved-row" key={s.id}><span>{s.symbol} · {new Date(s.created_at).toLocaleString('zh-CN')} · {s.status==='completed'?'已结束':'已放弃'}</span><button disabled={busy} onClick={()=>void importLegacy(s)}>保存并查看旧记录</button></article>)}</>}
 {drafts.length>0&&<><h3>可恢复草稿</h3><p>包含尚未选择保存的回放及旧版训练场次。</p>{drafts.map(d=><article className="replay-saved-row" key={d.id}><span>{d.symbol} · {new Date(d.state.cursor).toLocaleString('zh-CN')}</span><button onClick={()=>onResume(d.id,d.symbol)}>恢复草稿</button></article>)}</>}
 </>}
 </section>;
}
