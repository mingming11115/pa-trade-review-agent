import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_INDICATOR_SETTINGS, INDICATOR_PERIODS, isIndicatorSettings, isIndicatorVisible, loadIndicatorSettings, saveIndicatorSettings } from './indicatorSettings';
const legacy = () => ({ma:{enabled:true,period:50,color:'#123abc'},ema:{enabled:false,period:99,color:'#f59e0b'},boll:{enabled:true,period:30,multiplier:2.5,color:'#112233'},macd:{enabled:true,fast:10,slow:24,signal:8},rsi:{enabled:false,period:10,color:'#123abc'},volume:{enabled:false}});
beforeEach(() => { const values = new Map<string,string>(); vi.stubGlobal('localStorage',{getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v)}); });
afterEach(()=>vi.unstubAllGlobals());
describe('indicator settings persistence',()=>{
 it('migrates valid v1 selections and styles to independent v2 defaults',()=>{
  window.localStorage.setItem('pa.chart.indicators.v1',JSON.stringify(legacy()));
  const settings=loadIndicatorSettings();
  expect(settings.ma).toMatchObject({enabled:true,period:50,color:'#123abc',visible:true,lineWidth:2,timeframes:INDICATOR_PERIODS});
  expect(settings.boll).toMatchObject({multiplier:2.5,color:'#112233',upperColor:'#112233',lowerColor:'#112233'});
  expect(settings.ema.enabled).toBe(false);expect(settings.volume.enabled).toBe(false);
  settings.ma.timeframes.length=0; expect(loadIndicatorSettings().ema.timeframes).toEqual(INDICATOR_PERIODS);
 });
 it('saves v2 and prefers it over v1',()=>{
  window.localStorage.setItem('pa.chart.indicators.v1',JSON.stringify(legacy()));
  const settings=structuredClone(DEFAULT_INDICATOR_SETTINGS);settings.ma.visible=false;settings.ma.timeframes=['1m'];settings.macd.difColor='#ff0000';
  saveIndicatorSettings(settings); expect(JSON.parse(window.localStorage.getItem('pa.chart.indicators.v2')!)).toEqual(settings);expect(loadIndicatorSettings()).toEqual(settings);
 });
 it.each([{...DEFAULT_INDICATOR_SETTINGS,extra:true},{...DEFAULT_INDICATOR_SETTINGS,ma:{...DEFAULT_INDICATOR_SETTINGS.ma,lineWidth:5}},{...DEFAULT_INDICATOR_SETTINGS,ma:{...DEFAULT_INDICATOR_SETTINGS.ma,timeframes:['2m']}},{...DEFAULT_INDICATOR_SETTINGS,ma:{...DEFAULT_INDICATOR_SETTINGS.ma,timeframes:['1m','1m']}},{...DEFAULT_INDICATOR_SETTINGS,macd:{...DEFAULT_INDICATOR_SETTINGS.macd,fast:26}},{...DEFAULT_INDICATOR_SETTINGS,boll:{...DEFAULT_INDICATOR_SETTINGS.boll,multiplier:0}}])('rejects invalid v2 without silently accepting malformed settings',value=>{
  expect(isIndicatorSettings(value)).toBe(false);window.localStorage.setItem('pa.chart.indicators.v2',JSON.stringify(value));expect(loadIndicatorSettings()).toEqual(DEFAULT_INDICATOR_SETTINGS);
 });
 it('rejects malformed v1, handles corrupt JSON and unavailable storage',()=>{
  window.localStorage.setItem('pa.chart.indicators.v1',JSON.stringify({...legacy(),ma:{...legacy().ma,period:0}}));expect(loadIndicatorSettings()).toEqual(DEFAULT_INDICATOR_SETTINGS);
  window.localStorage.setItem('pa.chart.indicators.v2','{');expect(loadIndicatorSettings()).toEqual(DEFAULT_INDICATOR_SETTINGS);
  vi.stubGlobal('localStorage',{getItem:()=>{throw Error('denied');},setItem:()=>{throw Error('quota');}});expect(loadIndicatorSettings()).toEqual(DEFAULT_INDICATOR_SETTINGS);expect(()=>saveIndicatorSettings(DEFAULT_INDICATOR_SETTINGS)).not.toThrow();
 });
 it('filters known chart periods while accepting unspecified periods',()=>{
  const entry={...DEFAULT_INDICATOR_SETTINGS.ma,enabled:true,timeframes:['1m' as const]};
  expect(isIndicatorVisible(entry,'1m')).toBe(true);expect(isIndicatorVisible(entry,'1h')).toBe(false);expect(isIndicatorVisible({...entry,timeframes:[]},'')).toBe(true);expect(isIndicatorVisible({...entry,timeframes:[]})).toBe(true);expect(isIndicatorVisible({...entry,visible:false},'1m')).toBe(false);
 });
});
