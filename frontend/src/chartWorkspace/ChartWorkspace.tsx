import { useEffect, useMemo, useRef, useState, type ComponentProps, type ReactNode } from 'react';
import { TradingChart } from '../TradingChart';
import { getMarketBars } from '../api';
import type { ChartOverlayContext } from '../chartOverlay';
import { barCloseAtMs } from '../tradingHours';
import { loadHistoryBars, mergeBars, PERIOD_MINUTES } from '../feed';
import { INDICATOR_PERIODS } from '../indicatorSettings';
import { mapReplayMarkers } from '../replay/replayActions';
import type { Bar, Period } from '../types';
import { createChartSync } from './chartSync';
import { ChartDrawingTools } from './ChartDrawingTools';
import './chartWorkspace.css';

export interface WorkspaceReplay {
  id: string;
  cursor: string;
  dataCursor?: string;
  barsByPeriod: Partial<Record<Period, Bar[]>>;
  loading: boolean;
  disabled?: boolean;
  changeDisplayPeriods(periods: Period[]): Promise<void>;
  onPause(): void;
}
type ChartProps = ComponentProps<typeof TradingChart>;
type Props = ChartProps & { sourcePeriod?: Period | ''; symbol: string; userScope?: string; replay?: WorkspaceReplay; onDrawingInteractionChange?:(editing:boolean)=>void };
type Settings = { layout: 1 | 2 | 4; active: number; periods: Period[]; crosshair: boolean; time: boolean };
const defaults = (): Settings => ({ layout: 4, active: 0, periods: ['1m','5m','15m','1h'], crosshair: true, time: true });
function settingsFor(scope: string, period?: Period | ''): Settings {
  try {
    const value = JSON.parse(localStorage.getItem(`pa.chart.workspace.v1:${scope}`) ?? 'null') as Settings | null;
    if (value && [1,2,4].includes(value.layout) && Number.isInteger(value.active) && value.active >= 0 && value.active < 4
      && value.periods?.length === 4 && value.periods.every(p => INDICATOR_PERIODS.includes(p))
      && typeof value.crosshair === 'boolean' && typeof value.time === 'boolean') return value.layout===2&&value.active>1?{...value,active:0}:value;
  } catch { /* Restricted or old storage uses safe defaults. */ }
  const value = defaults();
  if (period) {
    const index = value.periods.indexOf(period);
    if (index >= 0) value.active = index;
    else value.periods[0] = period;
  }
  return value;
}

function PaneOverlay({ context, sync, id, period, bars, children }: {
  context: ChartOverlayContext; sync: ReturnType<typeof createChartSync>; id: string; period: Period; bars: Bar[]; children?: ReactNode;
}) {
  const latest = useRef({ period, bars });latest.current = { period, bars };
  useEffect(() => sync.register(id, context, latest.current.period, latest.current.bars), [sync,id,context]);
  useEffect(() => sync.update(id,period,bars),[sync,id,period,bars]);
  return <>{children}</>;
}

export function ChartWorkspace(props: Props) {
  return <WorkspaceInner key={props.userScope ?? 'local'} {...props} />;
}

