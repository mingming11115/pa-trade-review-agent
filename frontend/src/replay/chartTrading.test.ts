import { describe, expect, it } from 'vitest';
import { protectionError, snapChartPrice, suggestedOrderKind } from './chartTrading';

describe('chart trading prices', () => {
  it.each([[101.13, '0.25', '101.25'], [101.124, '0.25', '101'], [2345.26, '0.1', '2345.3'], [2345.2, '0.1', '2345.2'], [0.15, '0.1', '0.2']])('snaps %s to tick %s without floating point tails', (price, tick, expected) => {
    expect(snapChartPrice(Number(price), String(tick))).toBe(expected);
  });
  it('rejects prices that cannot be submitted', () => {
    expect(snapChartPrice(NaN, '0.25')).toBeNull();
    expect(snapChartPrice(0.01, '0.25')).toBeNull();
  });
  it('chooses limit and stop entries on the correct side of the current price', () => {
    expect(suggestedOrderKind('buy', '99.75', '100')).toBe('limit');
    expect(suggestedOrderKind('buy', '100.25', '100')).toBe('stop_market');
    expect(suggestedOrderKind('sell', '99.75', '100')).toBe('stop_market');
    expect(suggestedOrderKind('sell', '100.25', '100')).toBe('limit');
    expect(suggestedOrderKind('buy', '100', null)).toBeNull();
  });
  it('validates long and short protection against current price, allowing profitable stops', () => {
    expect(protectionError('stop_loss', '105', 2, '110')).toBeNull();
    expect(protectionError('take_profit', '110', 2, '110')).toBeTruthy();
    expect(protectionError('stop_loss', '105', -2, '100')).toBeNull();
    expect(protectionError('take_profit', '105', -2, '100')).toBeTruthy();
  });
});
