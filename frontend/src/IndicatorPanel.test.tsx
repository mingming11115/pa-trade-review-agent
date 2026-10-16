import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IndicatorPanel, IndicatorSettingsDialog } from './IndicatorPanel';
import { loadIndicatorSettings, type IndicatorKey, type IndicatorSettings } from './indicatorSettings';

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value) });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Library() {
  const [settings, setSettings] = useState(loadIndicatorSettings);
  return <IndicatorPanel settings={settings} onChange={setSettings} />;
}
function openLibrary() { render(<Library />); fireEvent.click(screen.getByRole('button', { name: '指标' })); }
function openSettings(indicator: IndicatorKey = 'ma', settings = loadIndicatorSettings()) {
  const onChange = vi.fn(); const onClose = vi.fn();
  render(<IndicatorSettingsDialog indicator={indicator} settings={settings} onChange={onChange} onClose={onClose} />);
  return { onChange, onClose };
}

describe('indicator library', () => {
  it('adds an indicator immediately and prevents duplicate instances', () => {
    openLibrary();
    expect(screen.getByRole('dialog', { name: '指标库' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加 MA' }));
    expect(screen.getByRole('button', { name: 'MA 已添加' })).toBeDisabled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
  it('searches Chinese and English names and filters categories', () => {
    openLibrary();
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索指标' }), { target: { value: '布林' } });
    expect(screen.getByRole('button', { name: '添加 BOLL' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '添加 MACD' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'relative strength' } });
    expect(screen.getByRole('button', { name: '添加 RSI' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '副图' }));
    expect(screen.getByRole('button', { name: '添加 MACD' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '添加 MA' })).not.toBeInTheDocument();
  });
  it('remembers favorites independently of added indicators', () => {
    openLibrary();
    fireEvent.click(screen.getByRole('button', { name: '收藏 MA' }));
    cleanup();
    openLibrary();
    fireEvent.click(screen.getByRole('button', { name: '收藏' }));
    expect(screen.getByRole('button', { name: '添加 MA' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '添加 MACD' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消收藏 MA' }));
    expect(screen.getByText('没有匹配的指标')).toBeInTheDocument();
  });
  it('keeps favorites usable with invalid or restricted storage', () => {
    vi.stubGlobal('localStorage', { getItem: () => 'not json', setItem: () => { throw new Error('denied'); } });
    openLibrary();
    fireEvent.click(screen.getByRole('button', { name: '收藏 MA' }));
    expect(screen.getByRole('button', { name: '取消收藏 MA' })).toBeInTheDocument();
  });
  it('adds a hidden disabled indicator with its current parameters made visible', () => {
    const settings = loadIndicatorSettings(); settings.ma.visible = false; settings.ma.period = 50;
    const onChange = vi.fn();
    render(<IndicatorPanel settings={settings} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: '指标' }));
    fireEvent.click(screen.getByRole('button', { name: '添加 MA' }));
    expect(onChange.mock.calls[0][0].ma).toMatchObject({ enabled: true, visible: true, period: 50 });
  });
  it('closes on Escape and restores trigger focus', async () => {
    render(<Library />);
    const trigger = screen.getByRole('button', { name: '指标' });
    trigger.focus(); fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});

describe('single indicator settings', () => {
  it('applies parameter, style and timeframe changes while preserving other indicators', () => {
    const settings = loadIndicatorSettings(); settings.ma.enabled = true;
    const { onChange, onClose } = openSettings('ma', settings);
    fireEvent.change(screen.getByLabelText('MA 周期'), { target: { value: '50' } });
    fireEvent.click(screen.getByRole('tab', { name: '样式' }));
    fireEvent.change(screen.getByLabelText('MA 颜色'), { target: { value: '#123abc' } });
    fireEvent.change(screen.getByLabelText('MA 线宽'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('tab', { name: '可见性' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '5m' }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    const applied: IndicatorSettings = onChange.mock.calls[0][0];
    expect(applied.ma).toMatchObject({ enabled: true, period: 50, color: '#123abc', lineWidth: 4 });
    expect(applied.ma.timeframes).not.toContain('5m');
    expect(applied.ema).toEqual(settings.ema);
    expect(onClose).toHaveBeenCalledOnce();
  });
  it.each([['MA 周期', '0'], ['MA 周期', '1.5'], ['MA 周期', '']])('rejects invalid %s %s', (label, value) => {
    const { onChange } = openSettings();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
  it('rejects reversed MACD periods', () => {
    const { onChange } = openSettings('macd');
    fireEvent.change(screen.getByLabelText('MACD 快线周期'), { target: { value: '26' } });
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
  it('restores only the current indicator defaults and retains its added state', () => {
    const settings = loadIndicatorSettings(); settings.ma.enabled = true; settings.ma.period = 50; settings.ema.period = 30;
    const { onChange } = openSettings('ma', settings);
    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));
    expect(screen.getByLabelText('MA 周期')).toHaveValue(20);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    expect(onChange.mock.calls[0][0].ma.enabled).toBe(true);
    expect(onChange.mock.calls[0][0].ema.period).toBe(30);
  });
  it.each(['取消', 'Escape'])('discards drafts on %s', method => {
    const { onChange, onClose } = openSettings();
    fireEvent.change(screen.getByLabelText('MA 周期'), { target: { value: '50' } });
    if (method === 'Escape') fireEvent.keyDown(document, { key: 'Escape' });
    else fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledOnce();
  });
  it.each([
    ['boll', ['BOLL 颜色', 'BOLL 上轨颜色', 'BOLL 下轨颜色', 'BOLL 线宽']],
    ['macd', ['MACD DIF 颜色', 'MACD DEA 颜色', 'MACD 正柱颜色', 'MACD 负柱颜色', 'MACD 线宽']],
    ['volume', ['VOL 上涨颜色', 'VOL 下跌颜色']],
  ] as const)('exposes actual style controls for %s', (key, labels) => {
    const { onChange } = openSettings(key);
    fireEvent.click(screen.getByRole('tab', { name: '样式' }));
    for (const label of labels) {
      fireEvent.change(screen.getByLabelText(label), { target: { value: label.endsWith('线宽') ? '3' : '#123abc' } });
    }
    fireEvent.click(screen.getByRole('button', { name: '应用' }));
    const entry = onChange.mock.calls[0][0][key];
    expect(Object.values(entry)).toContain('#123abc');
    if (key !== 'volume') expect(entry.lineWidth).toBe(3);
  });
});
