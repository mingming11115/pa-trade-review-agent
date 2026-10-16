import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { KnowledgePromptBrowser } from './KnowledgePromptBrowser';
import type { OrchestrationView, PromptFileRef } from './types';

const files: PromptFileRef[] = Array.from({ length:29 }, (_, i) => ({
  filename:i === 28 ? '上涨通道分析识别.txt' : `提示词${i + 1}.txt`,
  placement:i < 3 ? 'system' : 'user', condition:'按阶段加载', editable:true,
}));
const view: OrchestrationView = {
  stages:[
    { id:'snapshot', name:'行情快照', kind:'deterministic', description:'指标计算', prompt_files:[] },
    { id:'stage1', name:'Stage 1 · 市场诊断', kind:'llm', description:'市场诊断', prompt_files:files.slice(0, 4) },
    { id:'stage2', name:'Stage 2 · 交易决策', kind:'llm', description:'策略分析', prompt_files:files },
  ], edges:[],
};

describe('KnowledgePromptBrowser', () => {
  afterEach(cleanup);

  it('shows all 29 distinct choices without squeezing stage explanations into the list', () => {
    const select = vi.fn();
    render(<KnowledgePromptBrowser view={view} selectedFilename={files[0].filename} busy={false} onSelect={select} />);
    expect(screen.getByText('共 29 份')).toBeInTheDocument();
    const list = screen.getByRole('navigation', { name:'提示词文件列表' });
    expect(within(list).getAllByRole('button')).toHaveLength(29);
    const selected = within(list).getByRole('button', { name:/提示词1.txt/ });
    expect(selected).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('阶段与加载说明')).not.toBeInTheDocument();
    fireEvent.click(within(list).getByRole('button', { name:/上涨通道分析识别.txt/ }));
    expect(select).toHaveBeenCalledWith('上涨通道分析识别.txt');
  });

  it('searches file names and handles zero matches without changing the selection', () => {
    const select = vi.fn();
    render(<KnowledgePromptBrowser view={view} selectedFilename={files[0].filename} busy={false} onSelect={select} />);
    const search = screen.getByRole('searchbox', { name:'搜索提示词' });
    fireEvent.change(search, { target:{ value:'上涨通道' } });
    const list = screen.getByRole('navigation', { name:'提示词文件列表' });
    expect(within(list).getAllByRole('button')).toHaveLength(1);
    expect(screen.getByText('1 / 29 份')).toBeInTheDocument();
    fireEvent.change(search, { target:{ value:'不存在的文件' } });
    expect(screen.getByText('没有匹配的提示词')).toBeInTheDocument();
    expect(select).not.toHaveBeenCalled();
    fireEvent.change(search, { target:{ value:'  ' } });
    expect(within(list).getAllByRole('button')).toHaveLength(29);
  });

  it('makes the file list reachable by keyboard for scrolling', () => {
    render(<KnowledgePromptBrowser view={view} selectedFilename={files[0].filename} busy={false} onSelect={vi.fn()} />);
    const list = screen.getByRole('navigation', { name:'提示词文件列表' });
    expect(list.tabIndex).toBe(0);
    list.focus();
    expect(list).toHaveFocus();
  });

  it('prevents file switching during database operations', () => {
    const select = vi.fn();
    render(<KnowledgePromptBrowser view={view} selectedFilename={null} busy onSelect={select} />);
    const list = screen.getByRole('navigation', { name:'提示词文件列表' });
    const buttons = within(list).getAllByRole('button');
    expect(buttons.every(button => (button as HTMLButtonElement).disabled)).toBe(true);
    fireEvent.click(buttons[0]);
    expect(select).not.toHaveBeenCalled();
  });
});
