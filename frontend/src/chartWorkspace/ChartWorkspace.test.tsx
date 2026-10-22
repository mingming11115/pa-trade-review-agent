import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChartWorkspace } from './ChartWorkspace';
import type { Bar, Period } from '../types';

const api=vi.hoisted(()=>({getMarketBars:vi.fn()}));
vi.mock('../api',()=>api);
vi.mock('../TradingChart',()=>({TradingChart:({period,bars,onNeedHistory}:{period:Period;bars:Bar[];onNeedHistory?:(start:string,end:string)=>void|Promise<void>})=><div data-testid={`bars-${period}`}>{bars.map(b=>b.close).join(',')}{onNeedHistory&&<button onClick={()=>void onNeedHistory('2025-01-01T08:00:00Z','2025-01-01T10:00:00Z')}>history-{period}</button>}</div>}));
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

it('applies the top-level period to the selected slot even if another slot already uses it',()=>{
 const onPeriodChange=vi.fn();
 const props={symbol:'ES',bars:[bar],period:'1m' as const,chartKey:'top',onPeriodChange};
 const view=render(<ChartWorkspace {...props}/>);
 view.rerender(<ChartWorkspace {...props} period="5m"/>);
 expect(screen.getByRole('button',{name:'选中图表 1'})).toHaveAttribute('aria-pressed','true');
 expect(screen.getByLabelText('图表 1 周期')).toHaveValue('5m');
 expect(screen.getByLabelText('图表 2 周期')).toHaveValue('5m');
 expect(onPeriodChange).not.toHaveBeenCalled();
});

it('loads all supplemental timeframes using the selected RTH session',async()=>{
 const regular={...bar,timestamp:'2026-07-06T13:30:00Z',trading_hours:'RTH' as const};
 render(<ChartWorkspace symbol="ES" bars={[regular]} period="1m" tradingHours="RTH" chartKey="rth"/>);
 await waitFor(()=>expect(api.getMarketBars).toHaveBeenCalledTimes(3));
 expect(api.getMarketBars.mock.calls.every(call=>call[7]==='RTH')).toBe(true);
});

it('loads older candles only for the selected secondary pane without reloading the other periods',async()=>{
 const source=[bar];
 render(<ChartWorkspace symbol="ES" bars={source} sourcePeriod="1m" period="5m" chartKey="stable"/>);
 await waitFor(()=>expect(screen.getByTestId('bars-1h')).toHaveTextContent('100'));
 api.getMarketBars.mockClear();
 api.getMarketBars.mockResolvedValue({bars:[{...bar,timestamp:'2025-01-01T09:55:00Z',close:90}]});
 fireEvent.click(screen.getByRole('button',{name:'history-5m'}));
 await waitFor(()=>expect(screen.getByTestId('bars-5m')).toHaveTextContent('90,100'));
 expect(api.getMarketBars).toHaveBeenCalledTimes(1);
 expect(api.getMarketBars.mock.calls[0][1]).toBe('5m');
 expect(screen.getByTestId('bars-1h')).toHaveTextContent('100');
 expect(screen.getByTestId('bars-1m')).toHaveTextContent('100');
});

it('keeps the two chart slots in place when selecting either pane without reloading',async()=>{
 render(<ChartWorkspace symbol="ES" bars={[bar]} sourcePeriod="1m" period="1m" chartKey="fixed-double"/>);
 await waitFor(()=>expect(screen.getByTestId('bars-1h')).toHaveTextContent('100'));
 fireEvent.click(screen.getByRole('button',{name:'双图'}));
 await waitFor(()=>expect(screen.queryByText('正在同步行情…')).not.toBeInTheDocument());
 const panes=screen.getAllByRole('region');
 const calls=api.getMarketBars.mock.calls.length;
 for(const index of [2,1,2]){
  fireEvent.pointerDown(screen.getByRole('button',{name:`选中图表 ${index}`}));
  expect(screen.getAllByRole('region')).toEqual(panes);
  expect(screen.getAllByRole('region').map(pane=>pane.getAttribute('aria-label'))).toEqual(['ES · 1m 图表','ES · 5m 图表']);
  expect(screen.getByRole('button',{name:`选中图表 ${index}`})).toHaveAttribute('aria-pressed','true');
 }
 expect(api.getMarketBars).toHaveBeenCalledTimes(calls);
});

it('selects a visible fixed slot when switching from the fourth pane to two charts',async()=>{
 const onPeriodChange=vi.fn();
 render(<ChartWorkspace symbol="ES" bars={[bar]} sourcePeriod="1m" period="1m" chartKey="layout-four-to-two" onPeriodChange={onPeriodChange}/>);
 await waitFor(()=>expect(screen.getByTestId('bars-1h')).toHaveTextContent('100'));
 fireEvent.click(screen.getByRole('button',{name:'选中图表 4'}));
 onPeriodChange.mockClear();
 fireEvent.click(screen.getByRole('button',{name:'双图'}));
 expect(screen.getByRole('button',{name:'选中图表 1'})).toHaveAttribute('aria-pressed','true');
 expect(onPeriodChange).toHaveBeenCalledWith('1m');
});

it('exposes only the two fixed slots in narrow dual layout and preserves selection on widening',async()=>{
 let resize!:(entries:Array<{contentRect:{width:number}}>)=>void;
 vi.stubGlobal('ResizeObserver',class{constructor(callback:typeof resize){resize=callback;}observe(){}disconnect(){}});
 render(<ChartWorkspace symbol="ES" bars={[bar]} sourcePeriod="1m" period="1m" chartKey="responsive-dual"/>);
 fireEvent.click(screen.getByRole('button',{name:'双图'}));
 act(()=>resize([{contentRect:{width:500}}]));
 const tabs=screen.getByRole('group',{name:'选择活动周期'});
 expect(tabs.querySelectorAll('button')).toHaveLength(2);
 fireEvent.click(tabs.querySelectorAll('button')[1]);
 act(()=>resize([{contentRect:{width:900}}]));
 expect(screen.getByRole('button',{name:'选中图表 2'})).toHaveAttribute('aria-pressed','true');
 expect(screen.getAllByRole('region')).toHaveLength(2);
});
