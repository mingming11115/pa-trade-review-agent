"""Tests for post-analysis follow-up chat."""
from __future__ import annotations

from datetime import datetime, timezone

import anyio
import pytest
from fastapi.testclient import TestClient

from app.followup.service import (
    FollowupRequest,
    FollowupSessionStore,
    _followup_compaction_settings,
    _compact_followup_history,
    _split_followup_history,
    append_followup_messages,
    build_decision_recall,
    build_user_turn_content,
    ensure_followup_session,
    load_followup_messages,
    seed_followup_session,
    stream_followup_turn,
)
from app.main import app
from app.core.errors import AppError
from app.core.models import Bar


def _sample_result() -> dict:
    return {
        "run_id": "aid-1",
        "resolved_symbol": "ESU2",
        "query": {
            "symbol": "ES",
            "period": "5m",
            "start": "2022-06-06T00:00:00+00:00",
            "end": "2022-06-06T01:00:00+00:00",
            "analysis_mode": "historical",
        },
        "stage1": {
            "direction": "bullish",
            "cycle_position": "normal_channel",
            "gate_result": "proceed",
            "confidence": 0.72,
            "detected_patterns": ["h2"],
            "support_levels": [100.0],
            "resistance_levels": [105.0],
        },
        "stage2": {
            "decision": {
                "order_type": "限价单",
                "direction": "long",
                "entry_price": 101.5,
                "stop_loss_price": 99.0,
                "take_profit_price": 104.0,
                "take_profit_price_2": 106.0,
                "entry_reason": "H2 回调后继续上涨",
            },
            "terminal": {"outcome": "trade", "reason": "结构允许做多", "terminal_node": "prohibition_scan"},
        },
    }


def _bars() -> list[Bar]:
    return [
        Bar(timestamp=datetime(2022, 6, 6, 0, 0, tzinfo=timezone.utc), open=100, high=101, low=99.5, close=100.5, volume=10),
        Bar(timestamp=datetime(2022, 6, 6, 0, 5, tzinfo=timezone.utc), open=100.5, high=102, low=100.2, close=101.8, volume=12),
        Bar(timestamp=datetime(2022, 6, 6, 0, 10, tzinfo=timezone.utc), open=101.8, high=103, low=101.0, close=101.2, volume=11),
    ]


def _turn(index: int) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": f"q{index}"},
        {"role": "assistant", "content": f"a{index}"},
    ]


def test_split_followup_history_does_not_compact_at_threshold() -> None:
    messages = seed_followup_session("aid-1", _sample_result()).messages
    for index in range(10):
        messages.extend(_turn(index))
    assert _split_followup_history(messages, compact_after_turns=10, keep_recent_turns=4) is None


def test_split_followup_history_preserves_prefix_and_recent_complete_turns() -> None:
    messages = seed_followup_session("aid-1", _sample_result()).messages
    prefix = [dict(message) for message in messages]
    for index in range(11):
        messages.extend(_turn(index))

    split = _split_followup_history(messages, compact_after_turns=10, keep_recent_turns=4)

    assert split is not None
    actual_prefix, old_history, recent = split
    assert actual_prefix == prefix
    assert old_history == [message for index in range(7) for message in _turn(index)]
    assert recent == [message for index in range(7, 11) for message in _turn(index)]


@pytest.mark.parametrize("threshold,keep", [("bad", "4"), ("0", "4"), ("10", "10")])
def test_followup_compaction_settings_fall_back_for_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    threshold: str,
    keep: str,
) -> None:
    monkeypatch.setenv("FOLLOWUP_COMPACT_AFTER_TURNS", threshold)
    monkeypatch.setenv("FOLLOWUP_KEEP_RECENT_TURNS", keep)
    assert _followup_compaction_settings() == (10, 4)


