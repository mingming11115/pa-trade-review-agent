import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import './replayStartMenu.css';

type Props = {
  disabled: boolean;
  barDisabled: boolean;
  randomDisabled: boolean;
  randomTitle: string;
  onBar: () => void;
  onDate: () => void;
  onRandom: () => void;
};

export function ReplayStartMenu({ disabled, barDisabled, randomDisabled, randomTitle, onBar, onDate, onRandom }: Props) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const focusLast = useRef(false);
  const expanded = open && !disabled;
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useEffect(() => {
    if (!expanded) return;
    const items = container.current?.querySelectorAll<HTMLButtonElement>('[role=menuitem]:not(:disabled)');
    items?.[focusLast.current ? items.length - 1 : 0]?.focus();
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [expanded]);

  function close() { setOpen(false); trigger.current?.focus(); }
  function select(action: () => void) { close(); action(); }
  function keydown(event: KeyboardEvent<HTMLDivElement>) {
    if (!expanded) return;
    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); }
      close();
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const items = [...(container.current?.querySelectorAll<HTMLButtonElement>('[role=menuitem]:not(:disabled)') ?? [])];
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  }

  return <div className="replay-start-menu" ref={container} onKeyDown={keydown}>
    <button ref={trigger} type="button" aria-haspopup="menu" aria-expanded={expanded} disabled={disabled}
      onKeyDown={event => {
        if (!expanded && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
          event.preventDefault(); focusLast.current = event.key === 'ArrowUp'; setOpen(true);
        }
      }}
      onClick={() => { focusLast.current = false; setOpen(value => !value); }}>选择起点 <span aria-hidden="true">▾</span></button>
    {expanded && <div role="menu" aria-label="回放起点方式">
      <button role="menuitem" type="button" disabled={barDisabled} onClick={() => select(onBar)}>选择 K 线</button>
      <button role="menuitem" type="button" onClick={() => select(onDate)}>选择日期…</button>
      <button role="menuitem" type="button" disabled={randomDisabled} title={randomTitle} onClick={() => select(onRandom)}>随机 K 线</button>
    </div>}
  </div>;
}
