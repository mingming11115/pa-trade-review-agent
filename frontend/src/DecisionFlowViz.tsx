import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { formatBarRange } from "./barDisplay";
import type { BarRange } from "./types";

type Terminal = { outcome: string; reason: string; terminal_node: string };
type TraceItem = { node_id: string; question: string; answer: string; reason: string; bar_range?: BarRange | null; source?: "program" | "ai"; skipped?: boolean };
type NodeKind = "program" | "llm" | "validate" | "terminal";
type NodeStatus = "passed" | "failed" | "pending" | "terminal";
type NodeDef = { id: string; title: string; kind: NodeKind; summary: string; x: number; y: number };
type EdgeDef = { from: string; to: string; label?: string; alternate?: boolean };

const NODE_W = 310;
const NODE_H = 112;
const CANVAS_W = 1060;
const CANVAS_H = 1920;
const EMPTY_TRACE: TraceItem[] = [];

const GRAPH_NODES: NodeDef[] = [
  { id: "prepare_context", title: "准备上下文", kind: "program", summary: "记忆 · 行情 · 数据预检 · 可交易性", x: 250, y: 24 },
  { id: "stage1_features", title: "Stage1 特征", kind: "program", summary: "方向 · Always In · 周期/混乱/动量", x: 250, y: 164 },
  { id: "stage1_llm", title: "Stage1 模型", kind: "llm", summary: "诊断与闸门候选", x: 250, y: 304 },
  { id: "stage1_gate_validate", title: "闸门校验", kind: "validate", summary: "闸门一致性", x: 250, y: 444 },
  { id: "stage1_finalize", title: "Stage1 汇总", kind: "program", summary: "gate_result", x: 250, y: 584 },
  { id: "stage1_terminal", title: "Stage1 终止", kind: "terminal", summary: "WAIT / 未放行", x: 650, y: 304 },
  { id: "stage2_context", title: "Stage2 上下文", kind: "program", summary: "策略与几何特征", x: 250, y: 724 },
  { id: "stage2_precheck", title: "Stage2 硬门", kind: "program", summary: "信号 · 风控 · 下单方式", x: 250, y: 864 },
  { id: "stage2_llm", title: "Stage2 模型", kind: "llm", summary: "交易候选与三价", x: 250, y: 1004 },
  { id: "stage2_valid", title: "Stage2 校验", kind: "validate", summary: "交易门禁 / 禁止扫描", x: 250, y: 1144 },
  { id: "stage2_finalize", title: "Stage2 汇总", kind: "program", summary: "Decision / Terminal", x: 250, y: 1284 },
  { id: "stage2_terminal", title: "Stage2 终止", kind: "terminal", summary: "WAIT / REJECT", x: 650, y: 1004 },
];

const GRAPH_EDGES: EdgeDef[] = [
  { from: "prepare_context", to: "stage1_features", label: "预检成功" },
  { from: "prepare_context", to: "stage1_terminal", label: "预检失败", alternate: true },
  { from: "stage1_features", to: "stage1_llm" },
  { from: "stage1_llm", to: "stage1_gate_validate" },
  { from: "stage1_gate_validate", to: "stage1_finalize", label: "校验通过" },
  { from: "stage1_gate_validate", to: "stage1_terminal", label: "校验失败", alternate: true },
  { from: "stage1_finalize", to: "stage2_context", label: "已放行" },
  { from: "stage1_finalize", to: "stage1_terminal", label: "未放行", alternate: true },
  { from: "stage2_context", to: "stage2_precheck" },
  { from: "stage2_precheck", to: "stage2_llm", label: "硬门通过" },
  { from: "stage2_precheck", to: "stage2_terminal", label: "硬门失败", alternate: true },
  { from: "stage2_llm", to: "stage2_valid" },
  { from: "stage2_valid", to: "stage2_finalize", label: "校验通过" },
  { from: "stage2_valid", to: "stage2_terminal", label: "等待 / 拒绝", alternate: true },
];

const KIND_LABEL: Record<NodeKind, string> = { program: "程序", llm: "LLM", validate: "校验", terminal: "终止" };
const STATUS_LABEL: Record<NodeStatus, string> = { passed: "成功", failed: "失败", pending: "未进入", terminal: "终局" };
const ALTERNATE_EDGE_KEYS = new Set(GRAPH_EDGES.filter((edge) => edge.alternate).map((edge) => `${edge.from}→${edge.to}`));

function center(id: string) {
  const node = GRAPH_NODES.find((item) => item.id === id);
  return node ? { x: node.x + NODE_W / 2, y: node.y + NODE_H / 2 } : { x: 0, y: 0 };
}

