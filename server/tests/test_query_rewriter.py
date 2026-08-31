"""
Purpose: 查询重写测试：验证会话上下文拼入查询。
"""

from server.agent.query_rewriter import rewrite_query
from server.session.state import SessionState


def test_rewrite_query_includes_pending_subject_for_follow_up() -> None:
    session = SessionState(session_id="pytest")
    session.pending_subject = "手机"

    query = rewrite_query("拍照优先，预算4000", session)

    assert query.startswith("手机 拍照优先")
