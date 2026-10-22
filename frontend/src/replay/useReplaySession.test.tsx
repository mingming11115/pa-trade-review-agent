import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useReplaySession } from './useReplaySession';

afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers();});
const session={id:'one',mode:'training',symbol:'ES',status:'active',revision:0,state:{cursor:'2025-01-01T00:00:00Z',orders:[]}};

it('loads all displayed periods at one explicit cursor and publishes one complete batch',async()=>{
 const requests:string[]=[];
 let finishHour:(response:Response)=>void=()=>{};
 let multi=false;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars')){
   requests.push(url);
   if(multi&&url.includes('period=1h'))return new Promise<Response>(resolve=>{finishHour=resolve;});
   const period=new URL(url,'http://localhost').searchParams.get('period')!;
   return Response.json({bars:[{timestamp:'2024-12-31T23:00:00Z',open:period==='1m'?1:5,high:6,low:1,close:5,volume:1}]});
  }
  return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 const {result}=renderHook(()=>useReplaySession('one'));
 await waitFor(()=>expect(result.current.canTrade).toBe(true));
 multi=true;
 act(()=>{void result.current.changeDisplayPeriods(['1m','5m','1h','5m']);});
 await waitFor(()=>expect(requests.some(url=>url.includes('period=1h'))).toBe(true));
 expect(result.current.barsByPeriod['5m']).toBeUndefined();
 expect(result.current.canTrade).toBe(false);
 await act(async()=>{finishHour(Response.json({bars:[]}));});
 expect(Object.keys(result.current.barsByPeriod).sort()).toEqual(['1h','1m','5m']);
 expect(result.current.canTrade).toBe(true);
 for(const url of requests){expect(new URL(url,'http://localhost').searchParams.get('cursor')).toBe(session.state.cursor);}
});

it('keeps the prior complete batch when a secondary period fails and pauses playback',async()=>{
 let fail=false;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars')){
   if(fail&&url.includes('period=5m'))throw new TypeError('secondary period unavailable');
   return Response.json({bars:[]});
  }
  return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 const {result}=renderHook(()=>useReplaySession('one'));
 await waitFor(()=>expect(result.current.canTrade).toBe(true));
 await act(async()=>{await result.current.changeDisplayPeriods(['1m','5m']);});
 const previous=result.current.barsByPeriod;
 act(()=>result.current.setPlaying(true));fail=true;
 await act(async()=>{await result.current.refresh();});
 expect(result.current.barsByPeriod).toBe(previous);
 expect(result.current.playing).toBe(false);
 expect(result.current.canTrade).toBe(false);
 expect(result.current.error).toContain('secondary period unavailable');
});

it('starts each refresh at zero seconds so a prior slow read cannot flash its notice',async()=>{
  vi.useFakeTimers();
  let resolveBars:(response:Response)=>void=()=>{};
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    if(url.includes('/bars'))return new Promise<Response>(resolve=>{resolveBars=resolve;});
    return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const displayedSeconds:number[]=[];
  const {result}=renderHook(()=>{
    const replay=useReplaySession('one');
    if(replay.loading)displayedSeconds.push(replay.loadingSeconds);
    return replay;
  });
  await act(async()=>{await vi.advanceTimersByTimeAsync(2100);});
  expect(result.current.loadingSeconds).toBe(2);
  await act(async()=>{resolveBars(Response.json({bars:[]}));});
  displayedSeconds.length=0;
  act(()=>{void result.current.command({type:'advance',period:'5m'});});
  await act(async()=>{});
  expect(displayedSeconds.length).toBeGreaterThan(0);
  expect(displayedSeconds.every(seconds=>seconds===0)).toBe(true);
  await act(async()=>{resolveBars(Response.json({bars:[]}));});
});

it('autoplays in chart-period candles and uses the new period after switching',async()=>{
  vi.useFakeTimers();
  const commands:Array<{type:string;period?:string}>=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST'){
      commands.push(JSON.parse(String(init.body)));
      return Response.json({...session,revision:commands.length});
    }
    return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const {result}=renderHook(()=>useReplaySession('one','5m'));
  await act(async()=>{await vi.advanceTimersByTimeAsync(0);});
  act(()=>result.current.setPlaying(true));
  await act(async()=>{await vi.advanceTimersByTimeAsync(1000);});
  expect(commands).toHaveLength(1);
  expect(commands[0]).toMatchObject({type:'advance',period:'5m'});
  await act(async()=>{await result.current.changePeriod('1h');});
  act(()=>result.current.setPlaying(true));
  await act(async()=>{await vi.advanceTimersByTimeAsync(1000);});
  expect(commands).toHaveLength(2);
  expect(commands[1]).toMatchObject({type:'advance',period:'1h'});
});

