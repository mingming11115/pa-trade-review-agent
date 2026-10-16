import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SavedReplayList } from './SavedReplayList';
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('lists saved records and recoverable drafts separately',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify({items:url.includes('/legacy')?[]:url.includes('/drafts')?[{id:'draft',symbol:'ES',state:{cursor:'2025-01-01T00:00:00Z'}}]:[{id:'saved',name:'昨日练习',created_at:'2025-01-01T00:00:00Z',session:{symbol:'ES',state:{account:{realized_pnl:'10'}}}}]}))));
 const recover=vi.fn();
 render(<SavedReplayList onClose={()=>{}} onResume={recover}/>);
 await screen.findByText('昨日练习');
 expect(screen.getByText('可恢复草稿')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'恢复草稿'}));
 expect(recover).toHaveBeenCalledWith('draft','ES');
});

it('embeds saved records without a duplicate page heading while retaining draft recovery',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>Response.json({items:url.includes('/drafts')?[{id:'draft',symbol:'NQ',state:{cursor:'2025-01-01T00:00:00Z'}}]:[]})));
 const recover=vi.fn();
 render(<SavedReplayList embedded onClose={()=>{}} onResume={recover}/>);
 expect(screen.getByRole('region',{name:'回放记录'})).not.toHaveClass('replay-workspace');
 expect(screen.queryByRole('heading',{name:'回放记录'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'返回工作台'})).not.toBeInTheDocument();
 fireEvent.click(await screen.findByRole('button',{name:'恢复草稿'}));
 expect(recover).toHaveBeenCalledWith('draft','NQ');
});

it('ignores a resumed session that arrives after closing the record list',async()=>{
 let resolve:(r:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.endsWith('/resume'))return new Promise<Response>(r=>{resolve=r;});
  return Response.json({items:url.includes('/saved?')?[{id:'saved',name:'练习',created_at:'2025-01-01T00:00:00Z',session:{symbol:'ES',state:{account:{realized_pnl:'0'}}}}]:[]});
 }));
 const resume=vi.fn();
 const view=render(<SavedReplayList onClose={()=>{}} onResume={resume}/>);
 fireEvent.click(await screen.findByRole('button',{name:'继续回放'}));
 view.unmount();
 resolve(Response.json({id:'late',symbol:'ES'}));
 await new Promise(resolve=>setTimeout(resolve,0));
 expect(resume).not.toHaveBeenCalled();
});