function edgePath(from: string, to: string) {
  const left = center(from); const right = center(to); const middle = (left.y + right.y) / 2;
  return `M ${left.x} ${left.y} C ${left.x} ${middle}, ${right.x} ${middle}, ${right.x} ${right.y}`;
}

function mappedNodeId(item: TraceItem, phase: "stage1" | "stage2") {
  if (phase === "stage1") {
    if (["program_direction", "always_in", "not_extreme_chaos", "momentum_enough"].includes(item.node_id)) return "stage1_features";
    return "stage1_llm";
  }
  const precheckIds = new Set([
    "signal_bar_quality", "planned_limit", "signal_bar_closed", "signal_direction_ok",
    "signal_not_overlong", "signal_first_entry", "follow_through", "signal_second_entry",
    "entry_bar_strong", "stop_defined", "stop_not_excessive", "trader_equation",
    "order_market", "order_breakout", "order_limit", "order_breakout_entry",
  ]);
  if (precheckIds.has(item.node_id)) return "stage2_precheck";
  return "stage2_llm";
}

function resolveNodeStatus(node: NodeDef, trail: string[], revealed: Set<string>): NodeStatus {
  if (node.kind === "terminal") return revealed.has(node.id) ? "terminal" : "pending";
  if (!revealed.has(node.id)) return "pending";
  const index = trail.lastIndexOf(node.id);
  const next = index >= 0 ? trail[index + 1] : undefined;
  if (next && ALTERNATE_EDGE_KEYS.has(`${node.id}→${next}`)) return "failed";
  return "passed";
}

