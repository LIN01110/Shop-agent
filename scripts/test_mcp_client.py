"""
Purpose: MCP 通道端到端验证脚本 —— 以 MCP Client 身份通过 stdio 拉起
         server.mcp_server，校验工具列表与三个工具的调用结果。

用法（仓库根目录）:
    python scripts/test_mcp_client.py

通过标准:
1. list_tools 返回 search_products / compare_products / manage_cart；
2. search_products 返回非空 cards；
3. compare_products 返回 comparison 字段；
4. manage_cart 加购后购物车非空，且跨调用（同 session_id）状态保持。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


def _server_params() -> StdioServerParameters:
    env = dict(os.environ)
    env["MCP_ENABLED"] = "true"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.mcp_server"],
        cwd=str(ROOT),
        env=env,
    )


def _parse(result) -> dict:
    text = result.content[0].text
    return json.loads(text)


async def main() -> int:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("[1] list_tools:", names)
            assert names == ["compare_products", "manage_cart", "search_products"], names

            r = await session.call_tool(
                "search_products",
                {"query": "运动鞋", "top_k": 3, "session_id": "mcp-test"},
            )
            data = _parse(r)
            print(f"[2] search_products: {len(data['cards'])} cards")
            assert data["cards"], "search_products 返回空"
            first = data["cards"][0]
            print("    首个商品:", first.get("title") or first.get("name"))

            r = await session.call_tool(
                "compare_products", {"query": "对比两款跑鞋", "top_k": 2}
            )
            data = _parse(r)
            print("[3] compare_products: comparison =", bool(data.get("comparison")))
            assert data.get("comparison") is not None

            r = await session.call_tool(
                "manage_cart", {"message": "把第一个加购物车", "session_id": "mcp-test"}
            )
            data = _parse(r)
            cart = data.get("cart") or {}
            items = cart.get("items") if isinstance(cart, dict) else None
            print("[4] manage_cart add: items =", len(items) if items else cart)
            assert items, "加购后购物车为空"

            r = await session.call_tool(
                "manage_cart", {"message": "查看购物车", "session_id": "mcp-test"}
            )
            data = _parse(r)
            cart = data.get("cart") or {}
            items = cart.get("items") if isinstance(cart, dict) else None
            print("[5] manage_cart view (同 session):", len(items) if items else 0, "件")
            assert items, "同 session_id 购物车状态未保持"

    print("\nALL MCP CHECKS PASSED ✅")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