it('shows session metadata while the initial bounded chart request is pending, without allowing trades',async()=>{
  let resolveBars:(r:Response)=>void=()=>{};
  const urls:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    urls.push(url);
    if(url.includes('/bars'))return new Promise<Response>(resolve=>{resolveBars=resolve;});
    return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const {result}=renderHook(()=>useReplaySession('one','5m'));
  await waitFor(()=>expect(result.current.session?.id).toBe('one'));
  expect(result.current.canTrade).toBe(false);
  expect(result.current.loading).toBe(true);
  expect(urls.find(url=>url.includes('/bars'))).toContain('limit=120');
  await act(async()=>{resolveBars(Response.json({bars:[]}));});
  expect(result.current.canTrade).toBe(true);
});

it('cancels a slow read, ignores its late response, and can retry with more history',async()=>{
  let resolveOld:(r:Response)=>void=()=>{};
  let reads=0;
  const oldBar={timestamp:'2024-01-01T00:00:00Z',open:1,high:1,low:1,close:1,volume:1};
  const latestBar={...oldBar,timestamp:'2025-01-01T00:00:00Z'};
  const urls:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    urls.push(url);
    if(url.includes('/bars')){reads++;return reads===1?new Promise<Response>(resolve=>{resolveOld=resolve;}):Response.json({bars:[latestBar]});}
    return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await waitFor(()=>expect(result.current.session?.id).toBe('one'));
  act(()=>result.current.cancelLoad());
  expect(result.current.busy).toBe(false);
  expect(result.current.canTrade).toBe(false);
  await act(async()=>{await result.current.refresh();});
  await act(async()=>{resolveOld(Response.json({bars:[oldBar]}));});
  expect(result.current.bars).toEqual([latestBar]);
  expect(result.current.canTrade).toBe(true);
  await act(async()=>{await result.current.loadMoreHistory();});
  expect(urls.filter(url=>url.includes('/bars')).at(-1)).toContain('limit=500');
});

it('times out stalled reads and leaves trading disabled until a successful retry',async()=>{
  vi.useFakeTimers();
  let fail=true;
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    if(url.includes('/bars')&&fail)return new Promise<Response>(()=>{});
    return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await act(async()=>{await vi.advanceTimersByTimeAsync(91000);});
  expect(result.current.busy).toBe(false);
  expect(result.current.error).toContain('超时');
  expect(result.current.canTrade).toBe(false);
  fail=false;
  await act(async()=>{await result.current.refresh();});
  expect(result.current.canTrade).toBe(true);
});

it('retries uncertain submissions with the same operation id',async()=>{
  const commands:Array<{operation_id:string;period?:string}>=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST'){
      commands.push(JSON.parse(String(init.body)));
      if(commands.length===1)throw new TypeError('network lost');
      return new Response(JSON.stringify({...session,revision:1}));
    }
    return new Response(JSON.stringify(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session));
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await waitFor(()=>expect(result.current.session?.id).toBe('one'));
  await act(async()=>{await result.current.command({type:'advance',period:'5m'});});
  expect(result.current.retryAvailable).toBe(true);
  await act(async()=>{await result.current.changePeriod('1h');});
  await act(async()=>{await result.current.command();});
  expect(commands[0].operation_id).toBe(commands[1].operation_id);
  expect(commands[1].period).toBe('5m');
  expect(result.current.session?.revision).toBe(1);
});

it('keeps a timed out command pending despite cancelling reads and retries its original operation',async()=>{
  vi.useFakeTimers();
  const operations:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST'){
      operations.push(JSON.parse(String(init.body)).operation_id);
      if(operations.length===1)return new Promise<Response>(()=>{});
      return Response.json({...session,revision:1});
    }
    return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await act(async()=>{});
  let inFlight:Promise<boolean>;
  act(()=>{inFlight=result.current.command({type:'advance'});});
  await act(async()=>{await vi.advanceTimersByTimeAsync(150001);await inFlight;});
  expect(result.current.retryAvailable).toBe(true);
  act(()=>result.current.cancelLoad());
  expect(result.current.retryAvailable).toBe(true);
  expect(result.current.canTrade).toBe(false);
  await act(async()=>{await result.current.command();});
  expect(operations).toHaveLength(2);
  expect(operations[1]).toBe(operations[0]);
  expect(result.current.session?.revision).toBe(1);
  expect(result.current.canTrade).toBe(true);
});

