import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useReplayTradingController } from './useReplayTradingController';
const drawings=vi.hoisted(()=>({copyDrawingScope:vi.fn()}));
vi.mock('../chartWorkspace/chartDrawings',()=>drawings);
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.clearAllMocks();});
it('copies a draft drawing snapshot to the returned saved version before discarding',async()=>{
 const session={id:'draft',symbol:'ES',status:'active',revision:3,state:{cursor:'2025-01-01T10:00:00Z',orders:[]}};
 const operations:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(init?.method==='POST')operations.push(url);
  if(url.endsWith('/save'))return Response.json({id:'saved-version'});
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 const {result}=renderHook(()=>useReplayTradingController('alice'));
 act(()=>result.current.attach('draft'));
 await waitFor(()=>expect(result.current.replay.canTrade).toBe(true));
 await act(async()=>{await result.current.saveAndLeave('my replay');});
 expect(drawings.copyDrawingScope).toHaveBeenCalledExactlyOnceWith('alice:ES:replay:draft','alice:ES:saved:saved-version');
 expect(operations).toHaveLength(2);
 expect(result.current.id).toBe('');
});
