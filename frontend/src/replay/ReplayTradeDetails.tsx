import { canMarketTrade } from '../tradingHours';
import { useId, useState, type KeyboardEvent } from 'react';
import type { ReplayCommandInput, ReplayEvents, ReplayFill, ReplayOrder, ReplaySession } from '../types';
import { ReplayResults } from './ReplayResults';
import './replayTradeDetails.css';

interface Props {
  session: ReplaySession;
  events: ReplayEvents;
  visibleFills: ReplayFill[];
  disabled: boolean;
  command: (input: ReplayCommandInput) => Promise<boolean>;
  onAmend: (order: ReplayOrder) => void;
  onPause: () => void;
}

export const orderLabels = { market: '市价', limit: '限价', stop_market: '止损市价', stop_limit: '止损限价' };
const tabs = ['成交记录', '持仓', '订单', '统计'] as const;
const dateFormat = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' });
const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });

function decimals(value: string) {
  return (value.split('.')[1] ?? '').replace(/0+$/, '').length;
}

function price(value: string | null | undefined, tick: string, preservePrecision = false) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const places = Math.min(20, decimals(tick));
  return Number(value).toLocaleString('en-US', {
    minimumFractionDigits: places,
    maximumFractionDigits: preservePrecision ? Math.min(20, Math.max(places, decimals(value))) : places,
  });
}

function money(value: string | number | undefined, signed = false) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: signed ? 'exceptZero' : 'auto' });
}

function pnlClass(value: string) {
  return Number(value) > 0 ? 'replay-ledger-positive' : Number(value) < 0 ? 'replay-ledger-negative' : '';
}

function ProtectionForm({ session, disabled, command, onCancel }: Pick<Props, 'session' | 'disabled' | 'command'> & { onCancel: () => void }) {
  const [stop, setStop] = useState(session.state.position.stop_loss ?? '');
  const [profit, setProfit] = useState(session.state.position.take_profit ?? '');
  return <form className="replay-ledger-protection" onSubmit={async event => {
    event.preventDefault();
    if (disabled) return;
    if (await command({ type: 'update_protection', stop_loss: stop || null, take_profit: profit || null })) onCancel();
  }}>
    <fieldset disabled={disabled}>
      <label>持仓止损<input aria-label="持仓止损" type="number" min="0" step={session.config.instrument.tick_size} value={stop} onChange={event => setStop(event.target.value)} placeholder="未设置" /></label>
      <label>持仓止盈<input aria-label="持仓止盈" type="number" min="0" step={session.config.instrument.tick_size} value={profit} onChange={event => setProfit(event.target.value)} placeholder="未设置" /></label>
      <button type="submit">更新保护价</button>
    </fieldset>
    <button type="button" onClick={onCancel}>取消</button>
  </form>;
}

