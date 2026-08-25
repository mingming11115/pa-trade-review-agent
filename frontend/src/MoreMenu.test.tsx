import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MoreMenu } from "./MoreMenu";

describe("MoreMenu", () => {
  afterEach(cleanup);

  it("opens a semantic menu and invokes an item", () => {
    const onSelect = vi.fn();
    render(<MoreMenu items={[{ label: "交易日志", onSelect }]} />);
    const trigger = screen.getByRole("button", { name: "更多" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("menuitem", { name: "交易日志" }));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes on outside click and restores trigger focus on Escape", () => {
    render(<div><MoreMenu items={[{ label: "个人中心", onSelect: vi.fn() }]} /><button>外部</button></div>);
    const trigger = screen.getByRole("button", { name: "更多" });
    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByRole("button", { name: "外部" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
