import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ReplayStartMenu } from './ReplayStartMenu';

afterEach(cleanup);
function props() {
  return { disabled: false, barDisabled: false, randomDisabled: false, randomTitle: '从本地行情随机选择',
    onBar: vi.fn(), onDate: vi.fn(), onRandom: vi.fn() };
}

it.each([['选择 K 线', 'onBar'], ['选择日期…', 'onDate'], ['随机 K 线', 'onRandom']] as const)('dispatches %s and closes the shared menu', (name, callback) => {
  const handlers = props();
  render(<ReplayStartMenu {...handlers} />);
  const trigger = screen.getByRole('button', { name: '选择起点' });
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole('menuitem', { name }));
  expect(handlers[callback]).toHaveBeenCalledOnce();
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('skips unavailable choices for keyboard selection and restores focus on Escape', () => {
  render(<ReplayStartMenu {...props()} barDisabled randomDisabled />);
  const trigger = screen.getByRole('button', { name: '选择起点' });
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  const date = screen.getByRole('menuitem', { name: '选择日期…' });
  expect(date).toHaveFocus();
  expect(screen.getByRole('menuitem', { name: '选择 K 线' })).toBeDisabled();
  expect(screen.getByRole('menuitem', { name: '随机 K 线' })).toBeDisabled();
  fireEvent.keyDown(date, { key: 'ArrowUp' });
  expect(date).toHaveFocus();
  fireEvent.keyDown(date, { key: 'Escape' });
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('closes on outside pointer, Tab and busy state without activating a choice', () => {
  const handlers = props();
  const view = render(<ReplayStartMenu {...handlers} />);
  const open = () => fireEvent.click(screen.getByRole('button', { name: '选择起点' }));
  open(); fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  open(); fireEvent.keyDown(screen.getByRole('menu'), { key: 'Tab' });
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  open(); view.rerender(<ReplayStartMenu {...handlers} disabled />);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '选择起点' })).toBeDisabled();
  view.rerender(<ReplayStartMenu {...handlers} />);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(handlers.onBar).not.toHaveBeenCalled();
  expect(handlers.onDate).not.toHaveBeenCalled();
  expect(handlers.onRandom).not.toHaveBeenCalled();
});
