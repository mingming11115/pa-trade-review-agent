import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChartWorkspace } from './ChartWorkspace';
import type { Bar, Period } from '../types';

const api=vi.hoisted(()=>({getMarketBars:vi.fn()}));
vi.mock('../api',()=>api);
vi.mock('../TradingChart',()=>({TradingChart:({period,bars}:{period:Period;bars:Bar[]})=><div data-testid={`bars-${period}`}>{bars.map(b=>b.close).join(',')}</div>}));
vi.mock('./chartSync',()=>({createChartSync:()=>({register:()=>()=>{},update(){},setOptions(){},dispose(){}})}));
const bar:Bar={timestamp:'2025-01-01T10:00:00Z',open:100,high:101,low:99,close:100,volume:10};
beforeEach(()=>{
 const store=new Map<string,string>();
 vi.stubGlobal('localStorage',{getItem:(key:string)=>store.get(key)??null,setItem:(key:string,value:string)=>store.set(key,value)});
 api.getMarketBars.mockResolvedValue({bars:[bar]});
});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();});

it('shows four periods for one symbol and keeps layout after remounting',async()=>{
 const props={symbol:'ES',userScope:'tester',bars:[bar],period:'1m' as const,chartKey:'live'};
 const view=render(<ChartWorkspace {...props}/>);
 expect(screen.getAllByRole('region',{name:/ES · .*图表/})).toHaveLength(4);
 await waitFor(()=>expect(screen.getByTestId('bars-1h')).toHaveTextContent('100'));
 fireEvent.click(screen.getByRole('button',{name:'双图'}));
 expect(screen.getAllByRole('region',{name:/ES · .*图表/})).toHaveLength(2);
 view.unmount();render(<ChartWorkspace {...props}/>);
 expect(screen.getByRole('button',{name:'双图'})).toHaveAttribute('aria-pressed','true');
 expect(screen.getAllByRole('region',{name:/ES · .*图表/})).toHaveLength(2);
});

it('loads secondary periods from their own timeframe at a shared cutoff',async()=>{
 render(<ChartWorkspace symbol="ES" bars={[bar]} period="1m" chartKey="normal"/>);
 await waitFor(()=>expect(api.getMarketBars).toHaveBeenCalledTimes(3));
 const calls=api.getMarketBars.mock.calls;
 expect(calls.map(c=>c[1]).sort()).toEqual(['15m','1h','5m']);
 expect(new Set(calls.map(c=>c[3]))).toEqual(new Set(['2025-01-01T10:01:00.000Z']));
 expect(calls.find(c=>c[1]==='1h')![2]).not.toBe(calls.find(c=>c[1]==='5m')![2]);
});

it('uses the replay batch without additional market requests and reports displayed periods once',async()=>{
 const changeDisplayPeriods=vi.fn(async()=>{}),onPause=vi.fn();
 const replay={id:'session',cursor:'2025-01-01T10:01:00Z',dataCursor:'2025-01-01T10:01:00Z',barsByPeriod:{'1m':[bar],'5m':[bar],'15m':[bar],'1h':[bar]},loading:false,changeDisplayPeriods,onPause};
 render(<ChartWorkspace symbol="ES" bars={[bar]} period="1m" chartKey="replay:session" replay={replay}/>);
 await waitFor(()=>expect(changeDisplayPeriods).toHaveBeenCalledExactlyOnceWith(['1m','5m','15m','1h']));
 expect(api.getMarketBars).not.toHaveBeenCalled();
 expect(screen.getByTestId('bars-1h')).toHaveTextContent('100');
 fireEvent.change(screen.getByLabelText('图表 2 周期'),{target:{value:'30m'}});
 await waitFor(()=>expect(changeDisplayPeriods).toHaveBeenLastCalledWith(['1m','30m','15m','1h']));
 expect(onPause).toHaveBeenCalled();
});

it('ignores an old symbol response after a switch',async()=>{
 const oldResolvers:Array<(v:{bars:Bar[]})=>void>=[];
 api.getMarketBars.mockImplementation((symbol:string)=>symbol==='ES'?new Promise(resolve=>oldResolvers.push(resolve)):Promise.resolve({bars:[{...bar,close:200}]}));
 const view=render(<ChartWorkspace symbol="ES" bars={[bar]} period="1m" chartKey="ES"/>);
 view.rerender(<ChartWorkspace symbol="NQ" bars={[{...bar,close:200}]} period="1m" chartKey="NQ"/>);
 await waitFor(()=>expect(screen.getByTestId('bars-1h')).toHaveTextContent('200'));
 await act(async()=>{oldResolvers.forEach(resolve=>resolve({bars:[{...bar,close:1}]}));});
 expect(screen.getByTestId('bars-1h')).toHaveTextContent('200');
});

it('notifies the parent of a restored active timeframe without another click',()=>{
 localStorage.setItem('pa.chart.workspace.v1:restored',JSON.stringify({layout:4,active:3,periods:['1m','5m','15m','1h'],crosshair:true,time:true}));
 const onPeriodChange=vi.fn();
 render(<ChartWorkspace symbol="ES" userScope="restored" bars={[bar]} period="1m" chartKey="restore" onPeriodChange={onPeriodChange}/>);
 expect(onPeriodChange).toHaveBeenCalledWith('1h');
});

it('honors later parent period changes instead of restoring over the top-level selector',()=>{
 const onPeriodChange=vi.fn();
 const props={symbol:'ES',bars:[bar],period:'1m' as const,chartKey:'top',onPeriodChange};
 const view=render(<ChartWorkspace {...props}/>);
 view.rerender(<ChartWorkspace {...props} period="5m"/>);
 expect(screen.getByRole('button',{name:'选中图表 2'})).toHaveAttribute('aria-pressed','true');
 expect(onPeriodChange).not.toHaveBeenCalled();
});
