import { expect, it, vi } from 'vitest';
import type { IChartApi, Time } from 'lightweight-charts';
import { createChartIndicators } from './chartIndicators';
import { DEFAULT_INDICATOR_SETTINGS } from './indicatorSettings';
import type { Bar } from './types';
function setup() {
 const records: Array<{pane:number;options:Record<string,unknown>;data:Array<{time:Time;value:number;color?:string}>;api:unknown;removed:boolean}>=[];
 const panes=[{setStretchFactor:vi.fn()}];
 const chart={addSeries(_kind:unknown,options:Record<string,unknown>,pane=0){if(!panes[pane])panes[pane]={setStretchFactor:vi.fn()};const record={pane,options,data:[] as Array<{time:Time;value:number;color?:string}>,api:{} as unknown,removed:false};record.api={setData(data:typeof record.data){record.data=data;},applyOptions(options:Record<string,unknown>){Object.assign(record.options,options);},getPane:()=>({paneIndex:()=>record.pane}),createPriceLine(){}};records.push(record);return record.api;},removeSeries(api:unknown){const record=records.find(item=>item.api===api)!;record.removed=true;if(record.pane&& !records.some(item=>!item.removed&&item.pane===record.pane)){panes.splice(record.pane,1);records.filter(item=>!item.removed&&item.pane>record.pane).forEach(item=>item.pane--);}},panes:()=>panes,priceScale:()=>({applyOptions(){}})};
 return {renderer:createChartIndicators(chart as unknown as IChartApi),records,panes};
}
const bars:Bar[]=[1,3,2,6,4,8].map((close,i)=>({timestamp:`2026-01-01T0${i}:00:00Z`,open:close-1,high:close+1,low:close-1,close,volume:i===5?null:i+1}));
const time=(i:number)=>Date.parse(bars[i].timestamp)/1000 as Time;
const settings=()=>{const value=structuredClone(DEFAULT_INDICATOR_SETTINGS);value.ma.enabled=true;value.ma.period=2;value.ema.period=2;value.macd.enabled=true;value.macd.fast=2;value.macd.slow=3;value.macd.signal=2;value.rsi.enabled=true;value.rsi.period=2;return value;};
it('reports exact crosshair values, warmup gaps, and missing latest volume without leaking earlier data',()=>{
 const {renderer}=setup();renderer.update(bars,settings());
 expect(renderer.getLegendRows(time(0)).find(row=>row.key==='ma')?.values[0].value).toBeNull();
 expect(renderer.getLegendRows(time(1)).find(row=>row.key==='ma')?.values[0].value).toBe(2);
 expect(renderer.getLegendRows().find(row=>row.key==='ma')?.values[0].value).toBe(6);
 expect(renderer.getLegendRows().find(row=>row.key==='volume')?.values[0].value).toBeNull();
 expect(renderer.getLegendRows(1 as Time).every(row=>row.values.every(item=>item.value===null))).toBe(true);
});
it('retains hidden and timeframe-filtered panes and reflects current styles',()=>{
 const {renderer,records,panes}=setup();const value=settings();value.macd.visible=false;value.rsi.timeframes=['1m'];value.ema.color='#123abc';value.ema.lineWidth=4;value.volume.upColor='#abcdef';
 renderer.update(bars,value,'1h');expect(panes).toHaveLength(3);expect(records.find(item=>item.options.title==='DIF')?.options.visible).toBe(false);expect(records.find(item=>item.options.title==='RSI2')?.options.visible).toBe(false);expect(records.find(item=>item.options.title==='EMA2')?.options).toMatchObject({color:'#123abc',lineWidth:4,visible:true});expect(records.find(item=>item.options.title==='VOL')?.data[0].color).toBe('#abcdef47');
 expect(renderer.getLegendRows().find(row=>row.key==='rsi')?.visible).toBe(false);
 value.macd.visible=true;renderer.update(bars,value,'1m');expect(renderer.getLegendRows().every(row=>row.visible)).toBe(true);
});
it('follows pane reindexing on deletion without losing the remaining indicator',()=>{
 const {renderer,panes}=setup();const value=settings();renderer.update(bars,value);expect(renderer.getLegendRows().find(row=>row.key==='rsi')?.paneIndex).toBe(2);
 value.macd.enabled=false;renderer.update(bars,value);expect(panes).toHaveLength(2);expect(renderer.getLegendRows().find(row=>row.key==='rsi')?.paneIndex).toBe(1);expect(renderer.getLegendRows().some(row=>row.key==='macd')).toBe(false);
});
it('replaces cached values on correction, replay truncation and empty data',()=>{
 const {renderer}=setup();renderer.update(bars,settings());renderer.update(bars.map((bar,i)=>i===1?{...bar,close:5}:bar),settings());expect(renderer.getLegendRows(time(1)).find(row=>row.key==='ma')?.values[0].value).toBe(3);
 renderer.update(bars.slice(0,2),settings());expect(renderer.getLegendRows(time(5)).every(row=>row.values.every(item=>item.value===null))).toBe(true);expect(renderer.getLegendRows().find(row=>row.key==='ma')?.values[0].value).toBe(2);
 renderer.update([],settings());expect(renderer.getLegendRows().every(row=>row.values.every(item=>item.value===null))).toBe(true);
});
it('applies separate Bollinger and MACD styles on existing series',()=>{
 const {renderer,records}=setup();const value=settings();value.boll.enabled=true;value.boll.period=2;renderer.update(bars,value);value.boll.upperColor='#ff0000';value.boll.lowerColor='#00ff00';value.macd.difColor='#aabbcc';value.macd.deaColor='#112233';value.macd.positiveColor='#123456';value.macd.lineWidth=3;renderer.update(bars,value);
 expect(records.find(item=>item.options.title==='BOLL 上轨')?.options.color).toBe('#ff0000');expect(records.find(item=>item.options.title==='BOLL 下轨')?.options.color).toBe('#00ff00');expect(records.find(item=>item.options.title==='DIF')?.options).toMatchObject({color:'#aabbcc',lineWidth:3});expect(records.find(item=>item.options.title==='DEA')?.options.color).toBe('#112233');expect(records.find(item=>item.options.title==='MACD')?.data.filter(item=>item.value>=0).every(item=>item.color==='#123456')).toBe(true);
});

it('matches volume legend color to the hovered candle direction',()=>{
 const {renderer}=setup();const value=settings();value.volume.upColor='#abcdef';value.volume.downColor='#123456';const bearish=bars.map((bar,i)=>i===1?{...bar,open:bar.close+1}:bar);renderer.update(bearish,value);
 expect(renderer.getLegendRows(time(1)).find(row=>row.key==='volume')?.values[0]).toMatchObject({value:2,color:'#123456'});
 expect(renderer.getLegendRows(time(0)).find(row=>row.key==='volume')?.values[0].color).toBe('#abcdef');
});
