import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useTypewriterText } from "./useTypewriterText";

afterEach(() => {
  vi.useRealTimers();
});

it("reveals a streamed analysis chunk progressively", () => {
  vi.useFakeTimers();
  const { result, rerender } = renderHook(
    ({ text, active }) => useTypewriterText(text, active, 20),
    { initialProps: { text: "", active: true } },
  );

  rerender({ text: "分析结构", active: true });
  expect(result.current).toBe("");

  act(() => vi.advanceTimersByTime(20));
  expect(result.current).toBe("分");

  act(() => vi.advanceTimersByTime(60));
  expect(result.current).toBe("分析结构");
});

it("shows restored analysis immediately when streaming is inactive", () => {
  const { result } = renderHook(() => useTypewriterText("历史思考", false, 20));
  expect(result.current).toBe("历史思考");
});
