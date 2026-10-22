import { act, cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ChartOverlayContext } from '../chartOverlay';
import { ChartDrawingTools } from './ChartDrawingTools';
import { getDrawingScope, updateDrawing } from './chartDrawings';
import { TOOL_GROUPS } from './upstream/tool-groups';
const native=vi.hoisted(()=>({managers:[] as any[]}));
vi.mock('./OfficialVerticalMarkers',()=>({OfficialVerticalMarkers:()=>null}));
vi.mock('./drawingImages',()=>({enableDrawingImages:vi.fn(),saveDrawingImage:vi.fn()}));
vi.mock('lightweight-charts-drawing',async importOriginal=>({
 ...await importOriginal<object>(),DrawingManager:class{
 list:any[]=[];ids:string[]=[];armed:any=null;hover:string|null=null;visible=true;events=new Map<string,Set<Function>>();
 setMagnet=vi.fn();setStayInDrawingMode=vi.fn();destroy=vi.fn();
 constructor(){native.managers.push(this);}
 on(event:string,fn:Function){if(!this.events.has(event))this.events.set(event,new Set());this.events.get(event)!.add(fn);return()=>this.events.get(event)?.delete(fn);}
 emit(event:string,...args:any[]){this.events.get(event)?.forEach(fn=>fn(...args));}
 drawings(){return this.list;}selection(){return this.ids;}get(id:string){return this.list.find(d=>d.id===id);}tool(){return this.armed;}
 setTool(kind:any){this.armed=kind;this.emit('tool',kind);}hoveredId(){return this.hover;}
 select(ids:string[]){this.ids=ids;this.emit('selection',ids);}
 importJSON(value:any){this.list=structuredClone(typeof value==='string'?JSON.parse(value):value);this.emit('change');return this.list.length;}
 exportJSON(){return JSON.stringify(this.list);}
 update(d:any){this.list=this.list.map(old=>old.id===d.id?d:old);this.emit('update',d);this.emit('change');}
 remove(id:string){const d=this.get(id);this.list=this.list.filter(d=>d.id!==id);this.select(this.ids.filter(v=>v!==id));this.emit('remove',d);this.emit('change');}
 clear(){this.list=[];this.select([]);this.emit('change');}
 drawingsVisible(){return this.visible;}setDrawingsVisible(value:boolean){this.visible=value;this.emit('visibility',value);}
 setHidden(id:string,value:boolean){this.update({...this.get(id),hidden:value});}
 }
}));
function context(){return {chart:{},series:{data:()=>[]},container:document.createElement('div'),setInteractionLocked:vi.fn()} as unknown as ChartOverlayContext;}
function drawing(id='old',kind='horizontal-line'){return {id,kind,points:[{time:100,price:20}],style:{color:'#ff0000',width:2,lineStyle:'solid'}};}
function ui(){return document.querySelector('[data-upstream]')!.shadowRoot!;}
function queries(){return within(ui() as unknown as HTMLElement);}
beforeEach(()=>{native.managers.length=0;const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value)});});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('restores all original tool groups and arms native trend drawing instead of a reduced vertical-line toolbar',()=>{
 const pause=vi.fn(),editing=vi.fn();render(<ChartDrawingTools context={context()} scope="restore-tools" period="5m" active onInteractionStart={pause} onEditingChange={editing}/>);
 for(const group of TOOL_GROUPS)expect(queries().getByTitle(group.title).querySelector('svg')).not.toBeNull();
 expect(ui().getElementById('count')!.textContent).toContain('86 tools');
 fireEvent.click(queries().getByTitle('Trend tools'));fireEvent.click(queries().getByRole('button',{name:/^Trendline /}));
 expect(native.managers[0].tool()).toBe('trend-line');expect(pause).toHaveBeenCalled();expect(editing).toHaveBeenLastCalledWith(true);
 fireEvent.keyDown(window,{key:'Escape'});expect(native.managers[0].tool()).toBeNull();expect(editing).toHaveBeenLastCalledWith(false);
});
it('restores old multianchor drawings and persists original selection hide and delete actions',()=>{
 const channel={...drawing('channel','parallel-channel'),points:[{time:100,price:20},{time:110,price:25},{time:120,price:30}]};
 updateDrawing('restore-old',channel,{period:'5m'});render(<ChartDrawingTools context={context()} scope="restore-old" period="5m" active/>);
 const manager=native.managers[0];expect(manager.drawings()).toEqual([channel]);
 act(()=>manager.select(['channel']));fireEvent.click(queries().getByTitle("Hide (the eye button's menu shows it again)"));
 expect(getDrawingScope('restore-old').drawings[0]).toMatchObject({period:'5m',drawing:{hidden:true}});
 fireEvent.click(queries().getByTitle('Remove'));expect(getDrawingScope('restore-old').drawings).toHaveLength(0);
});
it('keeps the original magnet, continuous drawing and visibility controls',()=>{
 render(<ChartDrawingTools context={context()} scope="restore-modes" period="5m" active/>);const manager=native.managers[0];
 fireEvent.click(queries().getByTitle('Magnet mode'));expect(manager.setMagnet).toHaveBeenCalledWith('weak');
 fireEvent.click(queries().getByTitle('Keep drawing'));expect(manager.setStayInDrawingMode).toHaveBeenCalledWith(true);
 fireEvent.click(queries().getByTitle('Hide all drawings'));expect(manager.drawingsVisible()).toBe(false);
});
it('persists bulk imports and removal without dropping unsupported saved data',()=>{
 updateDrawing('restore-import',{...drawing('future'),kind:'future-tool'});
 render(<ChartDrawingTools context={context()} scope="restore-import" period="5m" active/>);const manager=native.managers[0];
 act(()=>manager.importJSON([drawing('imported','trend-line')]));expect(getDrawingScope('restore-import').drawings.map(d=>d.drawing.id).sort()).toEqual(['future','imported']);
 act(()=>manager.clear());expect(getDrawingScope('restore-import').drawings.map(d=>d.drawing.id)).toEqual(['future']);
});
it('keeps the original editor and excludes trading until text editing finishes',()=>{
 updateDrawing('restore-text',drawing('text','text'));const editing=vi.fn();render(<ChartDrawingTools context={context()} scope="restore-text" period="5m" active onEditingChange={editing}/>);
 const manager=native.managers[0];act(()=>manager.emit('textEdit',manager.get('text'),{x:10,y:10}));
 expect(editing).toHaveBeenLastCalledWith(true);const editor=ui().getElementById('editor') as HTMLTextAreaElement;
 fireEvent.change(editor,{target:{value:'restored note'}});fireEvent.keyDown(editor,{key:'Enter'});
 expect(getDrawingScope('restore-text').drawings[0].drawing.text).toBe('restored note');expect(editing).toHaveBeenLastCalledWith(false);
});
it('preserves the toolbar and file input during busy updates while locking copies of stored annotations',()=>{
 updateDrawing('restore-busy',drawing());const ctx=context(),props={context:ctx,scope:'restore-busy',period:'5m' as const,active:true};
 const view=render(<ChartDrawingTools {...props}/>);const input=ui().getElementById('imageFile'),manager=native.managers[0];
 view.rerender(<ChartDrawingTools {...props} disabled/>);
 expect(native.managers).toHaveLength(1);expect(ui().getElementById('imageFile')).toBe(input);expect((document.querySelector('[data-upstream]') as HTMLElement).inert).toBe(true);
 expect(manager.get('old').locked).toBe(true);expect(getDrawingScope('restore-busy').drawings[0].drawing.locked).toBeUndefined();
 view.rerender(<ChartDrawingTools {...props}/>);expect(manager.get('old').locked).toBeUndefined();
 view.unmount();expect(manager.destroy).toHaveBeenCalled();expect([...manager.events.values()].every((set:any)=>set.size===0)).toBe(true);
});
it('cancels a drag without persisting its preview or losing period restrictions',()=>{
 updateDrawing('restore-escape',drawing(),{period:'5m'});const ctx=context();render(<ChartDrawingTools context={ctx} scope="restore-escape" period="5m" active/>);
 const manager=native.managers[0];manager.hover='old';fireEvent(ctx.container,new MouseEvent('pointerdown',{button:0,bubbles:true}));
 act(()=>manager.update({...drawing(),points:[{time:200,price:99}]}));fireEvent.keyDown(window,{key:'Escape'});
 expect(manager.get('old').points[0].price).toBe(20);expect(getDrawingScope('restore-escape').drawings[0].period).toBe('5m');expect(getDrawingScope('restore-escape').drawings[0].drawing.points[0].price).toBe(20);
});

