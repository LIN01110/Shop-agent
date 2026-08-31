"""
Purpose: 会话管理 API：获取/恢复会话状态，返回购物车摘要。
"""

from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from server.agent.orchestrator import Orchestrator
from server.app_container import get_orchestrator
from server.session.state import SessionState
from server.tools.cart import build_cart_payload


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    sessions = [
        build_session_summary(record.state, record.updated_at)
        for record in orchestrator.sessions.list_recent(limit)
        if has_meaningful_state(record.state)
    ]
    return {"sessions": sessions}


@router.delete("/{session_id}")
async def reset_session(
    session_id: str = Path(min_length=1, max_length=128),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    orchestrator.sessions.delete(session_id)
    return {
        "session_id": session_id,
        "reset": True,
        "cart": build_cart_payload([]),
    }


@router.get("/{session_id}")
async def get_session_snapshot(
    session_id: str = Path(min_length=1, max_length=128),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    session = orchestrator.sessions.get(session_id)
    return build_session_snapshot(session)


@router.get("/{session_id}/memory")
async def get_session_memory(
    session_id: str = Path(min_length=1, max_length=128),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict[str, Any]:
    session = orchestrator.sessions.get(session_id)
    return {
        "session_id": session.session_id,
        "history_summary": session.history_summary,
        "user_profile": session.user_profile.model_dump(),
        "cart": build_cart_payload(session.cart),
        "history_turns": len(session.history),
    }


def build_session_summary(session: SessionState, updated_at: float) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": build_session_title(session),
        "summary": session.history_summary,
        "updated_at": updated_at,
        "message_count": len(session.history),
        "cart_quantity": build_cart_payload(session.cart)["total_quantity"],
    }


def build_session_snapshot(session: SessionState) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "title": build_session_title(session),
        "summary": session.history_summary,
        "messages": [
            {
                "role": turn.role,
                "content": turn.content,
            }
            for turn in session.history
        ],
        "products": session.candidate_product_cards,
        "cart": build_cart_payload(session.cart),
        "user_profile": session.user_profile.model_dump(),
    }


def build_session_title(session: SessionState) -> str:
    for turn in session.history:
        if turn.role != "user":
            continue
        content = turn.content.strip()
        if content:
            return truncate_text(content, 28)
    if session.cart:
        return truncate_text(f"购物车：{session.cart[0].get('name', '')}", 28)
    return "新对话"


def truncate_text(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else f"{value[:max_chars]}..."


def has_meaningful_state(session: SessionState) -> bool:
    return bool(session.history or session.cart or session.history_summary or session.candidate_product_cards)
