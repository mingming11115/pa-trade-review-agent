import { useEffect, useId, useRef, useState } from "react";

export interface MoreMenuItem {
  label: string;
  onSelect: () => void;
  hidden?: boolean;
  disabled?: boolean;
  danger?: boolean;
}

export function MoreMenu({ items }: { items: MoreMenuItem[] }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: globalThis.PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const visibleItems = items.filter((item) => !item.hidden);
  return <div className="more-menu" ref={containerRef}>
    <button aria-controls={menuId} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((value) => !value)} ref={triggerRef} type="button">更多 <span aria-hidden="true">⌄</span></button>
    {open && <div aria-label="更多功能" className="more-menu-popover" id={menuId} role="menu">
      {visibleItems.map((item) => <button className={item.danger ? "danger" : ""} disabled={item.disabled} key={item.label} onClick={() => { setOpen(false); item.onSelect(); }} role="menuitem" type="button">{item.label}</button>)}
    </div>}
  </div>;
}
