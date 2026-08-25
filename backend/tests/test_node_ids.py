"""Tests for semantic node_id registry."""

from app.analysis.workflow.stage1.core.node_ids import (
    CYCLE_IDENTIFIABLE,
    PLANNED_LIMIT,
    PROHIBITION_SCAN,
    TRADER_EQUATION,
    canonicalize_node_id,
    node_order_key,
    remap_trace_payload,
)


def test_canonicalize_legacy_and_section_mark():
    assert canonicalize_node_id("1.2") == CYCLE_IDENTIFIABLE
    assert canonicalize_node_id("§9.0P") == PLANNED_LIMIT
    assert canonicalize_node_id("14.1") == PROHIBITION_SCAN
    assert canonicalize_node_id(CYCLE_IDENTIFIABLE) == CYCLE_IDENTIFIABLE


def test_node_order_stage1_before_stage2():
    assert node_order_key("cycle_identifiable") < node_order_key("trader_equation")
    assert node_order_key("1.2") < node_order_key("10.3")


def test_remap_trace_payload_nested():
    payload = {
        "stage1": {"gate_trace": [{"node_id": "1.2", "answer": "是"}]},
        "stage2": {
            "decision_trace": [{"node_id": "10.3", "answer": "否"}],
            "terminal": {"node_id": "14", "outcome": "wait"},
        },
    }
    remap_trace_payload(payload)
    assert payload["stage1"]["gate_trace"][0]["node_id"] == CYCLE_IDENTIFIABLE
    assert payload["stage2"]["decision_trace"][0]["node_id"] == TRADER_EQUATION
    assert payload["stage2"]["terminal"]["node_id"] == PROHIBITION_SCAN
