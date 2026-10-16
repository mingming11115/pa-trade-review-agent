import type { ReplayOrder } from '../types';

export type ProtectionField = 'stop_loss' | 'take_profit';

/** Only user-selected prices are snapped; server position averages retain their precision. */
export function snapChartPrice(price: number, tickSize: string): string | null {
  const tick = Number(tickSize);
  if (!Number.isFinite(price) || !Number.isFinite(tick) || tick <= 0) return null;
  const precision = (tickSize.split('.')[1] ?? '').length;
  const ticks = price / tick;
  const snapped = Number((Math.round(ticks + Number.EPSILON * Math.abs(ticks)) * tick).toFixed(precision));
  return snapped > 0 ? String(snapped) : null;
}

export function suggestedOrderKind(side: 'buy' | 'sell', price: string, lastPrice: string | null): ReplayOrder['kind'] | null {
  if (lastPrice == null || !Number.isFinite(Number(lastPrice)) || Number(lastPrice) <= 0) return null;
  return (side === 'buy' ? Number(price) <= Number(lastPrice) : Number(price) >= Number(lastPrice)) ? 'limit' : 'stop_market';
}

export function protectionError(field: ProtectionField, price: string, quantity: number, lastPrice: string | null): string | null {
  if (!quantity || lastPrice == null || !Number.isFinite(Number(lastPrice)) || Number(lastPrice) <= 0) return '当前价格不可用，请等待行情加载';
  const below = field === 'stop_loss' ? quantity > 0 : quantity < 0;
  if (!Number.isFinite(Number(price)) || Number(price) <= 0) return '保护价必须大于 0';
  if (below ? Number(price) >= Number(lastPrice) : Number(price) <= Number(lastPrice)) {
    return `${field === 'stop_loss' ? 'SL 止损' : 'TP 止盈'}必须${below ? '低于' : '高于'}现价 ${lastPrice}`;
  }
  return null;
}

export function signedUsd(value: string | number): string {
  const amount = Number(value);
  return `${amount > 0 ? '+' : ''}${amount.toFixed(2)} USD`;
}
