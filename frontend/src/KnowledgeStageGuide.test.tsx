import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { KnowledgeStageGuide } from './KnowledgeStageGuide';
import type { OrchestrationView } from './types';

const view: OrchestrationView = {
  stages:[
    { id:'snapshot', name:'行情快照', kind:'deterministic', description:'准备行情', prompt_files:[] },
    { id:'stage1', name:'Stage 1 · 市场诊断', kind:'llm', description:'识别方向和结构', prompt_files:[{ filename:'共同规则.txt', placement:'system', condition:'每次固定加载', editable:true }] },
    { id:'stage2', name:'Stage 2 · 交易决策', kind:'llm', description:'选择交易策略', prompt_files:[{ filename:'共同规则.txt', placement:'system', condition:'共享规则', editable:true }, { filename:'上涨通道分析识别.txt', placement:'user', condition:'按诊断结果加载', editable:true }] },
  ], edges:[],
};

describe('KnowledgeStageGuide', () => {
  afterEach(cleanup);
  it('shows a dedicated guide with unique totals and the first analysis stage', () => {
    render(<KnowledgeStageGuide view={view} />);
    expect(screen.getByText('3 个阶段 · 2 份提示词')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name:'Stage 1 · 市场诊断' })).toBeInTheDocument();
    const table = screen.getByRole('table', { name:'本阶段的提示词与加载条件' });
    expect(within(table).getByText('共同规则.txt')).toBeInTheDocument();
    expect(within(table).getByText('系统提示词')).toBeInTheDocument();
    expect(within(table).getByText('每次固定加载')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name:/Stage 2 · 交易决策/ }));
    expect(screen.getByRole('heading', { name:'Stage 2 · 交易决策' })).toBeInTheDocument();
    const stage2Table = screen.getByRole('table', { name:'本阶段的提示词与加载条件' });
    expect(within(stage2Table).getByText('上涨通道分析识别.txt')).toBeInTheDocument();
    expect(within(stage2Table).getByText('按诊断结果加载')).toBeInTheDocument();
  });
  it('explains stages that do not load prompt files', () => {
    render(<KnowledgeStageGuide view={view} />);
    fireEvent.click(screen.getByRole('button', { name:/行情快照/ }));
    expect(screen.getByText('该阶段由程序处理，不加载提示词文件。')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
