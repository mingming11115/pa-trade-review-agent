import { useCallback, useEffect, useRef, useState } from 'react';
import { createTraceId, getReplayBars, getReplayEvents, getReplaySession, sendReplayCommand } from '../api';
import type { Bar, Period, ReplayCommandInput, ReplayEvents, ReplaySession } from '../types';

export function errorText(error:unknown):string {
  const e = error as {message?:string;detail?:unknown};
  return e?.message || (typeof e?.detail==='string' ? e.detail : '请求失败，请重试');
}

// Race aborts as well as fetch: a cancelled or stalled response must never keep the UI busy.
export async function replayRequest<T>(controller:AbortController, milliseconds:number, message:string, operation:()=>Promise<T>):Promise<T> {
  let rejectAbort:(()=>void)|undefined;
  const aborted=new Promise<never>((_,reject)=>{
    rejectAbort=()=>reject(controller.signal.reason??new DOMException('Aborted','AbortError'));
    controller.signal.addEventListener('abort',rejectAbort,{once:true});
    if(controller.signal.aborted)rejectAbort();
  });
  const timer=window.setTimeout(()=>controller.abort(new Error(message)),milliseconds);
  try {return await Promise.race([operation(),aborted]);}
  finally {window.clearTimeout(timer);if(rejectAbort)controller.signal.removeEventListener('abort',rejectAbort);}
}