function WorkspaceInner({symbol,userScope='local',sourcePeriod,replay,onDrawingInteractionChange,...chart}:Props) {
  const [settings,setSettings]=useState(()=>settingsFor(userScope,chart.period));
  const [narrow,setNarrow]=useState(false);
  const [toolbarContainer,setToolbarContainer]=useState<HTMLDivElement|null>(null);
  const [replayPanelContainer,setReplayPanelContainer]=useState<HTMLDivElement|null>(null);
  const [drawingContainer,setDrawingContainer]=useState<HTMLDivElement|null>(null);
  const root=useRef<HTMLDivElement>(null);
  const sync=useMemo(()=>createChartSync(),[]);
  const batchController=useRef<AbortController|null>(null);
  const [batch,setBatch]=useState<{identity:string;bars:Partial<Record<Period,Bar[]>>}>({identity:'',bars:{}});
  const [loading,setLoading]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const identity=`${symbol}:${chart.chartKey}:${chart.tradingHours??'ETH'}`;
  const visible=narrow||settings.layout===1?[settings.active]:settings.layout===2?[0,1]:[0,1,2,3];
  const periodKey=[...new Set(visible.map(i=>settings.periods[i]))].join(',');
  const periods=useMemo(()=>periodKey.split(',') as Period[],[periodKey]);
  const primaryPeriod=sourcePeriod||chart.period||'1m';
  const selectedPeriod=chart.period||'1m';

  useEffect(()=>{try{localStorage.setItem(`pa.chart.workspace.v1:${userScope}`,JSON.stringify(settings));}catch{/* Layout still works in memory. */}},[settings,userScope]);
  useEffect(()=>{sync.setOptions({crosshair:settings.crosshair,time:settings.time});},[sync,settings.crosshair,settings.time]);
  // Each PaneOverlay unregisters its listeners/RAF. Keep the memoized coordinator
  // reusable during React StrictMode's setup/cleanup/setup replay.
  const restoredPeriod=useRef(false);
  const activePeriod=settings.periods[settings.active];
  useEffect(()=>{
    if(replay)return;
    if(!restoredPeriod.current){
      restoredPeriod.current=true;
      if(activePeriod!==selectedPeriod)chart.onPeriodChange?.(activePeriod);
      return;
    }
    // A later parent change comes from the existing top-level period selector.
    setSettings(current=>{
      if(current.periods[current.active]===selectedPeriod)return current;
      return {...current,periods:current.periods.map((p,i)=>i===current.active?selectedPeriod:p)};
    });
  },[selectedPeriod,!!replay]);
  useEffect(()=>{
    const host=root.current;
    if(!host||typeof ResizeObserver==='undefined')return;
    const observer=new ResizeObserver(entries=>{
      const width=entries[0]?.contentRect.width;
      if(width>0)setNarrow(width<600);
    });
    observer.observe(host);return()=>observer.disconnect();
  },[]);
  const changePeriods=replay?.changeDisplayPeriods;
  useEffect(()=>{if(changePeriods)void changePeriods(periods);},[changePeriods,periods]);

  useEffect(()=>{
    if(replay)return;
    if(!chart.bars.length){setBatch({identity,bars:{}});setLoading(false);return;}
    let live=true;
    const controller=new AbortController();
    batchController.current=controller;
    const tip=chart.bars.at(-1)!;
    const cutoff=Math.min(Date.now(),barCloseAtMs(tip,primaryPeriod,symbol));
    const end=new Date(cutoff).toISOString();
    setLoading(true);setError('');
    const timer=window.setTimeout(()=>controller.abort(new Error('多周期行情加载超时，请重试')),30000);
    void Promise.all(periods.map(async period=>{
      if(period===primaryPeriod)return [period,chart.bars] as const;
      const start=new Date(cutoff-Math.min(30*86400000,PERIOD_MINUTES[period]*500*60_000)).toISOString();
      const response=await getMarketBars(symbol,period,start,end,controller.signal,false,'history_prefetch',chart.tradingHours);
      if(!Array.isArray(response.bars))throw new Error('多周期行情响应无效');
      return [period,response.bars] as const;
    })).then(entries=>{
      if(live&&!controller.signal.aborted)setBatch({identity,bars:Object.fromEntries(entries)});
    }).catch(reason=>{
      if(live)setError(controller.signal.reason instanceof Error?controller.signal.reason.message:reason instanceof Error?reason.message:'多周期行情加载失败');
    }).finally(()=>{clearTimeout(timer);if(live)setLoading(false);});
    return()=>{live=false;clearTimeout(timer);controller.abort();};
  // The replay coordinator already owns its batch and cancellation lifecycle.
  },[identity,symbol,primaryPeriod,chart.bars,periods,!!replay,reload]);

  const historyLoaders=useMemo(()=>Object.fromEntries(periods.map(period=>[period,async(start:string,end:string)=>{
    const owner=batchController.current;
    if(!owner||owner.signal.aborted)return;
    const controller=new AbortController();
    const cancel=()=>controller.abort();
    owner.signal.addEventListener('abort',cancel,{once:true});
    const timer=window.setTimeout(()=>controller.abort(new Error('历史行情加载超时')),30000);
    try{
      const bars=await loadHistoryBars(period,start,end,async(left,right)=>{
        const response=await getMarketBars(symbol,period,left,right,controller.signal,false,'history_prefetch',chart.tradingHours);
        if(!Array.isArray(response.bars))throw new Error('历史行情响应无效');
        return response.bars;
      },()=>!controller.signal.aborted);
      if(!controller.signal.aborted)setBatch(current=>current.identity===identity?{
        ...current,bars:{...current.bars,[period]:mergeBars(current.bars[period]??[],bars)},
      }:current);
    }catch(reason){
      if(!owner.signal.aborted)setError(reason instanceof Error?reason.message:'历史行情加载失败');
      throw reason;
    }finally{clearTimeout(timer);owner.signal.removeEventListener('abort',cancel);}
  }])) as Record<Period,(start:string,end:string)=>Promise<void>>,[identity,symbol,periods,chart.tradingHours]);

  const source=replay?.barsByPeriod??(batch.identity===identity?batch.bars:{});
  const drawingScope=`${userScope}:${symbol}:${replay?`replay:${replay.id}`:'market'}`;
  const cutoff=replay?Date.parse(replay.dataCursor??replay.cursor):chart.bars.length?Date.parse(chart.bars.at(-1)!.timestamp)+PERIOD_MINUTES[primaryPeriod]*60_000:0;
  const markers=(chart.markers??[]).filter(marker=>Date.parse(marker.timestamp)<=cutoff);
  const busy=replay?.loading??loading;
  const changeLayout=(layout:1|2|4)=>{
    const active=layout===2&&settings.active>1?0:settings.active;
    setSettings(current=>({...current,layout,active}));
    if(active!==settings.active&&!replay)chart.onPeriodChange?.(settings.periods[active]);
  };
  const activate=(index:number)=>{
    if(settings.active===index)return;
    replay?.onPause();setSettings(current=>({...current,active:index}));
    if(!replay)chart.onPeriodChange?.(settings.periods[index]);
  };
  return <div ref={root} className="chart-workspace" data-layout={narrow?1:settings.layout}>
    <div className="chart-workspace-toolbar" role="group" aria-label="多图布局与联动">
      <strong>{symbol}</strong>
      {([1,2,4] as const).map(count=><button key={count} type="button" aria-pressed={settings.layout===count} onClick={()=>changeLayout(count)}>{count===1?'单图':count===2?'双图':'四图'}</button>)}
      <label><input type="checkbox" checked={settings.crosshair} onChange={e=>setSettings(s=>({...s,crosshair:e.target.checked}))}/>十字线联动</label>
      <label><input type="checkbox" checked={settings.time} onChange={e=>setSettings(s=>({...s,time:e.target.checked}))}/>时间定位联动</label>
      <div className="chart-workspace-actions" ref={setToolbarContainer}/>
      {busy&&<span className="chart-workspace-loading">正在同步行情…</span>}
    </div>
    {narrow&&<div className="chart-workspace-tabs" role="group" aria-label="选择活动周期">{settings.periods.slice(0,settings.layout===2?2:4).map((period,i)=><button key={i} type="button" aria-pressed={settings.active===i} onClick={()=>activate(i)}>{period}</button>)}</div>}
    {error&&<div role="alert" className="chart-workspace-error">{error}<button type="button" onClick={()=>setReload(n=>n+1)}>重试多周期行情</button></div>}
    <div className="chart-workspace-replay" ref={setReplayPanelContainer}/>
    <div className="chart-workspace-body">
    <div ref={setDrawingContainer} className="chart-workspace-drawings"/>
    <div className="chart-workspace-grid">
      {visible.map(index=>{
        const period=settings.periods[index],active=index===settings.active;
        const analysisSource=period===(chart.analysisPeriod??primaryPeriod);
        const bars=source[period]??(analysisSource?chart.analysisBars:undefined)??[];
        const primary=period===primaryPeriod;
        return <section key={index} role="region" aria-label={`${symbol} · ${period} 图表`} className={`chart-workspace-pane${active?' is-active':''}`} onPointerDownCapture={()=>activate(index)}>
          <header className="chart-workspace-pane-heading">
            <button type="button" aria-label={`选中图表 ${index+1}`} aria-pressed={active} onClick={()=>activate(index)}>{symbol} <b>{period}</b></button>
            <select aria-label={`图表 ${index+1} 周期`} value={period} disabled={busy} onChange={e=>{
              const value=e.target.value as Period;replay?.onPause();
              setSettings(s=>({...s,active:index,periods:s.periods.map((p,i)=>i===index?value:p)}));
              if(!replay)chart.onPeriodChange?.(value);
            }}>{INDICATOR_PERIODS.map(p=><option key={p}>{p}</option>)}</select>
            {bars.at(-1)?.is_partial&&<small>未收盘</small>}
          </header>
          <TradingChart {...chart} bars={bars} period={period} chartKey={`${chart.chartKey}:pane:${index}:${period}`} showToolbar={active} toolbarContainer={toolbarContainer} replayPanelContainer={replayPanelContainer}
            indicatorStorageKey={`pa.chart.indicators.workspace:${userScope}:${index}`}
            symbol={symbol} markers={mapReplayMarkers(markers,bars,symbol,chart.tradingHours??'ETH',period)}
            stage1={analysisSource?chart.stage1:undefined} stage2={analysisSource?chart.stage2:undefined} analysisBars={analysisSource?chart.analysisBars:undefined}
            onReplayLoad={active?chart.onReplayLoad:undefined} onStartTradingReplay={active?chart.onStartTradingReplay:undefined}
            onNeedHistory={primary?chart.onNeedHistory:active&&!replay?historyLoaders[period]:undefined} onNeedFuture={primary?chart.onNeedFuture:undefined}
            renderOverlay={context=><PaneOverlay context={context} sync={sync} id={String(index)} period={period} bars={bars}>
              <ChartDrawingTools context={context} scope={drawingScope} period={period} active={active} toolbarContainer={drawingContainer} bars={bars} cursor={replay?.dataCursor??replay?.cursor}
                disabled={busy||replay?.disabled} onInteractionStart={replay?.onPause} onEditingChange={onDrawingInteractionChange}/>
              {active?chart.renderOverlay?.(context):null}
            </PaneOverlay>}/>
        </section>;
      })}
    </div>
    </div>
  </div>;
}