it('does not report a successful async import while loading would discard it',async()=>{
 const ctx=context(),props={context:ctx,scope:'restore-async-import',period:'5m' as const,active:true};
 const view=render(<ChartDrawingTools {...props}/>);
 let resolve!:(value:string)=>void;const pending=new Promise<string>(done=>{resolve=done;});
 fireEvent.change(ui().getElementById('importFile')!,{target:{files:[{name:'drawings.json',text:()=>pending}]}});
 view.rerender(<ChartDrawingTools {...props} disabled/>);
 await act(async()=>{resolve(JSON.stringify([drawing('late')]));await pending;});
 expect(ui().getElementById('hint')!.textContent).toContain('Please import again');
 expect(getDrawingScope('restore-async-import').drawings).toHaveLength(0);
 view.rerender(<ChartDrawingTools {...props}/>);
 expect(native.managers[0].drawings()).toHaveLength(0);
});

it('places the drawing UI outside the active chart in the shared workspace rail',()=>{
 const ctx=context(),toolbar=document.createElement('div');document.body.append(toolbar);
 const view=render(<ChartDrawingTools context={ctx} scope="shared-rail" period="5m" active toolbarContainer={toolbar}/>);
 expect(toolbar.querySelector('[data-chart-drawing-ui]')).not.toBeNull();
 expect(ctx.container.querySelector('[data-chart-drawing-ui]')).toBeNull();
 view.unmount();expect(toolbar.childElementCount).toBe(0);toolbar.remove();
});