export function useReplaySession(id:string,initialPeriod:Period='1m') {
  const [session,setSession] = useState<ReplaySession|null>(null);
  const [bars,setBars] = useState<Bar[]>([]);
  const [events,setEvents] = useState<ReplayEvents>({items:[],next_offset:null});
  const [period,setPeriod] = useState<Period>(initialPeriod);
  const [observedCursor,setObservedCursor] = useState<string|undefined>();
  const [playing,setPlaying] = useState(false);
  const [busy,setBusy] = useState(false);
  const [loading,setLoading] = useState(false);
  const [loadingSeconds,setLoadingSeconds] = useState(0);
  const [ready,setReady] = useState(false);
  const [historyExpanded,setHistoryExpanded] = useState(false);
  const [error,setError] = useState('');
  const [retryAvailable,setRetryAvailable] = useState(false);
  const generation = useRef(0);
  const request = useRef<AbortController|null>(null);
  const pending = useRef<(ReplayCommandInput & {operation_id:string;expected_revision:number})|null>(null);
  const sessionRef = useRef(session); sessionRef.current=session;
  const working = useRef(false);
  const periodRef = useRef(period); periodRef.current=period;
  const observerRef = useRef(observedCursor); observerRef.current=observedCursor;
  const readyRef=useRef(false);
  const historyLimit=useRef(120);
  useEffect(()=>{
    if(!loading)return;
    const started=Date.now(),timer=window.setInterval(()=>setLoadingSeconds(Math.floor((Date.now()-started)/1000)),1000);
    return()=>window.clearInterval(timer);
  },[loading]);

  const load = useCallback(async (signal:AbortSignal, token:number, current?:ReplaySession) => {
    const s = current ?? await getReplaySession(id,signal);
    if(token!==generation.current || signal.aborted) return;
    setSession(s);sessionRef.current=s;
    const [b, first] = await Promise.all([getReplayBars(id,periodRef.current,observerRef.current,signal,historyLimit.current),getReplayEvents(id,0,signal)]);
    const all = {...first,items:[...first.items]};
    let next=first.next_offset;
    while(next!==null) {
      if(token!==generation.current || signal.aborted) return;
      const page = await getReplayEvents(id,next,signal);
      all.items.push(...page.items); next=page.next_offset;
    }
    if(token!==generation.current || signal.aborted) return;
    setBars(b.bars);setEvents(all);setReady(true);readyRef.current=true;
  },[id]);

  const refresh = useCallback(async () => {
    if(pending.current)return;
    request.current?.abort();
    const controller=new AbortController();request.current=controller;
    const token=++generation.current;
    setBusy(true);setLoadingSeconds(0);setLoading(true);setReady(false);readyRef.current=false;working.current=true;setError('');
    try { await replayRequest(controller,90000,'回放行情或成交记录加载超时，请重试；也可退出后从「交易记录 → 回放交易」恢复。',()=>load(controller.signal,token)); }
    catch(e) { if(token===generation.current) {setError(errorText(e));setPlaying(false);} }
    finally { if(token===generation.current) {setBusy(false);setLoading(false);working.current=false;} }
  },[load]);

  function cancelLoad() {
    if(pending.current)return;
    generation.current++;request.current?.abort();working.current=false;
    setBusy(false);setLoading(false);setPlaying(false);setReady(false);readyRef.current=false;
    setError('已取消加载，可重试；未保存草稿可从「交易记录 → 回放交易」恢复。');
  }

  async function loadMoreHistory() {
    if(working.current||pending.current)return;
    historyLimit.current=500;setHistoryExpanded(true);await refresh();
  }

  useEffect(()=>{
    setPlaying(false);setSession(null);sessionRef.current=null;setBars([]);setEvents({items:[],next_offset:null});setObservedCursor(undefined);observerRef.current=undefined;pending.current=null;setRetryAvailable(false);setReady(false);readyRef.current=false;historyLimit.current=120;setHistoryExpanded(false);
    periodRef.current=initialPeriod;setPeriod(initialPeriod);
    if(id)void refresh();else {setBusy(false);setLoading(false);setError('');working.current=false;}
    return ()=>{generation.current++;request.current?.abort();working.current=false;};
  },[id,refresh,initialPeriod]);

  const command = useCallback(async (input?:ReplayCommandInput):Promise<boolean> => {
    const s=sessionRef.current;
    if(!s||working.current||(input&&!readyRef.current)) return false;
    if(input && pending.current) {setError('上次提交结果未知，请先重试确认');return false;}
    const payload=input ? {...input,operation_id:createTraceId(),expected_revision:s.revision} : pending.current;
    if(!payload) return false;
    pending.current=payload;
    working.current=true;setBusy(true);setError('');
    const controller=new AbortController();request.current=controller;
    const token=generation.current;
    try {
      const updated=await replayRequest(controller,150000,'提交超时，结果待确认，请重试确认。',()=>sendReplayCommand(id,payload,controller.signal));
      if(token!==generation.current||controller.signal.aborted) return false;
      pending.current=null;setRetryAvailable(false);sessionRef.current=updated;setSession(updated);
      setLoadingSeconds(0);setLoading(true);setReady(false);readyRef.current=false;
      await replayRequest(controller,90000,'回放行情或成交记录加载超时，请重试；订单提交已确认。',()=>load(controller.signal,token,updated));
      return true;
    } catch(e) {
      if(token===generation.current) {
        const code=(e as {code?:string}).code;
        if(code||Array.isArray((e as {detail?:unknown}).detail)) {pending.current=null;setRetryAvailable(false);}
        else setRetryAvailable(pending.current!==null);
        setError(errorText(e));setPlaying(false);
        if(code==='replay_revision_conflict') {
          setLoadingSeconds(0);setLoading(true);setReady(false);readyRef.current=false;
          try { await replayRequest(controller,90000,'回放数据加载超时，请重试。',()=>load(controller.signal,token)); } catch { /* Preserve initial conflict. */ }
        }
      }
      return false;
    } finally {if(token===generation.current){working.current=false;setBusy(false);setLoading(false);}}
  },[id,load]);

  useEffect(()=>{
    if(!playing||busy||!ready||!session||session.status!=='active'||observedCursor||retryAvailable)return;
    const timer=window.setTimeout(()=>void command({type:'advance',period}),1000);
    return ()=>window.clearTimeout(timer);
  },[playing,busy,ready,session,period,observedCursor,retryAvailable,command]);

  async function observe(cursor?:string) {
    if(working.current) return;
    setPlaying(false);observerRef.current=cursor;setObservedCursor(cursor);await refresh();
  }
  async function changePeriod(value:Period) {
    if(working.current)return;
    setPlaying(false);periodRef.current=value;setPeriod(value);await refresh();
  }
  return {session,bars,events,period,changePeriod,observedCursor,observe,playing,setPlaying,busy,loading,loadingSeconds,historyExpanded,error,retryAvailable,
    command,refresh,cancelLoad,loadMoreHistory,canTrade:ready&&!!session&&session.status==='active'&&!busy&&!observedCursor&&!retryAvailable};
}
