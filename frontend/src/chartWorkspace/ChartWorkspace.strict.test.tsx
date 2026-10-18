import { StrictMode } from 'react';
import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ChartOverlayContext } from '../chartOverlay';
import { ChartWorkspace } from './ChartWorkspace';

const charts=vi.hoisted(()=>new Map<string,{context:ChartOverlayContext;emit:(time:number)=>void;set:ReturnType<typeof vi.fn>}>());
vi.mock('../api',()=>({getMarketBars:async()=>({bars:[{timestamp:'2025-01-01T10:00:00Z',open:1,high:1,low:1,close:1,volume:1}]})}));
vi.mock('./ChartDrawingTools',()=>({ChartDrawingTools:()=>null}));
vi.mock('../TradingChart',()=>({TradingChart:({period,renderOverlay}:{period:string;renderOverlay:(context:ChartOverlayContext)=>unknown})=>{
 if(!charts.has(period)){
  const listeners=new Set<(event:unknown)=>void>();
  const set=vi.fn();const scale={subscribeVisibleLogicalRangeChange(){},unsubscribeVisibleLogicalRangeChange(){}};
  const context={chart:{subscribeCrosshairMove:(fn:(event:unknown)=>void)=>listeners.add(fn),unsubscribeCrosshairMove:(fn:(event:unknown)=>void)=>listeners.delete(fn),timeScale:()=>scale,setCrosshairPosition:set,clearCrosshairPosition:vi.fn()},series:{}} as unknown as ChartOverlayContext;
  charts.set(period,{context,set,emit:time=>listeners.forEach(fn=>fn({time}))});
 }
 return renderOverlay(charts.get(period)!.context);
}}));
afterEach(()=>{cleanup();charts.clear();vi.unstubAllGlobals();});
it('keeps crosshair subscriptions working after StrictMode effect replay',async()=>{
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem(){}});
 const bar={timestamp:'2025-01-01T10:00:00Z',open:1,high:1,low:1,close:1,volume:1};
 render(<StrictMode><ChartWorkspace symbol="ES" period="1m" chartKey="strict" bars={[bar]}/></StrictMode>);
 await waitFor(()=>{charts.get('1m')!.emit(Date.parse(bar.timestamp)/1000);expect(charts.get('5m')!.set).toHaveBeenCalled();});
 expect(charts.get('1h')!.set).toHaveBeenCalledWith(1,Date.parse(bar.timestamp)/1000,charts.get('1h')!.context.series);
});
