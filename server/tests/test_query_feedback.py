"""
Purpose: 查询反馈测试：验证检索失败事件记录。
"""

import json

from server.agent.query_feedback import QueryFeedbackStore, build_query_feedback_event
from server.agent.semantic_schema import SemanticPlan
from server.rag.post_process import SearchFilters


def test_query_feedback_records_no_retrieval_output(tmp_path) -> None:
    path = tmp_path / "query_failures.jsonl"
    store = QueryFeedbackStore(path)
    plan = SemanticPlan(intent="recommend", needs_search=True)
    filters = SearchFilters(product_types=["clothes.sports_shoes"])
    event = build_query_feedback_event(
        trace_id="trace-1",
        session_id="session-1",
        message="\u63a8\u8350\u4e00\u6b3e200\u4ee5\u4e0b\u7684\u8fd0\u52a8\u978b",
        plan=plan,
        filters=filters,
        events=[{"event": "status", "data": {}}],
        metadata={"scope_transition": {"type": "continue_refinement"}},
    )

    assert event is not None
    store.record(event)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "trace-1"
    assert "no_retrieval_output" in rows[0]["reasons"]
    assert "retrieval_diagnostics_missing" in rows[0]["reasons"]
    assert rows[0]["filters"]["product_types"] == ["clothes.sports_shoes"]


def test_query_feedback_records_uncertain_scope() -> None:
    event = build_query_feedback_event(
        trace_id="trace-2",
        session_id="session-2",
        message="\u6709\u6ca1\u6709\u706b\u7bad\u53d1\u52a8\u673a",
        plan=SemanticPlan(intent="recommend", needs_search=True),
        filters=SearchFilters(),
        events=[{"event": "status", "data": {}}],
        metadata={"scope_transition": {"type": "uncertain"}},
    )

    assert event is not None
    assert "no_retrieval_output" in event.reasons
    assert "uncertain_product_scope" in event.reasons
    assert "no_structured_scope" in event.reasons


def test_query_feedback_skips_successful_product_card() -> None:
    event = build_query_feedback_event(
        trace_id="trace-3",
        session_id="session-3",
        message="\u63a8\u8350\u4e00\u6b3e\u8fd0\u52a8\u978b",
        plan=SemanticPlan(intent="recommend", needs_search=True),
        filters=SearchFilters(product_types=["clothes.sports_shoes"]),
        events=[{"event": "product_card", "data": {"id": "p1"}}],
        metadata={"scope_transition": {"type": "continue_refinement"}},
    )

    assert event is None


def test_query_feedback_splits_filter_to_zero_root_cause() -> None:
    event = build_query_feedback_event(
        trace_id="trace-4",
        session_id="session-4",
        message="\u63a8\u8350\u4e00\u6b3e200\u4ee5\u4e0b\u7684\u8fd0\u52a8\u978b",
        plan=SemanticPlan(intent="recommend", needs_search=True),
        filters=SearchFilters(product_types=["clothes.sports_shoes"], max_price=200),
        events=[{"event": "status", "data": {}}],
        metadata={
            "retrieval": {
                "applied_filters": {
                    "product_types": ["clothes.sports_shoes"],
                    "max_price": 200,
                },
                "steps": [
                    {"name": "store_prefilter_recall", "count": 8},
                    {"name": "RangeFilter", "count": 0},
                    {"name": "ProductTypeFilter", "count": 0},
                ],
            }
        },
    )

    assert event is not None
    assert "no_retrieval_output" in event.reasons
    assert "filtered_to_zero_by_RangeFilter" in event.reasons
    assert "final_candidate_empty" in event.reasons
