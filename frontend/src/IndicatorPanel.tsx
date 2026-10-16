import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { DEFAULT_INDICATOR_SETTINGS, INDICATOR_PERIODS, isIndicatorSettings, type IndicatorKey, type IndicatorSettings } from './indicatorSettings';
import { INDICATOR_CATALOG } from './indicatorCatalog';
import { useDialogFocus } from './useDialogFocus';
import './indicators.css';

type Props = { settings: IndicatorSettings; onChange: (settings: IndicatorSettings) => void };
const FAVORITES_KEY = 'pa.chart.indicator-favorites.v1';
function loadFavorites(): IndicatorKey[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(FAVORITES_KEY) ?? '[]');
    return Array.isArray(parsed) && parsed.every(key => INDICATOR_CATALOG.some(item => item.key === key)) ? [...new Set(parsed)] : [];
  } catch { return []; }
}
function Dialog({ title, onClose, children, library = false }: { title: string; onClose: () => void; children: ReactNode; library?: boolean }) {
  const dialogRef = useDialogFocus({ open: true, onDismiss: onClose });
  return createPortal(<div className="indicator-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} data-replay-interactive className={`indicator-dialog ${library ? 'indicator-library' : 'indicator-settings'}`}>
      <header className="indicator-heading"><h2>{title}</h2><button className="indicator-close" type="button" aria-label={`关闭${title}`} onClick={onClose}>×</button></header>
      {children}
    </section>
  </div>, document.body);
}

