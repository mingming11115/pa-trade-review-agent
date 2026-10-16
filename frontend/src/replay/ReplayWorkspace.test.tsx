import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ReplayWorkspace } from './ReplayWorkspace';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('creates a training session with explicit costs and UTC time', async () => {
  const requests: Array<{url:string; body:unknown}> = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({url, body:init?.body ? JSON.parse(String(init.body)):null});
    if (init?.method === 'POST') return new Response(JSON.stringify({message:'模拟创建错误，保留输入'}), {status:422});
    return new Response(JSON.stringify({items:[]}));
  }));
  render(<ReplayWorkspace mode="training" onClose={() => {}} />);
  await screen.findByText('暂无训练记录');
  fireEvent.change(screen.getByLabelText('开始时间（北京时间）'), {target:{value:'2025-01-01T08:00'}});
  fireEvent.change(screen.getByLabelText('每手每边手续费'), {target:{value:'1.25'}});
  fireEvent.click(screen.getByRole('button', {name:'开始训练'}));
  await screen.findByText('模拟创建错误，保留输入');
  const body = requests.find(r => r.body)?.body;
  expect(body).toMatchObject({mode:'training', start:'2025-01-01T00:00:00.000Z', config:{initial_cash:'100000', fee_per_side:'1.25', slippage_ticks:0}});
});

it('historical replay selects real trades without exposing a trading ticket', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url:string) => new Response(JSON.stringify(url.includes('trades/recent') ? [{
    id:'one',symbol_root:'ES',contract_name:'ESH5',direction:'long',entered_at:'2025-01-01T00:00:00Z',exited_at:'2025-01-01T00:01:00Z',entry_price:'100',exit_price:'101',size:'1',
  }] : {items:[]}))));
  render(<ReplayWorkspace mode="historical" initialTradeIds={['one']} onClose={() => {}} />);
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeChecked());
  expect(screen.queryByText('下单')).not.toBeInTheDocument();
  expect(screen.getByText(/ESH5/)).toBeInTheDocument();
});

it('returns to unified records and directs an empty real-trade list to its import source', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url:string) => Response.json(url.includes('trades/recent') ? [] : {items:[]})));
  const onClose=vi.fn();
  render(<ReplayWorkspace mode="historical" closeLabel="返回交易记录" onClose={onClose} />);
  expect(await screen.findByText(/交易记录 → 真实交易/)).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'返回工作台'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'返回交易记录'}));
  expect(onClose).toHaveBeenCalledTimes(1);
});
