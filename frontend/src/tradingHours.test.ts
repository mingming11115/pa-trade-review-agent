import { expect, it } from 'vitest';
import { barCloseAtMs, canMarketTrade, inTradingHours } from './tradingHours';
import type { ReplaySession } from './types';
it('keeps RTH opening local through DST and excludes close and weekends',()=>{
 for(const [open,close] of [['2026-01-05T14:30:00Z','2026-01-05T21:15:00Z'],['2026-07-06T13:30:00Z','2026-07-06T20:15:00Z']]){
  expect(inTradingHours(open,'ES','RTH')).toBe(true);
  expect(inTradingHours(new Date(Date.parse(open)-60000).toISOString(),'ES','RTH')).toBe(false);
  expect(inTradingHours(close,'ES','RTH')).toBe(false);
 }
 expect(inTradingHours('2026-07-04T14:00:00Z','ES','RTH')).toBe(false);
});
it('truncates RTH tail candles and blocks a source quote from night hours',()=>{
 const bar={timestamp:'2026-07-06T19:30:00Z',trading_hours:'RTH'} as const;
 expect(barCloseAtMs(bar as any,'1h','ES')).toBe(Date.parse('2026-07-06T20:15:00Z'));
 const state={cursor:'2026-07-06T13:30:00Z',last_price:'100',last_price_at:'2026-07-06T13:29:00Z',config:{trading_hours:'RTH'}};
 expect(canMarketTrade({symbol:'ES',state} as ReplaySession)).toBe(false);
 expect(canMarketTrade({symbol:'ES',state:{...state,last_price_at:'2026-07-06T13:30:00Z'}} as ReplaySession)).toBe(true);
});
