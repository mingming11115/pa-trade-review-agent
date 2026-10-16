import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import './tradeRecords.css';

export type TradeRecordSource = 'replay' | 'real';

interface Props {
  source: TradeRecordSource;
  onSourceChange: (source: TradeRecordSource) => void;
  onClose: () => void;
  children: ReactNode;
}

const sources: Array<{ value: TradeRecordSource; label: string }> = [
  { value: 'replay', label: '回放交易' },
  { value: 'real', label: '真实交易' },
];

export function TradeRecords({ source, onSourceChange, onClose, children }: Props) {
  const id = useId();
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);

  function changeByKeyboard(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === 'ArrowRight' ? (index + 1) % sources.length
      : event.key === 'ArrowLeft' ? (index + sources.length - 1) % sources.length
      : event.key === 'Home' ? 0
      : event.key === 'End' ? sources.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    onSourceChange(sources[next].value);
    tabs.current[next]?.focus();
  }

  return <section className="replay-workspace trade-records" aria-label="交易记录">
    <header className="trade-records-header">
      <div><h2>交易记录</h2><p>查看已保存的回放与真实交易，继续回放或进入分析。</p></div>
      <button onClick={onClose} type="button">返回工作台</button>
    </header>
    <div className="trade-records-tabs" role="tablist" aria-label="交易来源">
      {sources.map(({ value, label }, index) => <button
        key={value}
        id={`${id}-${value}-tab`}
        ref={element => { tabs.current[index] = element; }}
        role="tab"
        type="button"
        aria-selected={source === value}
        aria-controls={`${id}-${value}-panel`}
        tabIndex={source === value ? 0 : -1}
        onClick={() => onSourceChange(value)}
        onKeyDown={event => changeByKeyboard(event, index)}
      >{label}</button>)}
    </div>
    <div className="trade-records-content" role="tabpanel" id={`${id}-${source}-panel`} aria-labelledby={`${id}-${source}-tab`} tabIndex={0}>
      {children}
    </div>
  </section>;
}

export default TradeRecords;
