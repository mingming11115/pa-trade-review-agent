import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { TradeRecords, type TradeRecordSource } from './TradeRecords';
afterEach(cleanup);

it('switches source with keyboard, including wrapping, while keeping the active panel labelled', () => {
  function Records() {
    const [source, setSource] = useState<TradeRecordSource>('replay');
    return <TradeRecords source={source} onSourceChange={setSource} onClose={() => {}}><p>{source === 'replay' ? '已保存场次' : '实际成交'}</p></TradeRecords>;
  }
  render(<Records />);
  const replay = screen.getByRole('tab', { name: '回放交易' });
  replay.focus();
  fireEvent.keyDown(replay, { key: 'ArrowRight' });
  expect(screen.getByRole('tab', { name: '真实交易' })).toHaveFocus();
  expect(screen.getByRole('tab', { name: '真实交易' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel', { name: '真实交易' })).toHaveTextContent('实际成交');
  fireEvent.keyDown(screen.getByRole('tab', { name: '真实交易' }), { key: 'ArrowRight' });
  expect(replay).toHaveFocus();
  expect(screen.getByRole('tabpanel', { name: '回放交易' })).toHaveTextContent('已保存场次');
});
