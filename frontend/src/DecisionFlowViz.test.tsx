import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DecisionFlowViz } from "./DecisionFlowViz";

const ref = (dayIndex: number, timestamp: string) => ({
  bar_timestamp: timestamp,
  timeframe: "5m",
  session: "CME" as const,
  day_index: dayIndex,
});

const gateTrace = [
  { node_id: "always_in", question: "Always In 是否支持？", answer: "是", reason: "连续收高", bar_range: { start: ref(41, "2026-08-11T13:15:00Z"), end: ref(42, "2026-08-11T13:20:00Z") }, source: "program" as const },
];
const decisionTrace = [
  { node_id: "trader_equation", question: "盈亏比是否合格？", answer: "是", reason: "目标空间充分", bar_range: { start: ref(42, "2026-08-11T13:20:00Z"), end: ref(42, "2026-08-11T13:20:00Z") }, source: "ai" as const },
];

describe("DecisionFlowViz", () => {
  afterEach(cleanup);

  it("keeps the LangGraph topology and attaches Stage 1 and Stage 2 evidence", () => {
    render(
      <DecisionFlowViz
        decisionTrace={decisionTrace}
        formatEvidence={(text) => text}
        gateTrace={gateTrace}
        graphTrail={["prepare_context", "stage1_features", "stage1_llm", "stage1_finalize", "stage2_context", "stage2_precheck", "stage2_llm", "stage2_finalize"]}
        terminal={{ outcome: "trade", reason: "条件满足", terminal_node: "order_breakout" }}
      />,
    );

    expect(screen.getByText("LangGraph 决策路径")).toBeInTheDocument();
    expect(screen.getByText("准备上下文")).toBeInTheDocument();
    expect(screen.getByText("Stage1 特征")).toBeInTheDocument();
    expect(screen.getByText("Stage2 硬门")).toBeInTheDocument();
    expect(screen.getByText("预检成功")).toHaveClass("active");
    expect(screen.getByText("预检失败")).toHaveClass("inactive");
    expect(screen.getAllByText("是").length).toBeGreaterThan(0);
    expect(screen.getByText("Always In 是否支持？")).toBeInTheDocument();
    expect(screen.getByText("盈亏比是否合格？")).toBeInTheDocument();
    expect(screen.getAllByText("连续收高", { exact: false }).length).toBeGreaterThan(0);
  }, 15000);

  it("marks branch failure nodes and keeps unused failure labels inactive", () => {
    render(
      <DecisionFlowViz
        formatEvidence={(text) => text}
        gateTrace={[]}
        graphTrail={["prepare_context", "stage1_terminal"]}
        terminal={{ outcome: "wait", reason: "行情数据无效", terminal_node: "prepare_context" }}
      />,
    );

    expect(screen.getByText("预检失败")).toHaveClass("active");
    expect(screen.getByText("预检成功")).toHaveClass("inactive");
    expect(screen.getByText("失败")).toBeInTheDocument();
  });

  it("expands evidence on its mapped LangGraph node", () => {
    render(
      <DecisionFlowViz
        formatEvidence={(text) => text}
        gateTrace={gateTrace}
        graphTrail={["stage1_features", "stage1_llm", "stage1_finalize"]}
        terminal={{ outcome: "wait", reason: "等待确认", terminal_node: "momentum_enough" }}
      />,
    );

    const node = screen.getByRole("button", { name: /stage1_features/ });
    expect(node).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(node);
    expect(node).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Always In 是否支持？")).toBeInTheDocument();
  });

  it("fits the canvas and returns the viewport to its origin", () => {
    render(<DecisionFlowViz formatEvidence={(text) => text} terminal={{ outcome: "wait", reason: "等待", terminal_node: "momentum_enough" }} />);
    const viewport = screen.getByLabelText("LangGraph 决策树");
    Object.defineProperty(viewport, "clientWidth", { configurable: true, value: 560 });
    Object.defineProperty(viewport, "scrollLeft", { configurable: true, writable: true, value: 120 });
    Object.defineProperty(viewport, "scrollTop", { configurable: true, writable: true, value: 240 });
    fireEvent.click(screen.getByRole("button", { name: "适应视图" }));
    expect(viewport.scrollLeft).toBe(0);
    expect(viewport.scrollTop).toBe(0);
    expect(document.querySelector(".flow-viz-canvas")).toHaveStyle({ transform: `scale(${528 / 1060})` });
    viewport.scrollLeft = 90;
    viewport.scrollTop = 160;
    fireEvent.click(screen.getByRole("button", { name: "回到起点" }));
    expect(viewport.scrollLeft).toBe(0);
    expect(viewport.scrollTop).toBe(0);
  });
});
