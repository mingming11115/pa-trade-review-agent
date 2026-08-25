import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useDialogFocus({ open, onDismiss, dismissible = true }: { open: boolean; onDismiss?: () => void; dismissible?: boolean }): RefObject<HTMLElement | null> {
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const onDismissRef = useRef(onDismiss);
  onDismissRef.current = onDismiss;

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusables = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter((element) => !element.hasAttribute("hidden"));
    queueMicrotask(() => (focusables()[0] ?? dialog)?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && dismissible && onDismissRef.current) {
        event.preventDefault();
        onDismissRef.current();
        window.setTimeout(() => previousFocusRef.current?.focus(), 0);
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusables();
      if (!elements.length) { event.preventDefault(); dialog?.focus(); return; }
      const first = elements[0]; const last = elements[elements.length - 1];
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.setTimeout(() => previousFocusRef.current?.focus(), 0);
    };
  }, [dismissible, open]);

  return dialogRef;
}
