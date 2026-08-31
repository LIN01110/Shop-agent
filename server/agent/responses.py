"""
Purpose: 响应流式工具：stream_text 将文本切分为 token 逐字输出，build_done_payload 构造 SSE 结束事件。
"""

import asyncio
from collections.abc import AsyncIterator


async def stream_text(text: str) -> AsyncIterator[dict]:
    for chunk in iter_response_chunks(text):
        yield {"event": "token", "data": {"text": chunk}}
        await asyncio.sleep(0)


def iter_response_chunks(text: str, *, max_chars: int = 48) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in "。！？!?；;\n" or len(buffer) >= max_chars:
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def build_done_payload(
    session_id: str,
    session,
    needs_clarification: bool,
    plan=None,
    trace_id: str = "",
) -> dict:
    constraints = []
    if plan is not None:
        constraints = [item.model_dump() for item in getattr(plan, "constraints", [])]
    presentation_mode = getattr(plan, "presentation_mode", "auto") if plan is not None else "auto"
    payload = {
        "session_id": session_id,
        "filters": [item.model_dump() for item in session.filters],
        "exclusions": [item.model_dump() for item in session.exclusions],
        "constraints": constraints,
        "presentation_mode": presentation_mode,
        "needs_clarification": needs_clarification,
        "pending_subject": session.pending_subject,
    }
    if trace_id:
        payload["trace_id"] = trace_id
    return payload


def build_grounded_answer(
    message: str,
    cards: list[dict],
    intent: str,
    *,
    presentation_mode: str = "auto",
) -> str:
    if not cards:
        return (
            "我在当前商品库里没有找到足够匹配的商品。"
            "你可以换一个类目、放宽预算，或补充更具体的需求。"
        )

    if presentation_mode == "listing":
        return build_catalog_listing_answer(cards)

    primary = cards[0]
    alternatives = cards[1:3]

    if intent == "browsing":
        opening = "我先按当前商品库给你几个可参考的方向。"
    else:
        opening = "我会优先推荐这款，先给你一个稳妥选择。"

    pieces = [
        opening,
        f"首选是 {format_product_reference(primary)}。",
        f"推荐理由：{build_card_reason(primary)}",
    ]
    if alternatives:
        pieces.append("另外可以顺手对比这两款：")
        for card in alternatives:
            pieces.append(f"{format_product_reference(card)}，{build_card_reason(card)}")
    pieces.append("如果你有肤质、预算、通勤/户外等偏好，我可以继续把候选缩到一两款。")
    return "\n".join(piece for piece in pieces if piece.strip())


def build_catalog_listing_answer(cards: list[dict]) -> str:
    label = infer_listing_label(cards)
    pieces = [
        f"当前商品库里按你的条件找到 {len(cards)} 款{label}。"
    ]
    for index, card in enumerate(cards[:10], 1):
        pieces.append(f"{index}. {format_product_reference(card)}：{build_card_reason(card)}")
    if len(cards) > 10:
        pieces.append("我先展示前 10 款，剩余商品可以继续按预算、品牌或功效筛选。")
    else:
        pieces.append("这些商品卡片已在下方展示，你可以继续按预算、品牌、肤质或功效再筛。")
    return "\n".join(piece for piece in pieces if piece.strip())


def infer_listing_label(cards: list[dict]) -> str:
    if not cards:
        return "商品"
    first = cards[0]
    names = first.get("product_type_names")
    if isinstance(names, list) and names:
        return str(names[0])
    category = str(first.get("category", "")).strip()
    return category or "商品"


def format_product_reference(card: dict) -> str:
    name = str(card.get("name", "")).strip()
    brand = str(card.get("brand", "")).strip()
    price = card.get("price", "")
    brand_prefix = f"{brand} " if brand and brand not in name else ""
    return f"{brand_prefix}{name}（¥{format_price(price)}）"


def format_price(value) -> str:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return str(value)
    if price.is_integer():
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")


def clean_reason(reason: str) -> str:
    text = " ".join(str(reason).split()).strip()
    if not text:
        return "它与当前检索条件相近。"
    if text[-1] not in "。！？!?；;":
        text += "。"
    return text


def build_card_reason(card: dict) -> str:
    evidence = card.get("evidence") if isinstance(card.get("evidence"), dict) else {}
    highlights = evidence.get("highlights") if isinstance(evidence.get("highlights"), list) else []
    for item in highlights:
        reason = clean_reason(str(item))
        if reason:
            return reason
    return clean_reason(str(card.get("reason", "")))
