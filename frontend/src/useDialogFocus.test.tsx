import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { useDialogFocus } from "./useDialogFocus";

function Harness({ dismissible = true, onDismiss = vi.fn() }: { dismissible?: boolean; onDismiss?: () => void }) {
  const [open, setOpen] = useState(false);
  const dialogRef = useDialogFocus({ open, dismissible, onDismiss: () => { onDismiss(); setOpen(false); } });
  return <><button onClick={() => setOpen(true)}>打开</button>{open && <section aria-label="测试弹窗" aria-modal="true" ref={dialogRef} role="dialog"><button>第一个</button><button>最后一个</button></section>}</>;
}

describe("useDialogFocus", () => {
  afterEach(cleanup);

  it("focuses, traps Tab, dismisses with Escape, and restores focus", async () => {
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "打开" });
    trigger.focus();
    fireEvent.click(trigger);
    const first = screen.getByRole("button", { name: "第一个" });
    const last = screen.getByRole("button", { name: "最后一个" });
    await waitFor(() => expect(first).toHaveFocus());
    last.focus(); fireEvent.keyDown(document, { key: "Tab" }); expect(first).toHaveFocus();
    first.focus(); fireEvent.keyDown(document, { key: "Tab", shiftKey: true }); expect(last).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("does not dismiss a non-dismissible dialog", async () => {
    const onDismiss = vi.fn();
    render(<Harness dismissible={false} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "打开" }));
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onDismiss).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
