import { useEffect, useRef, useState } from 'react';
import { TradingChart } from '../TradingChart';
import { getReplayBars, analyzeSavedReplay, getSavedReplayAnalyses, getRunDetail, createTraceId, cancelRun, type ReplayAnalysisLink } from '../api';
import type { Bar, SavedReplay } from '../types';
import { ReplayResults } from './ReplayResults';
import { errorText } from './useReplaySession';
import { orderLabels } from './ChartReplayTrading';

export function SavedReplayDetail({value:v,onResume,onResult}:{value:SavedReplay;onResume:()=>void;onResult?:(taskId:string,runId:string)=>void}){
 const [links,setLinks]=useState<ReplayAnalysisLink[]>([]),[statuses,setStatuses]=useState<Record<string,string>>({}),[failures,setFailures]=useState<Record<string,string>>({});
 const [analyzing,setAnalyzing]=useState(false);
 const operation=useRef(createTraceId());
 useEffect(()=>{let active=true;getSavedReplayAnalyses(v.id).then(data=>{if(active)setLinks(data.items);}).catch(e=>{if(active)setError(errorText(e));});return()=>{active=false;};},[v.id]);
 useEffect(()=>{if(!links.length)return;let active=true;let timer:ReturnType<typeof setTimeout>;
  async function poll(){try{const details=await Promise.all(links.flatMap(l=>l.runs.map(r=>getRunDetail(r.run_id))));if(!active)return;setStatuses(Object.fromEntries(details.map(d=>[d.run_id,d.status])));setFailures(Object.fromEntries(details.map(d=>[d.run_id,d.failure_message??''])));if(details.some(d=>['queued','running','cancel_requested'].includes(d.status)))timer=setTimeout(()=>void poll(),1500);}catch(e){if(active)setError(errorText(e));}}
  void poll();return()=>{active=false;clearTimeout(timer);};
 },[links]);
 async function analyze(){if(analyzing)return;setAnalyzing(true);setError('');try{const link=await analyzeSavedReplay(v.id,operation.current,['5m']);setLinks(current=>[...current.filter(l=>l.task_id!==link.task_id),link]);operation.current=createTraceId();}catch(e){setError(errorText(e));}finally{setAnalyzing(false);}}
 const [bars,setBars]=useState<Bar[]>([]),[error,setError]=useState('');
 useEffect(()=>{const abort=new AbortController();getReplayBars(v.session.id,'1m',v.session.state.cursor,abort.signal).then(b=>setBars(b.bars)).catch(e=>{if(!abort.signal.aborted)setError(errorText(e));});return()=>abort.abort();},[v.id,v.session.id,v.session.state.cursor]);
 const fills=v.events.flatMap(e=>'fills'in e?e.fills:[]);
 return <><h3>{v.name}</h3><button onClick={onResume}>继续回放</button> <button className="primary-action" disabled={analyzing||Object.values(statuses).some(s=>['queued','running','cancel_requested'].includes(s))} onClick={()=>void analyze()}>{analyzing?'正在提交…':'分析'}</button>
 <p>分析使用此保存版本，包含成交与未平仓状态；保存本身不会调用模型。</p>
 {links.flatMap(link=>link.runs.map(run=>{const status=statuses[run.run_id]??run.status;return <div className="replay-saved-row" key={run.run_id}><span>{run.period} · {({queued:'等待分析',running:'分析中',completed:'分析完成',completed_with_warnings:'完成（有警告）',degraded:'分析降级',failed:'分析失败',timed_out:'分析超时',cancelled:'已取消',cancel_requested:'正在取消'} as Record<string,string>)[status]??status} {failures[run.run_id]}</span>{['completed','completed_with_warnings','degraded'].includes(status)&&<button onClick={()=>onResult?.(link.task_id,run.run_id)}>查看分析结果</button>}{['queued','running'].includes(status)&&<button onClick={()=>void cancelRun(run.run_id).then(()=>setStatuses(x=>({...x,[run.run_id]:'cancel_requested'}))).catch(e=>setError(errorText(e)))}>取消分析</button>}</div>;}))}{error&&<p role="alert">{error}</p>}
 <TradingChart bars={bars} chartKey={`saved:${v.id}`} period="1m" locked timezoneOffsetMinutes={480}/>
 <ReplayResults session={v.session} events={{items:v.events,next_offset:null}}/>
 <p>保存时持仓 {v.session.state.position.quantity} 手 · 均价 {v.session.state.position.average_price} · 未平仓按保存时状态保留</p>
 <h3>订单</h3>{v.session.state.orders.map(o=><p key={o.id}>{o.side==='buy'?'买入':'卖出'} {o.quantity} · {orderLabels[o.kind]} · 限价 {o.limit_price??'—'} / 触发 {o.stop_price??'—'} · {o.status==='pending'?'待成交':o.status==='filled'?'已成交':'已撤销'}</p>)}
 <h3>成交</h3>{fills.map((f,i)=><p key={i}>{new Date(f.timestamp).toLocaleString('zh-CN')} · {f.side==='buy'?'买入':'卖出'} {f.quantity} @ {f.price} · 费用 {f.fee}</p>)}
 </>;
}