class _RecordingFollowupStore(FollowupSessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.persisted_messages: list[dict[str, str]] | None = None

    async def put_and_persist(self, session):
        self.put(session)
        self.persisted_messages = [dict(message) for message in session.messages]
        return session


def test_compact_followup_history_replaces_old_turns_with_one_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = seed_followup_session("aid-1", _sample_result())
    original_prefix = [dict(message) for message in session.messages]
    for index in range(11):
        session.messages.extend(_turn(index))
    original_messages = [dict(message) for message in session.messages]
    store = _RecordingFollowupStore()

    async def fake_stream(_messages):
        yield "仓位与风控摘要"

    monkeypatch.setattr("app.followup.service.stream_chat", fake_stream)

    compacted = anyio.run(_compact_followup_history, session, store)

    assert compacted is True
    assert session.messages[:3] == original_prefix
    assert session.messages[3]["role"] == "user"
    assert "历史对话摘要" in session.messages[3]["content"]
    assert session.messages[4] == {"role": "assistant", "content": "仓位与风控摘要"}
    assert session.messages[5:] == original_messages[-8:]
    assert store.persisted_messages == session.messages


@pytest.mark.parametrize("failure_mode", ["error", "empty"])
def test_compact_followup_history_keeps_full_history_on_summary_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    session = seed_followup_session("aid-1", _sample_result())
    for index in range(11):
        session.messages.extend(_turn(index))
    original_messages = [dict(message) for message in session.messages]
    store = _RecordingFollowupStore()

    async def fake_stream(_messages):
        if failure_mode == "error":
            raise AppError("summary_failed", "摘要失败", 502)
        yield "   "

    monkeypatch.setattr("app.followup.service.stream_chat", fake_stream)

    compacted = anyio.run(_compact_followup_history, session, store)

    assert compacted is False
    assert session.messages == original_messages
    assert store.persisted_messages is None


def test_build_decision_recall_includes_stage_facts() -> None:
    result = _sample_result()
    text = build_decision_recall(result["stage1"], result["stage2"])
    assert "【决策回忆摘要】" in text
    assert "方向=bullish" in text
    assert "闸门=proceed" in text
    assert "结果=trade" in text
    assert "入场=101.5" in text
    assert "结构允许做多" in text


def test_seed_session_has_pinned_prefix() -> None:
    session = seed_followup_session("aid-1", _sample_result())
    assert [m["role"] for m in session.messages] == ["system", "user", "assistant"]
    assert "追问助手" in session.messages[0]["content"]
    assert '"run_id":"aid-1"' in session.messages[1]["content"].replace(" ", "")
    assert "决策回忆摘要" in session.messages[2]["content"]


def test_user_turn_puts_kline_before_question() -> None:
    content = build_user_turn_content("现在还能持有吗？", _bars(), symbol="ES", period="5m")
    assert content.index("当前图表K线数据") < content.index("用户问题")
    assert content.index("最新已收盘棒·程序计算") < content.index("用户问题")
    assert "现在还能持有吗？" in content
    assert "upper_wick=" in content
    assert "body=" in content
    assert "日内开盘序号" in content
    assert "bar_timestamp=" in content
    assert "timeframe=5m" in content
    assert "day_index=" in content
    # Newest bar OHLC present
    assert "101.8000" in content or "101.8" in content


def test_session_store_reuses_same_run_id() -> None:
    store = FollowupSessionStore()
    first = anyio.run(ensure_followup_session, "aid-1", _sample_result(), store)
    second = anyio.run(ensure_followup_session, "aid-1", _sample_result(), store)
    assert first is second


def test_followup_messages_persist_and_reload(monkeypatch) -> None:
    persisted: list[dict[str, str]] = []

    async def fake_append(_run_id: str, messages: list[dict[str, str]]) -> None:
        persisted.extend(messages)

    async def fake_load(_run_id: str) -> list[dict[str, str]]:
        return list(persisted)

    monkeypatch.setattr("app.followup.service.append_followup_messages", fake_append)
    monkeypatch.setattr("app.followup.service.load_followup_messages", fake_load)

    async def run() -> None:
        store = FollowupSessionStore()
        session = await ensure_followup_session("aid-persist", _sample_result(), store=store)
        session.messages.append({"role": "user", "content": "hello"})
        session.messages.append({"role": "assistant", "content": "hi back"})
        await fake_append("aid-persist", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi back"},
        ])
        fresh_store = FollowupSessionStore()
        loaded = await fresh_store.get_or_load("aid-persist")
        assert loaded is not None
        roles = [m["role"] for m in loaded.messages]
        assert roles[-2] == "user"
        assert roles[-1] == "assistant"
        assert loaded.messages[-2]["content"] == "hello"
        assert loaded.messages[-1]["content"] == "hi back"

    anyio.run(run)


