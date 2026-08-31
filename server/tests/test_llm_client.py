"""
Purpose: LLM 客户端测试：验证 SSE 解析、超时、降级。
"""

import asyncio

import httpx

from server.llm.ark_client import ArkChatClient, parse_openai_sse_line
from server.llm.prompt import build_grounded_messages


def test_parse_openai_sse_line_extracts_token() -> None:
    line = 'data: {"choices":[{"delta":{"content":"你好"}}]}'

    assert parse_openai_sse_line(line) == "你好"


def test_parse_openai_sse_line_ignores_done() -> None:
    assert parse_openai_sse_line("data: [DONE]") == ""


def test_build_grounded_messages_include_guardrails_and_products() -> None:
    messages = build_grounded_messages(
        user_message="推荐一款眼霜",
        intent="buying",
        cards=[
            {
                "id": "p_beauty_021",
                "name": "科颜氏牛油果保湿眼霜",
                "category": "美妆护肤",
                "brand": "科颜氏",
                "price": 210.0,
                "reason": "匹配眼霜需求",
            }
        ],
    )

    prompt = "\n".join(message["content"] for message in messages)

    assert "只能基于提供的商品上下文回答" in prompt
    assert "不得编造" in prompt
    assert "科颜氏牛油果保湿眼霜" in prompt
    assert "210.0 元" in prompt


def test_ark_chat_client_retries_retryable_errors_before_streaming_tokens() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async def collect() -> list[str]:
        client = ArkChatClient(
            api_key="test-key",
            base_url="https://ark.example.test/api/v3",
            model="doubao",
            timeout_seconds=1,
            retry_attempts=2,
            transport=httpx.MockTransport(handler),
        )
        return [token async for token in client.stream_messages([{"role": "user", "content": "hi"}])]

    assert asyncio.run(collect()) == ["你好"]
    assert calls == 2