it('does not label a confirmed command as unknown when only chart refresh fails',async()=>{
  let posted=false;
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST'){posted=true;return new Response(JSON.stringify({...session,revision:1}));}
    if(url.includes('/bars')&&posted)throw new TypeError('chart request failed');
    return new Response(JSON.stringify(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session));
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await waitFor(()=>expect(result.current.session?.id).toBe('one'));
  await act(async()=>{await result.current.command({type:'advance'});});
  expect(result.current.session?.revision).toBe(1);
  expect(result.current.retryAvailable).toBe(false);
  expect(result.current.canTrade).toBe(false);
});

it('keeps trading disabled if refreshing a revision conflict fails',async()=>{
  let posted=false;
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST'){posted=true;return Response.json({code:'replay_revision_conflict',message:'版本已更新'},{status:409});}
    if(url.includes('/bars')&&posted)throw new TypeError('chart request failed');
    return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await waitFor(()=>expect(result.current.canTrade).toBe(true));
  await act(async()=>{await result.current.command({type:'advance'});});
  expect(result.current.retryAvailable).toBe(false);
  expect(result.current.canTrade).toBe(false);
});

it('late response from a closed session cannot overwrite the selected session',async()=>{
  let resolveOld:(r:Response)=>void=()=>{};
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    if(url.endsWith('/one'))return new Promise<Response>(resolve=>{resolveOld=resolve;});
    return new Response(JSON.stringify(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:{...session,id:'two'}));
  }));
  const {result,rerender}=renderHook(({id})=>useReplaySession(id),{initialProps:{id:'one'}});
  rerender({id:'two'});
  await waitFor(()=>expect(result.current.session?.id).toBe('two'));
  await act(async()=>{resolveOld(new Response(JSON.stringify(session)));});
  expect(result.current.session?.id).toBe('two');
  expect(result.current.playing).toBe(false);
});

it('serializes advance while its network response is outstanding',async()=>{
  let resolveCommand:(r:Response)=>void=()=>{};
  let count=0;
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST'){count++;return new Promise<Response>(resolve=>{resolveCommand=resolve;});}
    return new Response(JSON.stringify(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session));
  }));
  const {result}=renderHook(()=>useReplaySession('one'));
  await waitFor(()=>expect(result.current.session?.id).toBe('one'));
  let inFlight:Promise<boolean>;
  await act(async()=>{inFlight=result.current.command({type:'advance'});});
  await act(async()=>{await result.current.command({type:'advance'});});
  expect(count).toBe(1);
  await act(async()=>{resolveCommand(new Response(JSON.stringify({...session,revision:1})));await inFlight;});
});

it('retains display changes made during a pending advance for its confirmed batch',async()=>{
 let finishCommand:(response:Response)=>void=()=>{};const periods:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
  if(options?.method==='POST')return new Promise<Response>(resolve=>{finishCommand=resolve;});
  if(url.includes('/bars')){periods.push(new URL(url,'http://localhost').searchParams.get('period')!);return Response.json({bars:[]});}
  return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 const {result}=renderHook(()=>useReplaySession('one'));
 await waitFor(()=>expect(result.current.canTrade).toBe(true));periods.length=0;
 act(()=>{void result.current.command({type:'advance',period:'1m'});});
 await act(async()=>{await result.current.changeDisplayPeriods(['1m','5m','15m','1h']);});
 await act(async()=>{finishCommand(Response.json({...session,revision:1}));});
 expect(periods.sort()).toEqual(['15m','1h','1m','5m']);
 expect(Object.keys(result.current.barsByPeriod)).toHaveLength(4);
});

it('drops the old timeframe batch when an external hours switch refresh fails',async()=>{
 let hours:'ETH'|'RTH'='ETH',fail=false;
 const requested:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars')){
   requested.push(new URL(url,'http://localhost').searchParams.get('trading_hours')!);
   if(fail)throw new Error('market read failed');
   return Response.json({bars:[{timestamp:'2025-01-01T00:00:00Z',open:1,high:2,low:1,close:2}]});
  }
  return Response.json(url.includes('/events')?{items:[],next_offset:null}:{...session,state:{...session.state,config:{trading_hours:hours}}});
 }));
 const {result}=renderHook(()=>useReplaySession('one'));
 await waitFor(()=>expect(result.current.canTrade).toBe(true));
 expect(result.current.bars).toHaveLength(1);
 hours='RTH';fail=true;
 await act(async()=>{await result.current.refresh();});
 expect(result.current.session!.state.config.trading_hours).toBe('RTH');
 expect(result.current.bars).toEqual([]);expect(result.current.barsByPeriod).toEqual({});
 expect(result.current.dataCursor).toBeUndefined();expect(result.current.canTrade).toBe(false);
 expect(requested).toEqual(['ETH','RTH']);
});
