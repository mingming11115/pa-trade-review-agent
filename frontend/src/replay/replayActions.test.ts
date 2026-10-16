import { expect,it } from 'vitest';
import { endTraining, mapReplayMarkers } from './replayActions';
import type { ReplayCommandInput,ReplayOrder } from '../types';

it('cancels opening orders then closes and finishes without advancing the replay',async()=>{
  const commands:ReplayCommandInput[]=[];
  await endTraining([{id:'open',status:'pending',reduce_only:false} as ReplayOrder],2,async c=>{commands.push(c);return true;});
  expect(commands.map(c=>c.type)).toEqual(['cancel_order','place_order','finish']);
  expect(commands[1]).toMatchObject({reduce_only:true,quantity:2,side:'sell'});
});
it('stops termination when cancellation conflicts',async()=>{
  const commands:ReplayCommandInput[]=[];
  await endTraining([{id:'open',status:'pending',reduce_only:false} as ReplayOrder],2,async c=>{commands.push(c);return false;});
  expect(commands).toHaveLength(1);
});
it('maps occurred fills inside a partial 5m candle to its displayed bucket',()=>{
  const bars=[{timestamp:'2025-01-01T10:00:00Z',open:100,high:101,low:99,close:100,volume:0}];
  const markers=mapReplayMarkers([{timestamp:'2025-01-01T10:03:00Z',position:'belowBar',shape:'arrowUp',color:'green',text:'entry'}],bars);
  expect(markers[0].timestamp).toBe('2025-01-01T10:00:00Z');
});