export function DecisionFlowViz({ graphTrail = EMPTY_TRACE as unknown as string[], gateTrace = EMPTY_TRACE, decisionTrace = EMPTY_TRACE, terminal, formatEvidence }: {
  graphTrail?: string[]; gateTrace?: TraceItem[]; decisionTrace?: TraceItem[]; terminal: Terminal; formatEvidence: (text: string) => string;
}) {
  const visibleTrail = useMemo(() => graphTrail.filter((id, index) => GRAPH_NODES.some((node) => node.id === id) && graphTrail[index - 1] !== id), [graphTrail]);
  const evidenceByNode = useMemo(() => {
    const result = new Map<string, TraceItem[]>();
    for (const [phase, trace] of [["stage1", gateTrace], ["stage2", decisionTrace]] as const) {
      for (const item of trace) {
        const id = mappedNodeId(item, phase);
        result.set(id, [...(result.get(id) ?? []), item]);
      }
    }
    return result;
  }, [decisionTrace, gateTrace]);
  const [visibleCount, setVisibleCount] = useState(visibleTrail.length);
  const [playing, setPlaying] = useState(false);
  const [canvasScale, setCanvasScale] = useState(1);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const viewportRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  useEffect(() => { setVisibleCount(visibleTrail.length); setPlaying(false); }, [visibleTrail]);
  useEffect(() => {
    if (!playing) return;
    if (visibleCount >= visibleTrail.length) { setPlaying(false); return; }
    const timer = window.setTimeout(() => setVisibleCount((value) => value + 1), 420);
    return () => window.clearTimeout(timer);
  }, [playing, visibleCount, visibleTrail.length]);

  const revealed = new Set(visibleTrail.slice(0, visibleCount));
  const activeEdges = new Set(visibleTrail.slice(0, -1).map((id, index) => `${id}→${visibleTrail[index + 1]}`));

  function toggle(id: string) { setExpanded((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); }
  function resetOrigin() { const element = viewportRef.current; if (!element) return; element.scrollLeft = 0; element.scrollTop = 0; }
  function fitView() { const element = viewportRef.current; if (!element) return; setCanvasScale(Math.min(1, Math.max(.25, (element.clientWidth - 32) / CANVAS_W))); resetOrigin(); }
  function pointerDown(event: ReactPointerEvent<HTMLDivElement>) { const element = viewportRef.current; if (!element || (event.target as HTMLElement).closest("button")) return; dragRef.current = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop }; element.setPointerCapture(event.pointerId); }
  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) { const element = viewportRef.current; const drag = dragRef.current; if (!element || !drag) return; element.scrollLeft = drag.left - (event.clientX - drag.x); element.scrollTop = drag.top - (event.clientY - drag.y); }

  return <section className="flow-viz-shell">
    <div className="flow-viz-toolbar"><div><strong>LangGraph 决策路径</strong><small>节点拓扑、执行路径与业务依据合并展示</small></div><div><button onClick={fitView} type="button">适应视图</button><button onClick={resetOrigin} type="button">回到起点</button><button onClick={() => { setVisibleCount(0); setPlaying(true); }} type="button">{playing ? "推演中…" : "▶ 播放路径"}</button></div></div>
    <div className="flow-hud"><span>路径 {Math.min(visibleCount, visibleTrail.length)}/{visibleTrail.length}</span><b>{playing ? "正在回放" : "分析完成"}</b><span>终局 {terminal.outcome.toUpperCase()}</span></div>
    <div aria-label="LangGraph 决策树" className="flow-viz-viewport" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={() => { dragRef.current = null; }} ref={viewportRef}>
      <div className="flow-viz-canvas-wrap" style={{ width: CANVAS_W * canvasScale, height: CANVAS_H * canvasScale }}><div className="flow-viz-canvas" style={{ width: CANVAS_W, height: CANVAS_H, transform: `scale(${canvasScale})` }}>
        <svg className="flow-edges" height={CANVAS_H} width={CANVAS_W}>{GRAPH_EDGES.map((edge) => {
          const key = `${edge.from}→${edge.to}`;
          const active = activeEdges.has(key) && revealed.has(edge.from) && revealed.has(edge.to);
          return <path key={key} className={`flow-edge ${active ? "active" : ""} ${edge.alternate ? "alternate" : ""}`} d={edgePath(edge.from, edge.to)} opacity={active ? 1 : edge.alternate ? .22 : .18} />;
        })}</svg>
        {GRAPH_EDGES.map((edge) => {
          if (!edge.label) return null;
          const key = `${edge.from}→${edge.to}`;
          const active = activeEdges.has(key) && revealed.has(edge.from) && revealed.has(edge.to);
          const a = center(edge.from);
          const b = center(edge.to);
          const vertical = Math.abs(a.x - b.x) < 40;
          const left = edge.alternate ? (a.x + b.x) / 2 : vertical ? Math.min(a.x, b.x) - NODE_W / 2 - 42 : (a.x + b.x) / 2;
          const top = (a.y + b.y) / 2;
          return <span className={`flow-edge-chip ${active ? "active" : "inactive"}${edge.alternate ? " alternate" : ""}`} key={`label-${key}`} style={{ left, top }}>{edge.label}</span>;
        })}
        {GRAPH_NODES.map((node) => {
          const status = resolveNodeStatus(node, visibleTrail.slice(0, visibleCount), revealed);
          const items = evidenceByNode.get(node.id) ?? [];
          const open = expanded.has(node.id);
          const isTerminal = node.kind === "terminal";
          if (isTerminal) {
            return <article className={`flow-terminal ${status === "pending" ? "pending" : "revealed"} outcome-${terminal.outcome}`} key={node.id} style={{ left: node.x, top: node.y, width: NODE_W, minHeight: NODE_H }}>
              <small>{node.title}</small>
              <strong>{status === "pending" ? "—" : terminal.outcome.toUpperCase()}</strong>
              <p>{status === "pending" ? node.summary : formatEvidence(terminal.reason)}</p>
            </article>;
          }
          const statusClass = status === "passed" ? "yes" : status === "failed" ? "no" : "";
          return <article className={`flow-node ${status === "pending" ? "pending" : `revealed ${statusClass}`}${items.length ? " has-evidence" : ""}`} key={node.id} style={{ left: node.x, top: node.y, width: NODE_W, minHeight: NODE_H }}>
            <header><b>{KIND_LABEL[node.kind]}</b><span className={`flow-node-status status-${status}`}>{STATUS_LABEL[status]}</span></header>
            <h4>{node.title}</h4>
            <small>{node.summary}</small>
            {items.length ? <button aria-expanded={open} aria-label={`${node.id} 查看决策依据`} className="flow-evidence-toggle" onClick={() => toggle(node.id)} type="button"><b>{items[0].answer}</b>{items[0].bar_range ? <span>{formatBarRange(items[0].bar_range)}</span> : null}</button> : null}
            {items.length ? <div className={`flow-node-evidence${open ? " expanded" : ""}`}>{items.map((item, index) => <section key={`${item.node_id}-${index}`}><header><b>{item.answer}</b><span>{item.source === "ai" ? "AI" : "程序"}</span></header><p>{formatEvidence(item.question)}</p>{item.bar_range ? <strong>{formatBarRange(item.bar_range)}</strong> : null}<small>{formatEvidence(item.reason || "无明确依据")}</small></section>)}</div> : null}
            <footer><span>{node.id}</span></footer>
          </article>;
        })}
      </div></div>
    </div>
  </section>;
}
