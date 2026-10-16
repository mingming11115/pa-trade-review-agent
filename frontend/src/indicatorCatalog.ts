import type { IndicatorKey } from './indicatorSettings';

export const INDICATOR_CATALOG: { key: IndicatorKey; name: string; description: string; category: '主图' | '副图'; aliases: string }[] = [
  { key: 'ma', name: 'MA', description: '移动平均线', category: '主图', aliases: '简单移动平均 sma simple moving average' },
  { key: 'ema', name: 'EMA', description: '指数移动平均线', category: '主图', aliases: '指数均线 exponential moving average' },
  { key: 'boll', name: 'BOLL', description: '布林带', category: '主图', aliases: '布林线 bollinger bands bb' },
  { key: 'macd', name: 'MACD', description: '平滑异同移动平均线', category: '副图', aliases: 'moving average convergence divergence' },
  { key: 'rsi', name: 'RSI', description: '相对强弱指标', category: '副图', aliases: 'relative strength index' },
  { key: 'volume', name: 'VOL', description: '成交量', category: '主图', aliases: 'volume' },
];