export function ReplayTradeDetails({ session, events, visibleFills, disabled, command, onAmend, onPause }: Props) {
  const id = useId();
  const [activeTab, setActiveTab] = useState(0);
  const [editingProtection, setEditingProtection] = useState(false);
  const { position, account, orders, config } = session.state;
  const tick = session.config.instrument.tick_size;
  // Sort a copy; equal-time fills retain the server's execution sequence.
  const fills = [...visibleFills].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
  const counts = [fills.length, position.quantity ? 1 : 0, orders.length];
  const metrics = [
    { label: '账户权益 · USD', value: account.equity, signed: false },
    { label: '已实现毛利 · USD', value: account.realized_pnl, signed: true },
    { label: '浮动盈亏 · USD', value: account.unrealized_pnl, signed: true },
    { label: '手续费 · USD', value: account.fees, signed: false },
  ];
  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    setActiveTab(next);
    document.getElementById(`${id}-tab-${next}`)?.focus();
  }
  return <section className="replay-ledger" aria-label="回放交易明细">
    <dl className="replay-ledger-summary">{metrics.map(metric => <div className="replay-ledger-metric" key={metric.label}>
      <dt>{metric.label}</dt><dd className={metric.signed ? pnlClass(metric.value) : undefined} title={metric.value}>{money(metric.value, metric.signed)}</dd>
    </div>)}</dl>
    <div className="replay-ledger-tabs" role="tablist" aria-label="交易明细分类">{tabs.map((name, index) => <button key={name}
      type="button" id={`${id}-tab-${index}`} role="tab" aria-label={name} aria-controls={`${id}-panel-${index}`}
      aria-selected={activeTab === index} tabIndex={activeTab === index ? 0 : -1}
      onClick={() => setActiveTab(index)} onKeyDown={event => navigateTabs(event, index)}>
      {name}{counts[index] != null && <span className="replay-ledger-count">{counts[index]}</span>}
    </button>)}</div>

    <div className="replay-ledger-content" id={`${id}-panel-0`} role="tabpanel" aria-labelledby={`${id}-tab-0`} hidden={activeTab !== 0}>
      <div className="replay-ledger-scroll"><table className="replay-ledger-table" aria-label="成交记录">
        <thead><tr><th scope="col">品种</th><th scope="col">方向</th><th scope="col" className="replay-ledger-number">数量 / 手</th><th scope="col" className="replay-ledger-number">成交价</th><th scope="col" className="replay-ledger-number">费用 / USD</th><th scope="col">成交时间 · 北京</th></tr></thead>
        <tbody>{fills.length ? fills.map((fill, index) => <tr key={`${fill.order_id}:${fill.timestamp}:${index}`}>
          <td>{session.symbol}</td><td className={fill.side === 'buy' ? 'replay-ledger-buy' : 'replay-ledger-sell'}>{fill.side === 'buy' ? '买入' : '卖出'}</td>
          <td className="replay-ledger-number">{fill.quantity}</td><td className="replay-ledger-number" title={fill.price}>{price(fill.price, tick)}</td>
          <td className="replay-ledger-number" title={fill.fee}>{money(fill.fee)}</td>
          <td><time dateTime={fill.timestamp}>{timeFormat.format(new Date(fill.timestamp))}<span className="replay-ledger-date">{dateFormat.format(new Date(fill.timestamp))}</span></time>{fill.ambiguous && <span className="replay-ledger-annotation" title="同分钟成交路径不明，已按保守规则处理">顺序不明</span>}</td>
        </tr>) : <tr><td colSpan={6} className="replay-ledger-empty">暂无成交记录</td></tr>}</tbody>
      </table></div>
      {!!fills.length && <p className="replay-ledger-footnote">{fills.length} 笔成交 · 最新在前</p>}
    </div>

    <div className="replay-ledger-content" id={`${id}-panel-1`} role="tabpanel" aria-labelledby={`${id}-tab-1`} hidden={activeTab !== 1}>
      <div className="replay-ledger-scroll"><table className="replay-ledger-table" aria-label="持仓">
        <thead><tr><th scope="col">品种 / 方向</th><th scope="col" className="replay-ledger-number">数量 / 手</th><th scope="col" className="replay-ledger-number">均价</th><th scope="col" className="replay-ledger-number">浮动盈亏 / USD</th><th scope="col" className="replay-ledger-number">止盈</th><th scope="col" className="replay-ledger-number">止损</th><th scope="col">操作</th></tr></thead>
        <tbody>{position.quantity ? <tr>
          <td>{session.symbol} <span className={position.quantity > 0 ? 'replay-ledger-buy' : 'replay-ledger-sell'}>{position.quantity > 0 ? '多' : '空'}</span></td>
          <td className="replay-ledger-number">{Math.abs(position.quantity)}</td><td className="replay-ledger-number" title={position.average_price}>{price(position.average_price, tick, true)}</td>
          <td className={`replay-ledger-number ${pnlClass(account.unrealized_pnl)}`}>{money(account.unrealized_pnl, true)}</td>
          <td className="replay-ledger-number">{price(position.take_profit, tick)}</td><td className="replay-ledger-number">{price(position.stop_loss, tick)}</td>
          <td><div className="replay-ledger-actions"><button type="button" aria-expanded={editingProtection} onClick={() => { onPause(); setEditingProtection(open => !open); }}>修改保护价</button>
            <button type="button" disabled={disabled || !canMarketTrade(session)} onClick={() => void command({ type: 'place_order', kind: 'market', reduce_only: true, side: position.quantity > 0 ? 'sell' : 'buy', quantity: Math.abs(position.quantity) })}>提交全部平仓</button></div></td>
        </tr> : <tr><td colSpan={7} className="replay-ledger-empty">当前空仓</td></tr>}</tbody>
      </table></div>
      {!!position.quantity && editingProtection && <ProtectionForm key={`${position.quantity}:${position.stop_loss}:${position.take_profit}`} session={session} disabled={disabled} command={command} onCancel={() => setEditingProtection(false)} />}
      <p className="replay-ledger-footnote">持仓与资金显示最新回放进度。</p>
    </div>

    <div className="replay-ledger-content" id={`${id}-panel-2`} role="tabpanel" aria-labelledby={`${id}-tab-2`} hidden={activeTab !== 2}>
      <div className="replay-ledger-scroll"><table className="replay-ledger-table" aria-label="订单">
        <thead><tr><th scope="col">品种 / 方向</th><th scope="col">类型</th><th scope="col" className="replay-ledger-number">数量 / 手</th><th scope="col" className="replay-ledger-number">限价</th><th scope="col" className="replay-ledger-number">触发价</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
        <tbody>{orders.length ? orders.map(order => <tr key={order.id}>
          <td>{session.symbol} <span className={order.side === 'buy' ? 'replay-ledger-buy' : 'replay-ledger-sell'}>{order.side === 'buy' ? '买入' : '卖出'}</span></td><td>{orderLabels[order.kind]}</td>
          <td className="replay-ledger-number">{order.quantity}</td><td className="replay-ledger-number">{price(order.limit_price, tick)}</td><td className="replay-ledger-number">{price(order.stop_price, tick)}</td>
          <td><span className="replay-ledger-status" data-status={order.status}>{order.status === 'pending' ? (order.triggered_at ? '已触发待成交' : '待成交') : order.status === 'filled' ? '已成交' : '已撤单'}</span></td>
          <td>{order.status === 'pending' ? <div className="replay-ledger-actions">{order.kind !== 'market' && <button type="button" disabled={disabled} onClick={() => onAmend(order)}>改单</button>}<button type="button" disabled={disabled} onClick={() => void command({ type: 'cancel_order', order_id: order.id })}>撤单</button></div> : '—'}</td>
        </tr>) : <tr><td colSpan={7} className="replay-ledger-empty">暂无订单</td></tr>}</tbody>
      </table></div>
    </div>

    <div className="replay-ledger-content" id={`${id}-panel-3`} role="tabpanel" aria-labelledby={`${id}-tab-3`} hidden={activeTab !== 3}>
      {activeTab === 3 && <div className="replay-ledger-statistics"><ReplayResults session={session} events={events} /></div>}
    </div>
    <details className="replay-ledger-rules"><summary>成交规则与费用</summary><p>市价单按当前回放价立即成交 · 限价/条件单从下一分钟开始撮合 · 同分钟路径不明时保守处理 · 费用 {money(config.fee_per_side)} USD/手/边 · 滑点 {config.slippage_ticks} tick</p></details>
  </section>;
}
