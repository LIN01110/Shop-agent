"""
Purpose: 调试 API：/debug/traces 查看最近对话 trace，用于定位问题。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from server.agent.orchestrator import Orchestrator
from server.app_container import get_orchestrator


router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/traces")
async def list_traces(
    session_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> list[dict]:
    return [trace.model_dump() for trace in orchestrator.trace_store.list(session_id=session_id, limit=limit)]


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    trace = orchestrator.trace_store.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace.model_dump()