def test_stream_followup_appends_multi_turn_history(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FollowupSessionStore()
    replies = iter(["第一轮回答", "第二轮回答"])

    async def fake_stream(_messages):
        yield next(replies)

    monkeypatch.setattr("app.followup.service.stream_chat", fake_stream)

    async def run() -> None:
        events1 = [
            event
            async for event in stream_followup_turn(
                run_id="aid-1",
                result=_sample_result(),
                request=FollowupRequest(question="止损要不要挪？", bars=_bars(), symbol="ES", period="5m"),
                store=store,
            )
        ]
        events2 = [
            event
            async for event in stream_followup_turn(
                run_id="aid-1",
                result=_sample_result(),
                request=FollowupRequest(question="那目标呢？", bars=_bars(), symbol="ES", period="5m"),
                store=store,
            )
        ]
        assert events1[-1]["type"] == "done"
        assert events1[-1]["content"] == "第一轮回答"
        assert events2[-1]["turn_count"] == 2

        session = store.get("aid-1")
        assert session is not None
        roles = [m["role"] for m in session.messages]
        assert roles == ["system", "user", "assistant", "user", "assistant", "user", "assistant"]
        assert "止损要不要挪？" in session.messages[3]["content"]
        assert session.messages[4]["content"] == "第一轮回答"
        assert "那目标呢？" in session.messages[5]["content"]
        assert session.messages[6]["content"] == "第二轮回答"

    anyio.run(run)


def test_stream_followup_compacts_after_threshold_without_changing_turn_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingFollowupStore()
    session = seed_followup_session("aid-compact-stream", _sample_result())
    for index in range(10):
        session.messages.extend(_turn(index))
    store.put(session)
    replies = iter(["第十一轮回答", "滚动摘要"])

    async def fake_stream(_messages):
        yield next(replies)

    async def fake_append(_run_id, _messages):
        return None

    monkeypatch.setattr("app.followup.service.stream_chat", fake_stream)
    monkeypatch.setattr("app.followup.service.append_followup_messages", fake_append)

    async def run() -> None:
        events = [
            event
            async for event in stream_followup_turn(
                run_id="aid-compact-stream",
                result=_sample_result(),
                request=FollowupRequest(question="第十一问", bars=_bars(), symbol="ES", period="5m"),
                store=store,
            )
        ]

        assert events[-1]["type"] == "done"
        assert events[-1]["content"] == "第十一轮回答"
        assert events[-1]["turn_count"] == 11
        compacted = store.get("aid-compact-stream")
        assert compacted is not None
        assert compacted.messages[4]["content"] == "滚动摘要"
        assert compacted.messages[-1]["content"] == "第十一轮回答"
        assert store.persisted_messages == compacted.messages

    anyio.run(run)


def test_followup_endpoint_streams_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_history(run_id: str, *, user_id):
        assert run_id == "aid-1"
        assert user_id is None
        return _sample_result()

    async def fake_stream(_messages):
        yield "可以持有，"
        yield "但把止损收到 100。"

    monkeypatch.setattr("app.main.get_analysis_history", fake_history)
    monkeypatch.setattr("app.followup.service.stream_chat", fake_stream)
    monkeypatch.setattr("app.followup.service.DEFAULT_FOLLOWUP_STORE", FollowupSessionStore())

    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/v1/analyses/aid-1/followup/stream",
        json={
            "question": "还能不能拿着？",
            "symbol": "ES",
            "period": "5m",
            "bars": [
                {
                    "timestamp": "2022-06-06T00:00:00+00:00",
                    "open": 100,
                    "high": 101,
                    "low": 99.5,
                    "close": 100.5,
                    "volume": 10,
                }
            ],
        },
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    events = [__import__("json").loads(line) for line in lines]
    assert events[0]["type"] == "status"
    assert any(event["type"] == "delta" for event in events)
    assert events[-1]["type"] == "done"
    assert "可以持有" in events[-1]["content"]


def test_followup_history_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.followup.service import (
        FollowupMessagePublic,
        list_followup_history,
    )

    async def fake_load(run_id: str):
        assert run_id == "aid-1"
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "ctx"},
            {"role": "assistant", "content": "recall"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]

    monkeypatch.setattr("app.followup.service.load_followup_messages", fake_load)
    monkeypatch.setattr("app.main.list_followup_history", list_followup_history)

    async def run() -> None:
        result = await list_followup_history("aid-1")
        assert len(result) == 2
        assert result[0].role == "user"
        assert result[0].content == "你好"
        assert result[1].role == "assistant"
        assert result[1].content == "你好！"

    anyio.run(run)
