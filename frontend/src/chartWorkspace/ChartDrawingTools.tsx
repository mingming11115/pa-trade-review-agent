import { useEffect, useRef, useState } from 'react';
import { DrawingManager, OVERLAY_SPECS, type Drawing, type OHLC } from 'lightweight-charts-drawing';
import type { UTCTimestamp } from 'lightweight-charts';
import type { ChartOverlayContext } from '../chartOverlay';
import type { Bar, Period } from '../types';
import { mountUpstreamDrawingUI } from './upstream/mountUpstreamDrawingUI';
import { enableDrawingImages, saveDrawingImage } from './drawingImages';
import { getDrawingScope, subscribeDrawingScope, updateDrawing } from './chartDrawings';
import { OfficialVerticalMarkers } from './OfficialVerticalMarkers';
import './chartDrawingTools.css';

export interface ChartDrawingToolsProps {
 context:ChartOverlayContext; scope:string; period:Period; active:boolean; bars?:Bar[];
 cursor?:string; disabled?:boolean; onInteractionStart?:()=>void; onEditingChange?:(editing:boolean)=>void;
}
export function ChartDrawingTools({context,scope,period,active,bars,cursor,disabled=false,onInteractionStart,onEditingChange}:ChartDrawingToolsProps){
 const host=useRef<HTMLDivElement>(null);
 const latest=useRef({bars,cursor,disabled,onInteractionStart,onEditingChange});latest.current={bars,cursor,disabled,onInteractionStart,onEditingChange};
 const reconcile=useRef<()=>void>(()=>{});
 const [error,setError]=useState<string|null>(null);
 useEffect(()=>{
  enableDrawingImages();
  const editable=()=>active&&!latest.current.disabled;
  const manager=new DrawingManager(context.chart,context.series,{magnet:'off',stayInDrawingMode:false,bars:()=>latest.current.bars?.map(bar=>({...bar,time:Date.parse(bar.timestamp)/1000 as UTCTimestamp,volume:bar.volume??undefined}))??context.series.data().filter(bar=>'open' in bar) as OHLC[]});
  let applying=false,writing=false,cancelled=false,disposed=false,editing=false,editorOpen=false;
  let before:readonly Drawing[]|null=null;
  const report=(value:boolean)=>{if(value===editing)return;editing=value;latest.current.onEditingChange?.(value);};
  const supported=(kind:string)=>Object.hasOwn(OVERLAY_SPECS,kind);
  const sync=()=>{
   if(writing)return;
   const snapshot=getDrawingScope(scope,latest.current.cursor,period);setError(snapshot.error);
   const ids=manager.selection();applying=true;
   manager.importJSON(snapshot.drawings.filter(entry=>supported(entry.drawing.kind)&&(active||entry.drawing.kind!=='vertical-line')).map(entry=>editable()?entry.drawing:{...entry.drawing,locked:true}));
   manager.select(ids.filter(id=>manager.get(id)));applying=false;
  };
  const save=(drawing:Drawing|null,id?:string)=>{
   if(!editable()||applying||cancelled||before)return;
   const previous=getDrawingScope(scope,latest.current.cursor).drawings.find(entry=>entry.drawing.id===(drawing?.id??id));
   writing=true;updateDrawing(scope,drawing,{id,cursor:latest.current.cursor,period:previous?.period});writing=false;
  };
  const saveChanges=()=>{
   if(!editable()||applying||cancelled||before)return;
   const previous=getDrawingScope(scope,latest.current.cursor,period).drawings.filter(entry=>supported(entry.drawing.kind));
   const current=manager.drawings();
   current.forEach(drawing=>{if(JSON.stringify(previous.find(entry=>entry.drawing.id===drawing.id)?.drawing)!==JSON.stringify(drawing))save(drawing);});
   previous.forEach(entry=>{if(!current.some(drawing=>drawing.id===entry.drawing.id))save(null,entry.drawing.id);});
  };
  const finish=()=>{
   if(disposed)return;
   const start=before;before=null;report(editable()&&(manager.tool()!==null||editorOpen));
   if(!start||cancelled||!editable())return;
   saveChanges();
  };
  const off=[
   manager.on('tool',kind=>{if(editable()&&kind)latest.current.onInteractionStart?.();report(editable()&&(kind!==null||before!==null||editorOpen));}),
   manager.on('add',drawing=>save(drawing)),manager.on('update',drawing=>save(drawing)),manager.on('remove',drawing=>save(null,drawing.id)),
   manager.on('change',saveChanges),manager.on('gestureEnd',finish),subscribeDrawingScope(scope,sync),
  ];
  const pointerDown=(event:PointerEvent)=>{
   if(!editable()||event.button!==0)return;
   if(manager.tool()||manager.hoveredId()){before=structuredClone(manager.drawings());report(true);latest.current.onInteractionStart?.();}
  };
  const pointerUp=()=>queueMicrotask(finish);
  const contextMenu=(event:MouseEvent)=>{
   const id=manager.hoveredId();if(!id)return;
   event.preventDefault();event.stopImmediatePropagation();
   if(editable()){manager.select([id]);latest.current.onInteractionStart?.();}
  };
  const cancel=()=>{
   cancelled=true;
   if(before){const original=before;before=null;document.dispatchEvent(new Event('pointerup',{bubbles:true}));applying=true;manager.importJSON(original);applying=false;}
   manager.setTool(null);cancelled=false;
  };
  const escape=(event:KeyboardEvent)=>{if(event.key!=='Escape'||!editable())return;cancel();manager.select([]);context.setInteractionLocked(false);};
  context.container.addEventListener('pointerdown',pointerDown,true);context.container.addEventListener('contextmenu',contextMenu,true);
  document.addEventListener('pointerup',pointerUp);window.addEventListener('keydown',escape,true);
  sync();
  if(host.current)host.current.inert=latest.current.disabled;
  const unmount=active&&host.current?mountUpstreamDrawingUI(host.current,manager,{assetScope:scope,saveImage:saveDrawingImage,isEditable:editable,onEditorChange:open=>{editorOpen=open;report(editable()&&(open||manager.tool()!==null||before!==null));}}):undefined;
  reconcile.current=()=>{if(latest.current.disabled)cancel();if(host.current)host.current.inert=latest.current.disabled;sync();report(editable()&&(editorOpen||manager.tool()!==null||before!==null));};
  return()=>{
   disposed=true;cancelled=true;reconcile.current=()=>{};
   if(before)document.dispatchEvent(new Event('pointerup',{bubbles:true}));
   unmount?.();off.forEach(stop=>stop());
   context.container.removeEventListener('pointerdown',pointerDown,true);context.container.removeEventListener('contextmenu',contextMenu,true);
   document.removeEventListener('pointerup',pointerUp);window.removeEventListener('keydown',escape,true);
   manager.destroy();context.setInteractionLocked(false);report(false);
  };
 },[context,scope,period,active]);
 useEffect(()=>{reconcile.current();},[cursor,disabled]);
 return <>
  {active&&<div ref={host} className="chart-drawing-ui" data-chart-drawing-ui data-upstream="lightweight-charts-drawing/demo@0291d9c" onPointerDown={event=>{event.stopPropagation();latest.current.onInteractionStart?.();}} onContextMenu={event=>event.stopPropagation()}/>}
  {!active&&<OfficialVerticalMarkers context={context} scope={scope} period={period} bars={bars} cursor={cursor} active={false} disabled/>}
  {error&&<span className="chart-drawing-error" role="status">{error}</span>}
 </>;
}
