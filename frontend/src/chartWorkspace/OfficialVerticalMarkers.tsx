import { useEffect, useRef, useState } from 'react';
import { VerticalLine } from '@tradingview/lwc-plugin-vertical-line';
import { LineStyle, type MouseEventParams, type Time, type UTCTimestamp } from 'lightweight-charts';
import type { ChartOverlayContext } from '../chartOverlay';
import type { Bar, Period } from '../types';
import { barCloseAtMs } from '../tradingHours';
import { PERIOD_MINUTES } from '../feed';
import { getDrawingScope, subscribeDrawingScope, updateDrawing, type DrawingEntry } from './chartDrawings';
import './chartDrawingTools.css';

export interface OfficialVerticalMarkersProps {
 context:ChartOverlayContext; scope:string; period:Period; active:boolean; bars?:Bar[];
 cursor?:string; disabled?:boolean; onInteractionStart?:()=>void; onEditingChange?:(editing:boolean)=>void;
}
function seconds(time:Time):number {
 return typeof time==='number'?time:typeof time==='string'?Date.parse(time)/1000:Date.UTC(time.year,time.month-1,time.day)/1000;
}
export function OfficialVerticalMarkers({context,scope,period,active,bars=[],cursor,disabled=false,onInteractionStart,onEditingChange}:OfficialVerticalMarkersProps){
 const [armed,setArmed]=useState(false),[selected,setSelected]=useState<string|null>(null),[legacy,setLegacy]=useState(0),[error,setError]=useState<string|null>(null);
 const latest=useRef({bars,cursor,disabled,onInteractionStart,onEditingChange});latest.current={bars,cursor,disabled,onInteractionStart,onEditingChange};
 const armRef=useRef(false),reconcile=useRef<()=>void>(()=>{});
 const editingRef=useRef(false);
 const editing=(value:boolean)=>{if(editingRef.current!==value){editingRef.current=value;latest.current.onEditingChange?.(value);}};
 const choose=(value:boolean)=>{armRef.current=value;setArmed(value);editing(value);if(value)latest.current.onInteractionStart?.();};
 useEffect(()=>{
  armRef.current=false;setArmed(false);setSelected(null);editing(false);
  const attached=new Map<string,{plugin:VerticalLine;off:()=>void}>();
  let writing=false,applying=false;
  let beforeDrag:DrawingEntry|null=null;
  const editable=()=>active&&!latest.current.disabled;
  const sync=()=>{
   if(writing)return;
   if(!editable())beforeDrag=null;
   const snapshot=getDrawingScope(scope,latest.current.cursor,period);setError(snapshot.error);
   setLegacy(snapshot.drawings.filter(entry=>entry.drawing.kind!=='vertical-line').length);
   const desired=new Set<string>();applying=true;
   for(const {drawing} of snapshot.drawings){
    if(drawing.kind!=='vertical-line'||drawing.hidden)continue;
    const width=PERIOD_MINUTES[period]*60;
    const time=drawing.points[0].time;
    const match=latest.current.bars.find(bar=>bar.trading_hours==='RTH'&&Date.parse(bar.timestamp)/1000<=time&&time*1000<barCloseAtMs(bar,period));
    const bucket=match?Date.parse(match.timestamp)/1000:Math.floor(time/width)*width;
    // Never show an annotation in a neighboring bar across an archive gap.
    if(!latest.current.bars.some(bar=>Date.parse(bar.timestamp)/1000===bucket))continue;
    desired.add(drawing.id);
    const options={id:drawing.id,color:drawing.style.color,width:drawing.style.width,lineStyle:drawing.style.lineStyle==='dashed'?LineStyle.Dashed:LineStyle.Solid,showLabel:true,labelText:'',labelBackgroundColor:drawing.style.color,snap:'exact' as const,draggable:editable()&&!drawing.locked};
    const existing=attached.get(drawing.id);
    if(existing){existing.plugin.applyOptions(options);existing.plugin.setTime(bucket as UTCTimestamp);continue;}
    const plugin=new VerticalLine(bucket as UTCTimestamp,options);
    const changed=(time:Time)=>{
     if(applying||!editable())return;
     const current=getDrawingScope(scope,latest.current.cursor,period).drawings.find(entry=>entry.drawing.id===drawing.id);
     if(!current)return;
     const at=seconds(time);if(!Number.isFinite(at))return;
     writing=true;
     updateDrawing(scope,{...current.drawing,points:[{...current.drawing.points[0],time:at}]},{cursor:latest.current.cursor,period:current.period});
     writing=false;
    };
    plugin.timeChanged().subscribe(changed);
    context.series.attachPrimitive(plugin);
    attached.set(drawing.id,{plugin,off:()=>plugin.timeChanged().unsubscribe(changed)});
   }
   for(const [id,entry] of attached){if(!desired.has(id)){entry.off();context.series.detachPrimitive(entry.plugin);attached.delete(id);}}
   applying=false;
   setSelected(previous=>previous&&snapshot.drawings.some(entry=>entry.drawing.id===previous&&entry.drawing.kind==='vertical-line'&&!entry.drawing.hidden)?previous:null);
  };
  reconcile.current=sync;sync();
  const stopStore=subscribeDrawingScope(scope,sync);
  const click=(event:MouseEventParams)=>{
   if(!editable())return;
   if(armRef.current&&event.time!==undefined){
    const at=seconds(event.time);if(!Number.isFinite(at))return;
    const id=crypto.randomUUID();
    updateDrawing(scope,{id,kind:'vertical-line',points:[{time:at,price:0}],style:{color:'#2962ff',width:2,lineStyle:'solid'}},{cursor:latest.current.cursor});
    setSelected(id);choose(false);return;
   }
   if(event.point){const hit=[...attached].find(([,entry])=>entry.plugin.hitTest(event.point!.x,event.point!.y));setSelected(hit?.[0]??null);}
  };
  const pointerDown=(event:PointerEvent)=>{
   if(!editable()||event.button!==0)return;
   const rect=context.container.getBoundingClientRect();
   const hit=[...attached].find(([,entry])=>entry.plugin.hitTest(event.clientX-rect.left,event.clientY-rect.top));
   if(!hit)return;
   const current=getDrawingScope(scope,latest.current.cursor,period).drawings.find(entry=>entry.drawing.id===hit[0]);
   if(!current||current.drawing.locked)return;
   beforeDrag=current;setSelected(hit[0]);latest.current.onInteractionStart?.();editing(true);
  };
  const contextMenu=(event:MouseEvent)=>{
   if(!active)return;
   const rect=context.container.getBoundingClientRect();
   const hit=[...attached].find(([,entry])=>entry.plugin.hitTest(event.clientX-rect.left,event.clientY-rect.top));
   if(!hit)return;
   event.preventDefault();event.stopImmediatePropagation();setSelected(hit[0]);latest.current.onInteractionStart?.();
  };
  const pointerUp=()=>{beforeDrag=null;editing(armRef.current);};
  const escape=(event:KeyboardEvent)=>{
   if(!active||event.key!=='Escape')return;
   if(beforeDrag){
    const original=beforeDrag;beforeDrag=null;
    attached.get(original.drawing.id)?.plugin.applyOptions({draggable:false});
    updateDrawing(scope,original.drawing,{cursor:latest.current.cursor,period:original.period});
   }
   choose(false);sync();
  };
  context.chart.subscribeClick(click);
  context.container.addEventListener('pointerdown',pointerDown,true);
  context.container.addEventListener('contextmenu',contextMenu,true);
  document.addEventListener('pointerup',pointerUp);
  window.addEventListener('keydown',escape);
  return()=>{
   stopStore();reconcile.current=()=>{};
   context.chart.unsubscribeClick(click);context.container.removeEventListener('pointerdown',pointerDown,true);
   context.container.removeEventListener('contextmenu',contextMenu,true);
   document.removeEventListener('pointerup',pointerUp);window.removeEventListener('keydown',escape);
   for(const entry of attached.values()){entry.off();context.series.detachPrimitive(entry.plugin);}
   armRef.current=false;editing(false);
  };
 },[context,scope,period,active]);
 useEffect(()=>{if(disabled){choose(false);}reconcile.current();},[bars,cursor,disabled]);
 const remove=()=>{if(!selected||disabled)return;latest.current.onInteractionStart?.();updateDrawing(scope,null,{id:selected,cursor});setSelected(null);};
 return <>
  {active&&<div className="chart-drawing-ui" data-chart-drawing-ui data-plugin="@tradingview/lwc-plugin-vertical-line" onPointerDown={event=>event.stopPropagation()}>
   <button type="button" aria-label="添加竖线" aria-pressed={armed} disabled={disabled} onClick={()=>choose(!armed)}>竖线</button>
   {selected&&<button type="button" aria-label="删除选中竖线" disabled={disabled} onClick={remove}>删除</button>}
   {armed&&<span role="status">点击 K 线放置时间标记 · Esc 取消</span>}
  </div>}
  {active&&legacy>0&&<small className="chart-legacy-notice">旧版标注 {legacy} 条已保留，官方插件不支持显示</small>}
  {error&&<span role="status" className="chart-drawing-error">{error}</span>}
 </>;
}
