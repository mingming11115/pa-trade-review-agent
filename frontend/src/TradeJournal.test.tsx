import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { Trade } from './types';
import { TradeJournal } from './TradeJournal';
afterEach(cleanup);

const trade: Trade = {
  id: 'es', source_trade_id: 'order-es', contract_name: 'ESZ6', symbol_root: 'ES',
  entered_at: '2026-10-02T22:05:00Z', exited_at: '2026-10-02T22:10:00Z',
  entry_price: '6800.25', exit_price: '6802.50', direction: 'long', size: '2', reported_pnl: '225.00',
  source_file_name: 'trades.csv', imported_at: '2026-10-03T01:00:00Z',
  strategy: 'Opening range', account: 'Account A', tags: ['突破'], notes: '等回踩',
};
const nq: Trade = { ...trade, id: 'nq', contract_name: 'NQZ6', symbol_root: 'NQ', strategy: 'Trend', account: 'Account B', tags: ['趋势'], notes: '等待确认' };
const base = {
  trades: [trade, nq], imports: [], loading: false, error: '', query: '', onQueryChange: vi.fn(),
  onRefresh: vi.fn(), onImport: vi.fn(), onAdd: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(),
  onReview: vi.fn(), onReplay: vi.fn(), onReplayHistory: vi.fn(),
};

it.each(['esz6', 'OPENING RANGE', 'Account A', '突破', '等回踩'])('searches real trades by %s without changing their original values', query => {
  render(<TradeJournal {...base} query={query} />);
  expect(screen.getByText('ESZ6')).toBeInTheDocument();
  expect(screen.queryByText('NQZ6')).not.toBeInTheDocument();
  expect(screen.getByText('6800.25')).toBeInTheDocument();
  expect(screen.getByText('6802.50')).toBeInTheDocument();
  expect(screen.getByText('225.00')).toBeInTheDocument();
  expect(screen.getByText('2026/10/03 06:05:00')).toBeInTheDocument();
});

it('shows search no-match separately from empty and suppresses false empty states during load or failure', () => {
  const { rerender } = render(<TradeJournal {...base} query="missing" />);
  expect(screen.getByText('没有匹配的交易记录')).toBeInTheDocument();
  rerender(<TradeJournal {...base} trades={[]} loading />);
  expect(screen.getByRole('status')).toHaveTextContent('正在加载交易记录');
  expect(screen.queryByText('暂无真实交易记录')).not.toBeInTheDocument();
  rerender(<TradeJournal {...base} trades={[]} error="读取失败" />);
  expect(screen.getByRole('alert')).toHaveTextContent('读取失败');
  expect(screen.queryByText('暂无真实交易记录')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试加载' }));
  expect(base.onRefresh).toHaveBeenCalledOnce();
  rerender(<TradeJournal {...base} trades={[]} />);
  expect(screen.getByText('暂无真实交易记录')).toBeInTheDocument();
});

it('uses the selected row for replay and updates the filtered list when search changes', () => {
  function Journal() {
    const [query, setQuery] = useState('');
    const [selection, setSelection] = useState('');
    return <><TradeJournal {...base} query={query} onQueryChange={setQuery} onReplay={selected => setSelection(selected.id)} /><output>{selection}</output></>;
  }
  render(<Journal />);
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索交易记录' }), { target: { value: 'NQZ6' } });
  expect(screen.queryByText('ESZ6')).not.toBeInTheDocument();
  fireEvent.click(within(screen.getByRole('row', { name: /NQZ6/ })).getByRole('button', { name: '回放' }));
  expect(screen.getByRole('status')).toHaveTextContent('nq');
});
