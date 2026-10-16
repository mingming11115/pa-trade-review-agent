from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analysis.workflow.stage1.adapter import build_stage1_frame
from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.analysis.workflow.stage1.core.prompt_assembler import PromptAssembler, _SYSTEM_PROMPT_CACHE
from app.core.errors import AppError
from app.core.models import Bar


def frame():
    return build_stage1_frame([
        Bar(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            open=100+i, high=103+i, low=99+i, close=102+i, volume=10)
        for i in range(40)
    ], 'ES', '1m')


def contents():
    return {p.name: p.read_text(encoding='utf-8') for p in PROMPT_DIR.glob('*.txt')}


@pytest.mark.parametrize('direction,cycle,full', [
    ('bullish', 'normal_channel', False), ('bearish', 'normal_channel', False),
    ('neutral', 'trading_range', False), ('bullish', 'normal_channel', True),
])
def test_snapshot_messages_match_files(direction, cycle, full):
    data = frame()
    settings = SimpleNamespace(stage2_load_full_strategy_library=full)
    old = PromptAssembler(PROMPT_DIR, prompt_settings=settings)
    new = PromptAssembler(PROMPT_DIR, prompt_settings=settings, prompt_contents=contents())
    assert new.build_stage1(data) == old.build_stage1(data)
    stage1 = {'direction': direction, 'cycle_position': cycle, 'detected_patterns': []}
    files = ['上涨通道分析识别.txt'] if direction == 'bullish' else ['震荡区间交易策略.txt']
    assert new.build_stage2(data, stage1, files, []) == old.build_stage2(data, stage1, files, [])


def test_missing_snapshot_entry_fails_without_file_fallback():
    with pytest.raises(AppError) as error:
        PromptAssembler(PROMPT_DIR, prompt_contents={}).build_stage1(frame())
    assert error.value.code == 'knowledge_prompt_missing'


def test_cache_isolates_content():
    a, b = contents(), contents()
    a['提示词大纲_人设与思维方式.txt'] = '用户 A 独立人设'
    b['提示词大纲_人设与思维方式.txt'] = '用户 B 独立人设'
    for source, included, excluded in [(a, '用户 A', '用户 B'), (b, '用户 B', '用户 A'), (a, '用户 A', '用户 B')]:
        messages = PromptAssembler(PROMPT_DIR, prompt_contents=source).build_stage1(frame())
        assert included in messages[0]['content'] and excluded not in messages[0]['content']


def test_cache_bound():
    _SYSTEM_PROMPT_CACHE.clear()
    source = contents()
    for i in range(70):
        source['提示词大纲_人设与思维方式.txt'] = f'独立人设{i}'
        PromptAssembler(PROMPT_DIR, prompt_contents=source).build_stage1(frame())
    assert len(_SYSTEM_PROMPT_CACHE) <= 64
