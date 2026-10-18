import { beforeEach, expect, it, vi } from 'vitest';
import type { Drawing } from './chartDrawings';
import { copyDrawingScope, getDrawingScope, updateDrawing, subscribeDrawingScope, drawingStorageKey } from './chartDrawings';

const line = (price = 100): Drawing => ({ id: 'line', kind: 'trend-line', points: [{ time: 1000, price }, { time: 1600, price: 110 }], style: { color: '#ffffff', width: 2, lineStyle: 'solid' } }) as Drawing;
beforeEach(() => { const values = new Map<string, string>(); vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: vi.fn((key: string, value: string) => { values.set(key, value); }) }); });
it('shares changes once within a scope and isolates users, symbols and replay drafts', () => {
 const notice = vi.fn(); const off = subscribeDrawingScope('user:ES:market', notice);
 updateDrawing('user:ES:market', line());
 expect(getDrawingScope('user:ES:market').drawings[0].drawing.points[0].time).toBe(1000);
 expect(notice).toHaveBeenCalledTimes(1);
 expect(getDrawingScope('other:ES:market').drawings).toEqual([]);
 expect(getDrawingScope('user:NQ:market').drawings).toEqual([]);
 expect(getDrawingScope('user:ES:replay').drawings).toEqual([]);
 off();
});
it('persists versioned canonical anchors and restores an independent saved snapshot', () => {
 updateDrawing('snapshot-source', line()); copyDrawingScope('snapshot-source', 'saved');
 updateDrawing('snapshot-source', line(200));
 expect(getDrawingScope('saved').drawings[0].drawing.points[0].price).toBe(100);
 const parsed = JSON.parse(window.localStorage.getItem(drawingStorageKey('saved'))!);
 expect(parsed.version).toBe(1);
 expect(parsed.revisions[0].drawing.points[1].time).toBe(1600);
});
it('shows the annotation revision that existed at the replay cursor, including deletion', () => {
 const scope = 'revisions';
 updateDrawing(scope, line(), { cursor: '2026-01-01T10:00:00Z' });
 updateDrawing(scope, line(200), { cursor: '2026-01-01T10:10:00Z' });
 updateDrawing(scope, null, { id: 'line', cursor: '2026-01-01T10:20:00Z' });
 expect(getDrawingScope(scope, '2026-01-01T09:59:00Z').drawings).toEqual([]);
 expect(getDrawingScope(scope, '2026-01-01T10:05:00Z').drawings[0].drawing.points[0].price).toBe(100);
 expect(getDrawingScope(scope, '2026-01-01T10:15:00Z').drawings[0].drawing.points[0].price).toBe(200);
 expect(getDrawingScope(scope).drawings).toEqual([]);
});
it('filters current-period annotations without deleting the shared original', () => {
 updateDrawing('period', line(), { period: '5m' });
 expect(getDrawingScope('period', undefined, '1m').drawings).toEqual([]);
 expect(getDrawingScope('period', undefined, '5m').drawings).toHaveLength(1);
});
it('rejects corrupt stored drawings and retains memory with a visible persistence failure', () => {
 window.localStorage.setItem(drawingStorageKey('corrupt'), JSON.stringify({ version: 1, revisions: [{ id: 'bad', drawing: { id: 'bad', kind: 'trend-line', points: [{ time: 'bad', price: NaN }] } }] }));
 expect(getDrawingScope('corrupt').drawings).toEqual([]);
 expect(getDrawingScope('corrupt').error).toBeTruthy();
 vi.mocked(window.localStorage.setItem).mockImplementation(() => { throw new Error('quota'); });
 updateDrawing('quota', line());
 expect(getDrawingScope('quota').drawings).toHaveLength(1);
 expect(getDrawingScope('quota').error).toContain('未能保存');
});
it('restores persisted revisions on the first scope read', () => {
 window.localStorage.setItem(drawingStorageKey('reload'), JSON.stringify({ version: 1, revisions: [{ id: 'line', at: null, drawing: line(42) }] }));
 expect(getDrawingScope('reload').drawings[0].drawing.points[0].price).toBe(42);
});

it('preserves legacy multi-anchor records after switching to official plugins', () => {
 const drawing = { ...line(), id: 'channel', kind: 'parallel-channel', points: [{ time: 1000, price: 100 }, { time: 1600, price: 110 }, { time: 1300, price: 115 }] } as Drawing;
 updateDrawing('native-channel', drawing);
 expect(getDrawingScope('native-channel').drawings[0]?.drawing).toEqual(drawing);
 copyDrawingScope('native-channel', 'native-channel-saved');
 expect(getDrawingScope('native-channel-saved').drawings[0]?.drawing.points).toHaveLength(3);
});
