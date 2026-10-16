import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReplayEvents, ReplayFill, ReplayOrder, ReplaySession } from '../types';
import { ReplayTradeDetails } from './ReplayTradeDetails';

afterEach(cleanup);

const session: ReplaySession = {
  id: 'ledger', mode: 'training', symbol: 'ES', status: 'active', revision: 1,
  created_at: '2026-09-30T13:30:00Z',
  config: { instrument: { symbol: 'ES', tick_size: '0.25', multiplier: '50' }, rules_version: 1, start: '2026-09-30T13:30:00Z', end: null },
  state: {
    cursor: '2026-09-30T13:47:00Z', last_price: '5872.25', instrument_id: 1,
    config: { initial_cash: '100000', fee_per_side: '1.5', slippage_ticks: 0 },
    position: { quantity: 1, average_price: '5870.125', entry_fees: '1.5', stop_loss: '5865', take_profit: '5880' },
    account: { cash: '100219', equity: '100331.5', realized_pnl: '225', fees: '6', unrealized_pnl: '112.5', peak_equity: '100419', max_drawdown: '87.5', closed_trades: 2, wins: 2 },
    orders: [],
  },
};
const events: ReplayEvents = { items: [], next_offset: null };
const fill = (order_id: string, timestamp: string, price: string): ReplayFill => ({
  order_id, timestamp, price, side: 'buy', quantity: 1, fee: '1.5', reason: 'market', ambiguous: false, realized_pnl: '0',
});
const order = (overrides: Partial<ReplayOrder> = {}): ReplayOrder => ({
  id: 'pending', side: 'buy', quantity: 2, kind: 'stop_limit', limit_price: '5875.5', stop_price: '5875', triggered_at: session.state.cursor,
  stop_loss: null, take_profit: null, reduce_only: false, created_at: session.state.cursor, status: 'pending', reason: '', ...overrides,
});
function setup(overrides: Partial<Parameters<typeof ReplayTradeDetails>[0]> = {}) {
  const props = { session, events, visibleFills: [] as ReplayFill[], disabled: false, command: vi.fn().mockResolvedValue(true), onAmend: vi.fn(), onPause: vi.fn(), ...overrides };
  return { ...render(<ReplayTradeDetails {...props} />), props };
}
function tab(name: string) { fireEvent.click(screen.getByRole('tab', { name })); }

