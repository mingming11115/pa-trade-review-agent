import { useEffect, useRef, useState } from 'react';
import { createReplaySession, createTraceId, discardReplay, saveReplay } from '../api';
import type { Period, ReplayCreate } from '../types';
import { errorText, replayRequest, useReplaySession } from './useReplaySession';

export function useReplayTradingController() {
 const [id,setId]=useState('');
 const [initialPeriod,setInitialPeriod]=useState<Period>('1m');
 const replay=useReplaySession(id,initialPeriod);
 const [exitOpen,setExitOpen]=useState(false);
 const [busy,setBusy]=useState(false);
 const [starting,setStarting]=useState(false);
 const [startingSeconds,setStartingSeconds]=useState(0);
 const [error,setError]=useState('');
 const next=useRef<()=>void>(()=>{});
 const saving=useRef(false);
 const generation=useRef(0);
 const creation=useRef<AbortController|null>(null);
 useEffect(()=>{
  if(!starting)return;
  setStartingSeconds(0);
  const started=Date.now(),timer=window.setInterval(()=>setStartingSeconds(Math.floor((Date.now()-started)/1000)),1000);
  return()=>window.clearInterval(timer);
 },[starting]);
 useEffect(()=>()=>{generation.current++;creation.current?.abort();},[]);
 const saveAttempt=useRef<{id:string;name:string;revision:number;operation:string}|null>(null);
 useEffect(()=>{
  if(!id)return;
  const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
  window.addEventListener('beforeunload',warn);
  return()=>window.removeEventListener('beforeunload',warn);
 },[id]);
 function attach(value:string){setId(value);setError('');saveAttempt.current=null;}
 async function start(payload:ReplayCreate,period:Period='1m'){
  setInitialPeriod(period);
  if(saving.current)return;
  saving.current=true;setBusy(true);setStarting(true);setError('');
  const token=++generation.current,abort=new AbortController();creation.current=abort;
  try {const created=await replayRequest(abort,60000,'创建回放超时，服务端可能已生成草稿，请先到「交易记录 → 回放交易」查看并恢复。',()=>createReplaySession(payload,abort.signal));if(token===generation.current&&!abort.signal.aborted)attach(created.id);}
  catch(e){if(token===generation.current)setError(errorText(e));}
  finally{if(token===generation.current){saving.current=false;setBusy(false);setStarting(false);creation.current=null;}}
 }
 function requestLeave(action:()=>void=()=>{}){
  if(!id){generation.current++;creation.current?.abort();creation.current=null;saving.current=false;setBusy(false);setStarting(false);if(starting)setError('已取消等待；服务端可能已生成草稿，可到「交易记录 → 回放交易」查看。');action();return;}
  if(replay.loading)replay.cancelLoad();
  if(!replay.session){setId('');setError('已退出加载，草稿可从「交易记录 → 回放交易」恢复。');action();return;}
  replay.setPlaying(false);next.current=action;setExitOpen(true);setError('');
 }
 function cancelLeave(){if(!saving.current){setExitOpen(false);setError('');}}
 function complete(){setId('');setExitOpen(false);setError('');saveAttempt.current=null;const action=next.current;next.current=()=>{};action();}
 async function leave(name?:string){
  if(saving.current||replay.busy||replay.retryAvailable||!replay.session)return;
  saving.current=true;setBusy(true);setError('');
  const s=replay.session;
  try {
   if(name!==undefined){
    const previous=saveAttempt.current;
    const saveName=name.trim()||`${s.symbol} 回放`;
    if(!previous||previous.id!==s.id||previous.revision!==s.revision||previous.name!==saveName)saveAttempt.current={id:s.id,name:saveName,revision:s.revision,operation:createTraceId()};
    const a=saveAttempt.current!;
    await saveReplay(a.id,a.name,a.operation,a.revision);
   }
   await discardReplay(s.id,s.revision);
   complete();
  }catch(e){setError(errorText(e));}
  finally{saving.current=false;setBusy(false);}
 }
 return {id,replay,exitOpen,busy,startingSeconds,error,attach,start,requestLeave,cancelLeave,
  saveAndLeave:(name:string)=>leave(name),discardAndLeave:()=>leave()};
}
export type ReplayTradingController=ReturnType<typeof useReplayTradingController>;
