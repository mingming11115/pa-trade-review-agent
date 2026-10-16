import { cleanup,fireEvent,render,screen,waitFor } from '@testing-library/react';
import { afterEach,expect,it,vi } from 'vitest';
import { SavedReplayDetail } from './SavedReplayDetail';
import type { SavedReplay } from '../types';
vi.mock('../TradingChart',()=>({TradingChart:()=> <div>图表</div>}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('starts analysis on the saved version and opens its completed result',async()=>{
 const requests:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(init?.method==='POST'){requests.push(url);return Response.json({task_id:'task',runs:[{run_id:'run',period:'5m',status:'queued'}]});}
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/analysis-runs/')?{run_id:'run',status:'completed',result:{}}:{items:[]});
 }));
 const value={id:'saved',name:'练习',events:[],session:{id:'s',state:{cursor:'2025-01-01T00:00:00Z',orders:[],position:{quantity:1},account:{closed_trades:0,wins:0}}}} as unknown as SavedReplay;
 const result=vi.fn();
 render(<SavedReplayDetail value={value} onResume={()=>{}} onResult={result}/>);
 fireEvent.click(screen.getByRole('button',{name:'分析'}));
 await waitFor(()=>expect(requests).toEqual(['/api/v1/replay/saved/saved/analysis']));
 fireEvent.click(await screen.findByRole('button',{name:'查看分析结果'}));
 expect(result).toHaveBeenCalledWith('task','run');
});