it('shows one ledger at a time and keeps every empty state discoverable', () => {
  setup({ session: { ...session, state: { ...session.state, position: { ...session.state.position, quantity: 0 } } } });
  expect(screen.getByRole('tab', { name: '成交记录' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByText('暂无成交记录')).toBeVisible();
  tab('持仓');
  expect(screen.getByText('当前空仓')).toBeVisible();
  expect(screen.queryByText('暂无成交记录')).not.toBeVisible();
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
  tab('订单');
  expect(screen.getByText('暂无订单')).toBeVisible();
  tab('统计');
  expect(screen.getByText('最大回撤')).toBeVisible();
  expect(screen.getByText('胜率')).toBeVisible();
});

it('orders fills newest first, preserves equal-time execution order and formats Beijing time and tick precision', () => {
  const visibleFills = [fill('early', '2026-09-30T13:35:00Z', '5864.5'), fill('same-a', '2026-09-30T13:47:00Z', '5870'), { ...fill('same-b', '2026-09-30T13:47:00Z', '5870.25'), side: 'sell' as const, ambiguous: true }];
  setup({ visibleFills });
  const rows = within(screen.getByRole('table', { name: '成交记录' })).getAllByRole('row').slice(1);
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('5,870.00');
  expect(rows[1]).toHaveTextContent('5,870.25');
  expect(rows[2]).toHaveTextContent('5,864.50');
  expect(rows[0]).toHaveTextContent('21:47:00');
  expect(rows[0]).toHaveTextContent('2026-09-30');
  expect(rows[0]).toHaveTextContent('1.50');
  expect(rows[1]).toHaveTextContent('顺序不明');
  expect(visibleFills[0].order_id).toBe('early');
  expect(screen.getByText('100,331.50')).toBeVisible();
  expect(screen.getByText('+225.00')).toBeVisible();
});

it('retains protection edits across tabs and failed submissions, and clears prices with null', async () => {
  const command = vi.fn().mockResolvedValue(false);
  const { props } = setup({ command });
  tab('持仓');
  expect(screen.getByRole('cell', { name: '5,870.125' })).toBeVisible();
  expect(screen.queryByLabelText('持仓止损')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '修改保护价' }));
  expect(props.onPause).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByLabelText('持仓止损'), { target: { value: '5860' } });
  fireEvent.change(screen.getByLabelText('持仓止盈'), { target: { value: '' } });
  tab('成交记录');
  tab('持仓');
  expect(screen.getByLabelText('持仓止损')).toHaveValue(5860);
  fireEvent.click(screen.getByRole('button', { name: '更新保护价' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith({ type: 'update_protection', stop_loss: '5860', take_profit: null }));
  expect(screen.getByLabelText('持仓止损')).toHaveValue(5860);
  expect(screen.getByLabelText('持仓止盈')).toHaveValue(null);
});

it('refreshes protection fields from confirmed server values without resetting the selected tab', () => {
  const { props, rerender } = setup();
  tab('持仓');
  fireEvent.click(screen.getByRole('button', { name: '修改保护价' }));
  fireEvent.change(screen.getByLabelText('持仓止损'), { target: { value: '5860' } });
  const updated = { ...session, revision: 2, state: { ...session.state, position: { ...session.state.position, stop_loss: '5868' } } };
  rerender(<ReplayTradeDetails {...props} session={updated} disabled />);
  expect(screen.getByRole('tab', { name: '持仓' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByLabelText('持仓止损')).toHaveValue(5868);
  expect(screen.getByRole('button', { name: '更新保护价' })).toBeDisabled();
});

it.each([2, -3])('closes the entire %s position with a reduce-only market order', async quantity => {
  const { props } = setup({ session: { ...session, state: { ...session.state, position: { ...session.state.position, quantity } } } });
  tab('持仓');
  fireEvent.click(screen.getByRole('button', { name: '提交全部平仓' }));
  await waitFor(() => expect(props.command).toHaveBeenCalledWith({ type: 'place_order', kind: 'market', reduce_only: true, side: quantity > 0 ? 'sell' : 'buy', quantity: Math.abs(quantity) }));
});

it('keeps order status, amendment and cancellation bound to the correct order', async () => {
  const pending = order();
  const { props, rerender } = setup({ session: { ...session, state: { ...session.state, orders: [pending, order({ id: 'cancelled', status: 'cancelled' }), order({ id: 'filled', status: 'filled' })] } } });
  tab('订单');
  expect(screen.getByText('已触发待成交')).toBeVisible();
  expect(screen.getByText('已成交')).toBeVisible();
  expect(screen.getByText('已撤单')).toBeVisible();
  expect(screen.getAllByRole('button', { name: '改单' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '改单' }));
  expect(props.onAmend).toHaveBeenCalledWith(pending);
  fireEvent.click(screen.getByRole('button', { name: '撤单' }));
  await waitFor(() => expect(props.command).toHaveBeenCalledWith({ type: 'cancel_order', order_id: 'pending' }));
  rerender(<ReplayTradeDetails {...props} disabled />);
  expect(screen.getByRole('button', { name: '改单' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '撤单' })).toBeDisabled();
  tab('持仓');
  expect(screen.getByRole('button', { name: '提交全部平仓' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '修改保护价' }));
  expect(screen.getByLabelText('持仓止损')).toBeDisabled();
  expect(screen.getByRole('button', { name: '更新保护价' })).toBeDisabled();
});

it('supports keyboard tab navigation without exposing inactive panels', () => {
  setup();
  const fillsTab = screen.getByRole('tab', { name: '成交记录' });
  fillsTab.focus();
  fireEvent.keyDown(fillsTab, { key: 'ArrowRight' });
  expect(screen.getByRole('tab', { name: '持仓' })).toHaveFocus();
  expect(screen.getByRole('tabpanel', { name: '持仓' })).toBeVisible();
  fireEvent.keyDown(screen.getByRole('tab', { name: '持仓' }), { key: 'End' });
  expect(screen.getByRole('tab', { name: '统计' })).toHaveFocus();
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
});
