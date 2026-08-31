"""
Purpose: MCP Server —— 将导购 Agent 的商品检索 / 商品对比 / 购物车能力
         通过 Model Context Protocol 暴露给外部 Agent（Claude Desktop、Cursor 等）。

设计要点:
- 复用 ToolRegistry 中已注册的三个工具，不改动编排逻辑（编排器与传输层解耦）。
- 检索/对比工具将「自然语言进、自然语言出」升级为「结构化参数进、JSON 出」，
  过滤器直接映射到生产检索管道的 7 级结构化后过滤（SearchFilters）。
- 购物车保留自然语言指令接口（NL 本身就是购物车交互的最佳抽象），
  通过 session_id 参数映射到现有 SessionStore，支持多会话隔离。
- 配置化开关: 环境变量 MCP_ENABLED=true 才允许启动（默认关闭，向后兼容）。
- 传输: 默认 stdio（MCP 客户端标准接入方式）；MCP_TRANSPORT=sse 可切换 SSE。

启动:
    MCP_ENABLED=true python -m server.mcp_server
验证:
    python scripts/test_mcp_client.py
"""

from __future__ import annotations

import json
import logging
import os

from fastmcp import FastMCP

from server.app_container import get_orchestrator
from server.rag.post_process import SearchFilters

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "shop-agent",
    instructions=(
        "电商导购工具集：search_products（商品检索）、compare_products（商品对比）、"
        "manage_cart（购物车管理）。检索管道为「向量+BM25 混合召回 → 结构化后过滤 → 重排序」。"
    ),
)


def _registry():
    return get_orchestrator().registry


def _sessions():
    return get_orchestrator().sessions


def _bind_candidates(session_id: str | None, cards: list[dict]) -> None:
    """将检索/对比结果绑定为会话候选商品，与 /chat 流程中 handler 的行为一致，
    使 MCP 通道支持「先搜索、再说把第一个加购物车」的多轮上下文。"""
    if not session_id or not cards:
        return
    sessions = _sessions()
    session = sessions.get(session_id)
    session.candidate_products = [card["id"] for card in cards if "id" in card]
    session.candidate_product_cards = cards
    sessions.save(session)


@mcp.tool()
def search_products(
    query: str,
    top_k: int = 5,
    product_type: str | None = None,
    brand: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False,
    session_id: str | None = None,
) -> str:
    """检索商品库，返回结构化商品卡片 JSON（含价格/库存/属性）。

    query: 自然语言商品需求，如「白色跑鞋」「千元以内蓝牙耳机」。
    product_type / brand / min_price / max_price / in_stock_only 为结构化过滤条件，
    与生产路径的结构化后过滤一致。
    session_id: 可选；传入后检索结果会绑定为该会话的候选商品，
    后续可用 manage_cart 以「第一个/这款」等指代加购（多轮上下文）。
    """
    filters = SearchFilters(
        min_price=min_price,
        max_price=max_price,
        product_types=[product_type] if product_type else [],
        brands=[brand] if brand else [],
        in_stock_only=in_stock_only,
    )
    tool = _registry().get("search_products")
    result = tool.run_with_diagnostics(query=query, filters=filters, top_k=max(1, min(top_k, 20)))
    _bind_candidates(session_id, result.cards)
    return json.dumps(
        {"cards": result.cards, "diagnostics": result.diagnostics},
        ensure_ascii=False,
        default=str,
    )


@mcp.tool()
def compare_products(query: str, top_k: int = 2, session_id: str | None = None) -> str:
    """对比商品，返回候选卡片 + 结构化对比维度 JSON。

    query: 对比需求，如「对比耐克和阿迪的跑鞋」「iPhone 15 和小米 14 哪个好」。
    session_id: 可选；传入后对比候选绑定为该会话的候选商品（多轮上下文）。
    """
    tool = _registry().get("compare_products")
    result = tool.run(query=query, filters=SearchFilters(), top_k=max(2, min(top_k, 5)))
    _bind_candidates(session_id, result.get("cards") or [])
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
def manage_cart(message: str, session_id: str = "mcp-default") -> str:
    """以自然语言管理购物车（加购/删除/改数量/查看/清空/结算）。

    message: 购物车指令，如「把第一个加购物车」「来两件」「查看购物车」。
    session_id: 会话标识，同一 session_id 共享购物车状态。
    """
    sessions = _sessions()
    session = sessions.get(session_id)
    tool = _registry().get("manage_cart")
    result = tool.run(message=message, session=session)
    sessions.save(session)
    return json.dumps(result, ensure_ascii=False, default=str)


def main() -> None:
    if os.getenv("MCP_ENABLED", "false").lower() != "true":
        raise SystemExit(
            "MCP Server 未启用：请设置环境变量 MCP_ENABLED=true 后重试"
            "（新功能默认关闭，不影响现有 HTTP 服务）。"
        )
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Shop-Agent MCP server (transport=%s)", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
