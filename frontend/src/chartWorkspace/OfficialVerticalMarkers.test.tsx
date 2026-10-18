import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ChartOverlayContext } from '../chartOverlay';
import { OfficialVerticalMarkers as ChartDrawingTools } from './OfficialVerticalMarkers';
import { getDrawingScope, updateDrawing } from './chartDrawings';
const native=vi.hoisted(()=>({lines:[] as any[]}));
vi.mock('@tradingview/lwc-plugin-vertical-line',()=>({VerticalLine:class{
 value:number;settings:any;changed=new Set<(time:number)=>void>();
 constructor(time:number,options:any){this.value=time;this.settings=options;native.lines.push(this);}
 setTime(time:number){this.value=time;}time(){return this.value;}
 applyOptions(options:any){Object.assign(this.settings,options);}
 timeChanged(){return {subscribe:(fn:(time:number)=>void)=>this.changed.add(fn),unsubscribe:(fn:(time:number)=>void)=>this.changed.delete(fn)};}
 hitTest(){return null;}
 move(time:number){this.value=time;this.changed.forEach(fn=>fn(time));}
}}));
const time=Date.parse('2026-10-18T10:07:00Z')/1000;
const bar={timestamp:'2026-10-18T10:05:00Z',open:1,high:2,low:1,close:2,volume:1};
function context(){const clicks=new Set<(event:any)=>void>();const series={attachPrimitive:vi.fn(),detachPrimitive:vi.fn()};return {clicks,series,value:{container:document.createElement('div'),chart:{subscribeClick:(fn:any)=>clicks.add(fn),unsubscribeClick:(fn:any)=>clicks.delete(fn)},series} as unknown as ChartOverlayContext};}
function drawing(id='official'){return {id,kind:'vertical-line',points:[{time,price:0}],style:{color:'#2962ff',width:2,lineStyle:'solid'}};}
beforeEach(()=>{native.lines.length=0;const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value)});});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('attaches the official plugin at the containing bucket while preserving the canonical UTC anchor',()=>{
 updateDrawing('official-bucket',drawing());const ctx=context();const view=render(<ChartDrawingTools context={ctx.value} scope="official-bucket" period="5m" bars={[bar]} active/>);
 expect(native.lines).toHaveLength(1);expect(native.lines[0].value).toBe(Date.parse(bar.timestamp)/1000);
 expect(ctx.series.attachPrimitive).toHaveBeenCalledWith(native.lines[0]);expect(native.lines[0].settings.draggable).toBe(true);
 expect(getDrawingScope('official-bucket').drawings[0].drawing.points[0].time).toBe(time);
 view.unmount();expect(ctx.series.detachPrimitive).toHaveBeenCalled();expect(ctx.clicks.size).toBe(0);
});
it('places one official time marker, pauses trading interaction, and deletes only the selected marker',()=>{
 const ctx=context(),pause=vi.fn(),editing=vi.fn();render(<ChartDrawingTools context={ctx.value} scope="official-place" period="5m" bars={[bar]} active onInteractionStart={pause} onEditingChange={editing}/>);
 fireEvent.click(screen.getByRole('button',{name:'添加竖线'}));expect(editing).toHaveBeenLastCalledWith(true);
 act(()=>ctx.clicks.forEach(fn=>fn({time,point:{x:20,y:10}})));
 expect(getDrawingScope('official-place').drawings).toHaveLength(1);expect(pause).toHaveBeenCalled();expect(editing).toHaveBeenLastCalledWith(false);
 fireEvent.click(screen.getByRole('button',{name:'删除选中竖线'}));expect(getDrawingScope('official-place').drawings).toHaveLength(0);
});
it('shares native drag time changes and keeps passive charts read only',()=>{
 updateDrawing('official-drag',drawing());const first=context(),second=context();
 render(<><ChartDrawingTools context={first.value} scope="official-drag" period="5m" bars={[bar]} active/><ChartDrawingTools context={second.value} scope="official-drag" period="5m" bars={[bar]} active={false}/></>);
 expect(native.lines[1].settings.draggable).toBe(false);
 act(()=>native.lines[0].move(time+60));expect(getDrawingScope('official-drag').drawings[0].drawing.points[0].time).toBe(time+60);
});
it('does not draw across a missing bucket and preserves unsupported legacy annotations',()=>{
 const legacy={...drawing('legacy'),kind:'trend-line',points:[{time,price:1},{time:time+60,price:2}]};
 updateDrawing('official-legacy',legacy);updateDrawing('official-legacy',drawing());
 const ctx=context();render(<ChartDrawingTools context={ctx.value} scope="official-legacy" period="5m" bars={[]} active/>);
 expect(native.lines).toHaveLength(0);expect(getDrawingScope('official-legacy').drawings).toHaveLength(2);
 expect(screen.getByText(/旧版标注/)).toBeInTheDocument();
});
it('disables native dragging and placement until loading finishes without replacing the plugin',()=>{
 updateDrawing('official-disabled',drawing());const ctx=context();const props={context:ctx.value,scope:'official-disabled',period:'5m' as const,bars:[bar],active:true};
 const view=render(<ChartDrawingTools {...props}/>);view.rerender(<ChartDrawingTools {...props} disabled/>);
 expect(native.lines).toHaveLength(1);expect(native.lines[0].settings.draggable).toBe(false);expect(screen.getByRole('button',{name:'添加竖线'})).toBeDisabled();
 act(()=>native.lines[0].move(time+60));expect(getDrawingScope('official-disabled').drawings[0].drawing.points[0].time).toBe(time);
});
it('preserves a period-only annotation when Escape rolls back an official drag',()=>{
 updateDrawing('official-escape',drawing(),{period:'5m'});const ctx=context();
 render(<ChartDrawingTools context={ctx.value} scope="official-escape" period="5m" bars={[bar]} active/>);
 native.lines[0].hitTest=()=>({externalId:'official'});
 fireEvent(ctx.value.container,new MouseEvent('pointerdown',{button:0,clientX:10,clientY:10,bubbles:true}));
 act(()=>native.lines[0].move(time+60));fireEvent.keyDown(window,{key:'Escape'});
 expect(getDrawingScope('official-escape').drawings[0].drawing.points[0].time).toBe(time);
 expect(getDrawingScope('official-escape').drawings[0].period).toBe('5m');
 expect(getDrawingScope('official-escape',undefined,'1m').drawings).toHaveLength(0);
});
it('selects an annotation on right click without opening the chart trading menu',()=>{
 updateDrawing('official-context',drawing());const ctx=context(),tradeMenu=vi.fn();
 render(<ChartDrawingTools context={ctx.value} scope="official-context" period="5m" bars={[bar]} active/>);
 native.lines[0].hitTest=()=>({externalId:'official'});ctx.value.container.addEventListener('contextmenu',tradeMenu);
 fireEvent.contextMenu(ctx.value.container,{clientX:10,clientY:10});
 expect(tradeMenu).not.toHaveBeenCalled();expect(screen.getByRole('button',{name:'删除选中竖线'})).toBeInTheDocument();
});
