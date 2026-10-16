import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useReplayTradingController } from './useReplayTradingController';
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers();});
it('shows elapsed creation time and explains that a timeout may have left a recoverable draft',async()=>{
 vi.useFakeTimers();
 let resolve:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(r=>{resolve=r;})));
 const {result}=renderHook(()=>useReplayTradingController());
 let pending:Promise<void>;
 act(()=>{pending=result.current.start({mode:'training',symbol:'ES',start:'2025-01-01T00:00:00Z'});});
 await act(async()=>{await vi.advanceTimersByTimeAsync(12000);});
 expect(result.current.startingSeconds).toBe(12);
 await act(async()=>{await vi.advanceTimersByTimeAsync(49000);await pending;});
 expect(result.current.busy).toBe(false);
 expect(result.current.error).toContain('超时');
 expect(result.current.error).toContain('交易记录');
 await act(async()=>{resolve(Response.json({id:'late'}));});
 expect(result.current.id).toBe('');
});

it('can save and exit while chart reads are still pending',async()=>{
 const session={id:'draft',mode:'training',symbol:'ES',status:'active',revision:2,state:{cursor:'2025-01-01T00:00:00Z',orders:[]}};
 const saved:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars'))return new Promise<Response>(()=>{});
  if(url.endsWith('/save'))saved.push('saved');
  return Response.json(url.includes('/events')?{items:[],next_offset:null}:url.endsWith('/discard')?{ok:true}:session);
 }));
 const {result}=renderHook(()=>useReplayTradingController());
 act(()=>result.current.attach('draft'));
 await waitFor(()=>expect(result.current.replay.session?.id).toBe('draft'));
 act(()=>result.current.requestLeave());
 expect(result.current.replay.busy).toBe(false);
 await act(async()=>{await result.current.saveAndLeave('pending chart');});
 expect(saved).toEqual(['saved']);
 expect(result.current.id).toBe('');
});
it('asks before leaving, keeps failed save, and discards only current draft',async()=>{
 const calls:string[]=[];
 const session={id:'draft',mode:'training',symbol:'ES',status:'active',revision:2,state:{cursor:'2025-01-01T00:00:00Z',orders:[]}};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  calls.push(url);
  if(url.endsWith('/save'))return new Response(JSON.stringify({message:'保存失败'}),{status:500});
  return new Response(JSON.stringify(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:url.endsWith('/discard')?{ok:true}:session));
 }));
 const {result}=renderHook(()=>useReplayTradingController());
 act(()=>result.current.attach('draft'));
 await waitFor(()=>expect(result.current.replay.session?.id).toBe('draft'));
 const leave=vi.fn();
 act(()=>result.current.requestLeave(leave));
 expect(leave).not.toHaveBeenCalled();
 expect(result.current.exitOpen).toBe(true);
 await act(async()=>{await result.current.saveAndLeave('练习');});
 expect(leave).not.toHaveBeenCalled();
 expect(result.current.error).toContain('保存失败');
 act(()=>result.current.cancelLeave());
 expect(result.current.id).toBe('draft');
 act(()=>result.current.requestLeave(leave));
 await act(async()=>{await result.current.discardAndLeave();});
 expect(leave).toHaveBeenCalledOnce();
 expect(result.current.id).toBe('');
 expect(calls.filter(x=>x.endsWith('/discard'))).toHaveLength(1);
});

it.each([false,true])('saves latest state after a failed save and continued replay (server committed=%s)',async(committed)=>{
 let revision=2,attempts=0;
 const saves:Array<{name:string;expected_revision:number;operation_id:string}>=[];
 const session=()=>({id:'draft',mode:'training',symbol:'ES',status:'active',revision,state:{cursor:'2025-01-01T00:00:00Z',orders:[]}});
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.endsWith('/save')){const payload=JSON.parse(String(init?.body));saves.push(payload);attempts++;if(attempts===1)throw new TypeError('connection lost');if(payload.expected_revision!==revision&&!committed)return Response.json({code:'replay_revision_conflict',message:'已更新'},{status:409});return Response.json({id:'saved'});}
  if(url.endsWith('/commands')){revision++;return Response.json(session());}
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:url.endsWith('/discard')?{ok:true}:session());
 }));
 const {result}=renderHook(()=>useReplayTradingController());
 act(()=>result.current.attach('draft'));
 await waitFor(()=>expect(result.current.replay.session?.revision).toBe(2));
 act(()=>result.current.requestLeave());
 await act(async()=>{await result.current.saveAndLeave('first');});
 act(()=>result.current.cancelLeave());
 await act(async()=>{await result.current.replay.command({type:'advance'});});
 act(()=>result.current.requestLeave());
 await act(async()=>{await result.current.saveAndLeave('latest');});
 expect(saves[1]).toMatchObject({name:'latest',expected_revision:3});
 expect(saves[1].operation_id).not.toBe(saves[0].operation_id);
 expect(result.current.id).toBe('');
});

it('does not attach a late creation after leaving the original chart',async()=>{
 let resolve:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(r=>{resolve=r;})));
 const {result}=renderHook(()=>useReplayTradingController());
 let pending:Promise<void>;
 act(()=>{pending=result.current.start({mode:'training',symbol:'ES',start:'2025-01-01T00:00:00Z'});});
 const leave=vi.fn();
 act(()=>result.current.requestLeave(leave));
 await act(async()=>{resolve(Response.json({id:'late'}));await pending;});
 expect(leave).toHaveBeenCalledOnce();
 expect(result.current.id).toBe('');
});

it('keeps the selected chart timeframe when entering replay',async()=>{
 const urls:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{urls.push(url);return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:{id:'new',status:'active',revision:0,state:{cursor:'2025-01-01T00:00:00Z',orders:[]}});}));
 const {result}=renderHook(()=>useReplayTradingController());
 await act(async()=>{await result.current.start({mode:'training',symbol:'ES',start:'2025-01-01T00:00:00Z'},'15m');});
 await waitFor(()=>expect(result.current.replay.session?.id).toBe('new'));
 expect(result.current.replay.period).toBe('15m');
 expect(urls.some(url=>url.includes('/bars?period=15m'))).toBe(true);
});