export function IndicatorPanel({ settings, onChange, displayControls }: Props & { displayControls?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('全部');
  const [favorites, setFavorites] = useState<IndicatorKey[]>(loadFavorites);
  const triggerRef = useRef<HTMLButtonElement>(null);
  function toggleFavorite(key: IndicatorKey) {
    const next = favorites.includes(key) ? favorites.filter(item => item !== key) : [...favorites, key];
    setFavorites(next);
    try { window.localStorage.setItem(FAVORITES_KEY, JSON.stringify(next)); } catch { /* Favorites remain usable without storage. */ }
  }
  const query = search.trim().toLowerCase();
  const filtered = INDICATOR_CATALOG.filter(item => (category === '全部' || category === item.category || (category === '收藏' && favorites.includes(item.key)))
    && `${item.name} ${item.description} ${item.aliases}`.toLowerCase().includes(query));
  return <>
    <button ref={triggerRef} type="button" aria-haspopup="dialog" aria-expanded={open} onClick={() => { triggerRef.current?.focus(); setSearch(''); setOpen(true); }}>指标</button>
    {open && <Dialog title="指标库" onClose={() => setOpen(false)} library>
      {displayControls && <fieldset className="indicator-display-controls"><legend>图表显示</legend>{displayControls}</fieldset>}
      <div className="indicator-search"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="10" cy="10" r="6" /><path d="m15 15 5 5" /></svg><input type="search" aria-label="搜索指标" placeholder="搜索指标名称" value={search} onChange={event => setSearch(event.target.value)} /></div>
      <div className="indicator-library-body">
        <nav className="indicator-categories" aria-label="指标分类">{['全部', '收藏', '主图', '副图'].map(item => <button type="button" key={item} aria-pressed={category === item} onClick={() => setCategory(item)}>{item === '收藏' && <span aria-hidden="true">☆ </span>}{item}</button>)}</nav>
        <div className="indicator-results">
          <div className="indicator-list-heading"><span>指标名称</span><span>位置</span></div>
          {filtered.map(item => <div className="indicator-library-row" key={item.key}>
            <button className={`indicator-star ${favorites.includes(item.key) ? 'is-favorite' : ''}`} type="button" aria-label={`${favorites.includes(item.key) ? '取消收藏' : '收藏'} ${item.name}`} aria-pressed={favorites.includes(item.key)} onClick={() => toggleFavorite(item.key)}>{favorites.includes(item.key) ? '★' : '☆'}</button>
            <button className="indicator-add" type="button" aria-label={settings[item.key].enabled ? `${item.name} 已添加` : `添加 ${item.name}`} disabled={settings[item.key].enabled} onClick={() => {
              if (settings[item.key].enabled) return;
              onChange({ ...settings, [item.key]: { ...settings[item.key], enabled: true, visible: true } });
            }}><span className="indicator-name"><strong>{item.name}</strong><span>{item.description}</span></span><span className="indicator-row-status">{settings[item.key].enabled ? '已添加' : item.category}</span></button>
          </div>)}
          {!filtered.length && <p className="indicator-empty">没有匹配的指标</p>}
        </div>
      </div>
      <footer className="indicator-library-note">点击指标立即添加 · 每种指标可添加一次</footer>
    </Dialog>}
  </>;
}

const TABS = ['参数', '样式', '可见性'] as const;
type Tab = typeof TABS[number];
export function IndicatorSettingsDialog({ indicator, settings, onChange, onClose }: Props & { indicator: IndicatorKey; onClose: () => void }) {
  const [draft, setDraft] = useState(() => structuredClone(settings));
  const [tab, setTab] = useState<Tab>('参数');
  const [error, setError] = useState('');
  const id = useId();
  const name = INDICATOR_CATALOG.find(item => item.key === indicator)!.name;
  const entry = draft[indicator];
  function patch(field: string, value: unknown) {
    setDraft(current => ({ ...current, [indicator]: { ...current[indicator], [field]: value } }));
    setError('');
  }
  function numberField(label: string, field: string, value: number, multiplier = false) {
    return <label className="indicator-setting-field"><span>{label}</span><input aria-label={`${name} ${label}`} type="number" min={multiplier ? 0.01 : 1} max={multiplier ? 10 : 500} step={multiplier ? 'any' : 1} value={Number.isNaN(value) ? '' : value} onChange={event => patch(field, event.target.valueAsNumber)} /></label>;
  }
  function colorField(label: string, field: string, value: string) {
    return <label className="indicator-setting-field"><span>{label}</span><input aria-label={`${name} ${label}`} type="color" value={value} onChange={event => patch(field, event.target.value)} /></label>;
  }
  function handleTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : -1) + 3) % 3;
    setTab(TABS[next]);
    document.getElementById(`${id}-tab-${next}`)?.focus();
  }
  return <Dialog title={`${name} 设置`} onClose={onClose}>
    <div className="indicator-tabs" role="tablist" aria-label="设置分类">{TABS.map((item, index) => <button key={item} type="button" role="tab" id={`${id}-tab-${index}`} aria-controls={`${id}-panel-${index}`} aria-selected={tab === item} tabIndex={tab === item ? 0 : -1} onKeyDown={event => handleTabKey(event, index)} onClick={() => setTab(item)}>{item}</button>)}</div>
    <form noValidate onSubmit={event => {
      event.preventDefault();
      const updated = { ...settings, [indicator]: draft[indicator] };
      if (!isIndicatorSettings(updated)) { setError('周期须为 1–500 的整数，BOLL 倍数须大于 0 且不超过 10，MACD 快线周期须小于慢线周期。'); return; }
      onChange(updated); onClose();
    }}>
      <div className="indicator-setting-body" role="tabpanel" id={`${id}-panel-${TABS.indexOf(tab)}`} aria-labelledby={`${id}-tab-${TABS.indexOf(tab)}`}>
        {tab === '参数' && <>
          {'period' in entry && numberField('周期', 'period', entry.period)}
          {indicator === 'boll' && numberField('倍数', 'multiplier', draft.boll.multiplier, true)}
          {indicator === 'macd' && <>{numberField('快线周期', 'fast', draft.macd.fast)}{numberField('慢线周期', 'slow', draft.macd.slow)}{numberField('信号周期', 'signal', draft.macd.signal)}<p className="indicator-help">MACD 柱值为 2 × (DIF − DEA)。</p></>}
          {indicator === 'volume' && <p className="indicator-help">成交量使用每根 K 线的交易量，无需周期参数。</p>}
          {indicator !== 'volume' && <p className="indicator-help">样本不足时，等待足够 K 线后显示。</p>}
        </>}
        {tab === '样式' && <>
          {'color' in entry && colorField('颜色', 'color', entry.color)}
          {indicator === 'boll' && <>{colorField('上轨颜色', 'upperColor', draft.boll.upperColor)}{colorField('下轨颜色', 'lowerColor', draft.boll.lowerColor)}</>}
          {indicator === 'macd' && <>{colorField('DIF 颜色', 'difColor', draft.macd.difColor)}{colorField('DEA 颜色', 'deaColor', draft.macd.deaColor)}{colorField('正柱颜色', 'positiveColor', draft.macd.positiveColor)}{colorField('负柱颜色', 'negativeColor', draft.macd.negativeColor)}</>}
          {indicator === 'volume' && <>{colorField('上涨颜色', 'upColor', draft.volume.upColor)}{colorField('下跌颜色', 'downColor', draft.volume.downColor)}</>}
          {'lineWidth' in entry && <label className="indicator-setting-field"><span>线宽</span><select aria-label={`${name} 线宽`} value={entry.lineWidth} onChange={event => patch('lineWidth', Number(event.target.value))}>{[1, 2, 3, 4].map(width => <option key={width} value={width}>{width} px</option>)}</select></label>}
        </>}
        {tab === '可见性' && <><p className="indicator-help">选择在哪些图表周期显示该指标。</p><div className="indicator-timeframes">{INDICATOR_PERIODS.map(period => <label key={period}><input type="checkbox" checked={entry.timeframes.includes(period)} onChange={event => patch('timeframes', event.target.checked ? [...entry.timeframes, period] : entry.timeframes.filter(value => value !== period))} />{period}</label>)}</div></>}
      </div>
      {error && <p role="alert" className="indicator-error">{error}</p>}
      <footer className="indicator-footer"><button type="button" className="indicator-reset" onClick={() => { setDraft(current => ({ ...current, [indicator]: { ...structuredClone(DEFAULT_INDICATOR_SETTINGS[indicator]), enabled: settings[indicator].enabled } })); setError(''); }}>恢复默认</button><span /><button type="button" onClick={onClose}>取消</button><button type="submit" className="indicator-apply">应用</button></footer>
    </form>
  </Dialog>;
}
