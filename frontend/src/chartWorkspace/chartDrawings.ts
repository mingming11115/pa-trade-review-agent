// Plugin-independent storage also retains unsupported legacy annotations.
export interface Drawing {
 id:string; kind:string; points:Array<{time:number;price:number}>;
 style:{color:string;width:number;lineStyle?:string;[key:string]:unknown};
 locked?:boolean; hidden?:boolean; text?:string; [key:string]:unknown;
}
import type { Period } from '../types';

export type DrawingEntry = { drawing: Drawing; period?: Period };
type Revision = { id: string; at: number | null; drawing: Drawing | null; period?: Period };
type Document = { version: 1; revisions: Revision[] };
type ScopeState = { document: Document; error: string | null; listeners: Set<() => void> };
const states = new Map<string, ScopeState>();

const periods: Period[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];
export const drawingStorageKey = (scope: string) => `pa.chart-drawings.v1:${scope}`;
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

function validDrawing(value: unknown): value is Drawing {
 if (!value || typeof value !== 'object') return false;
 const d = value as Drawing;
 return typeof d.id === 'string' && d.id.length > 0 && typeof d.kind === 'string' && d.kind.length > 0
  && Array.isArray(d.points) && d.points.length > 0
  && d.points.every(p => p && typeof p.time === 'number' && Number.isFinite(p.time) && Number.isFinite(p.price))
  && !!d.style && typeof d.style.color === 'string' && Number.isFinite(d.style.width) && d.style.width >= 0;
}
function stateFor(scope: string): ScopeState {
 const existing = states.get(scope); if (existing) return existing;
 const state: ScopeState = { document: { version: 1, revisions: [] }, error: null, listeners: new Set() };
 try {
  const json = window.localStorage.getItem(drawingStorageKey(scope));
  if (json) {
   const parsed = JSON.parse(json) as Document;
   if (parsed.version !== 1 || !Array.isArray(parsed.revisions) || !parsed.revisions.every(r => r && typeof r.id === 'string' && (r.at === null || Number.isFinite(r.at)) && (r.drawing === null || validDrawing(r.drawing) && r.drawing.id === r.id) && (r.period === undefined || periods.includes(r.period)))) throw new Error('invalid drawing document');
   state.document = parsed;
  }
 } catch { state.error = '未能保存到此浏览器：存储不可用或标注数据损坏'; }
 states.set(scope, state); return state;
}
function persist(scope: string, state: ScopeState) {
 try { window.localStorage.setItem(drawingStorageKey(scope), JSON.stringify(state.document)); state.error = null; }
 catch { state.error = '未能保存到此浏览器'; }
 state.listeners.forEach(listener => listener());
}
export function getDrawingScope(scope: string, cursor?: string, period?: Period): { drawings: DrawingEntry[]; error: string | null } {
 const state = stateFor(scope); const limit = cursor === undefined ? Infinity : Date.parse(cursor);
 const latest = new Map<string, Revision>();
 // At equal cursors the later edit wins. Sorting also handles an edit made while reviewing history.
 const revisions = state.document.revisions.map((r, index) => ({ r, index })).filter(({ r }) => r.at === null || r.at <= limit)
  .sort((a, b) => (a.r.at ?? -Infinity) - (b.r.at ?? -Infinity) || a.index - b.index);
 revisions.forEach(({ r }) => latest.set(r.id, r));
 return { drawings: [...latest.values()].filter(r => r.drawing && (!period || !r.period || period === r.period)).map(r => ({ drawing: clone(r.drawing!), period: r.period })), error: state.error };
}
export function updateDrawing(scope: string, drawing: unknown, options: { id?: string; cursor?: string; period?: Period } = {}) {
 if (drawing !== null && !validDrawing(drawing)) return;
 const id = drawing?.id ?? options.id; const at = options.cursor === undefined ? null : Date.parse(options.cursor);
 if (!id || drawing && !validDrawing(drawing) || at !== null && !Number.isFinite(at)) return;
 const state = stateFor(scope);
 const revision: Revision = { id, at, drawing: drawing ? clone(drawing) : null, period: options.period };
 // Repeated drag updates at one replay cursor are one revision, keeping storage bounded per edit.
 state.document.revisions = state.document.revisions.filter(r => !(r.id === id && r.at === at));
 state.document.revisions.push(revision); persist(scope, state);
}
export function copyDrawingScope(from: string, to: string) {
 const target = stateFor(to); target.document = clone(stateFor(from).document); persist(to, target);
}
export function subscribeDrawingScope(scope: string, listener: () => void) {
 const state = stateFor(scope); state.listeners.add(listener); return () => { state.listeners.delete(listener); };
}
