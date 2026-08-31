"""
Purpose: Prompt 模板：SYSTEM_PROMPT 系统提示词，build_product_context() 构建商品上下文，build_grounded_messages() 构造 grounding messages。
       追加：build_indexed_product_context() / build_indexed_grounded_messages() —— 序号引用模式（防ID幻觉）。
"""

import re

SYSTEM_PROMPT = """你是一个电商智能导购助手。
你只能基于提供的商品上下文回答，不得编造不存在的商品、价格、库存、优惠券、活动或功效。
如果商品上下文不足以满足用户需求，要明确说明不足，并建议用户放宽或补充条件。
回答要像真实导购：先给最推荐的一款，再说明为什么适合，必要时给备选差异。
不要机械复读字段，不要输出被截断的半句话；每个理由必须是完整自然句。
不要使用 Markdown 标记，例如 **加粗**、列表符号或代码块。"""


# ── 序号引用模式（防ID幻觉）──

INDEXED_SYSTEM_PROMPT = """你是一个电商智能导购助手。
你只能基于提供的候选商品回答，不得编造不存在的商品、价格、库存、优惠券、活动或功效。
回答时必须用【候选N】引用商品，例如【候选1】或【候选2】。
严禁输出或编造任何商品ID。
回答要像真实导购：先给最推荐的一款，再说明为什么适合，必要时给备选差异。
不要机械复读字段，不要输出被截断的半句话；每个理由必须是完整自然句。
不要使用 Markdown 标记。"""


def build_product_context(cards: list[dict]) -> str:
    if not cards:
        return "当前没有可用商品。"

    lines: list[str] = []
    for index, card in enumerate(cards[:3], 1):
        evidence = card.get("evidence") if isinstance(card.get("evidence"), dict) else {}
        highlights = evidence.get("highlights") if isinstance(evidence.get("highlights"), list) else []
        facts = [
            f"id={card.get('id', '')}",
            f"name={card.get('name', '')}",
            f"category={card.get('category', '')}",
            f"brand={card.get('brand', '')}",
            f"price={card.get('price', '')} 元",
            f"reason={card.get('reason', '')}",
        ]
        if highlights:
            facts.append(f"evidence={' '.join(str(item) for item in highlights[:2])}")
        lines.append(f"{index}. " + "; ".join(facts))
    return "\n".join(lines)


def build_grounded_messages(user_message: str, cards: list[dict], intent: str) -> list[dict]:
    product_context = build_product_context(cards)
    user_prompt = f"""用户需求：
{user_message}

识别意图：
{intent}

候选商品：
{product_context}

请基于候选商品回答。必须遵守：
1. 只推荐候选商品中的商品。
2. 价格、品牌、类目必须与候选商品一致。
3. 不要编造优惠、库存、销量、功效或外部评价。
4. 如果候选商品与需求不完全匹配，要如实说明。
5. 用自然导购口吻回答，不要截断句子；理由要来自"推荐依据"或"商品证据要点"。
"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# ── 序号引用模式（防ID幻觉）──

def build_indexed_product_context(cards: list[dict]) -> tuple[str, dict[int, str]]:
    """
    构建带序号的商品上下文（隐藏真实 product_id）。

    Returns:
        (prompt文本, {序号: product_id} 映射)
    """
    if not cards:
        return "当前没有可用商品。", {}

    lines = ["以下是可以推荐的候选商品："]
    index_to_id: dict[int, str] = {}

    for i, card in enumerate(cards, 1):
        index_to_id[i] = str(card.get("id", ""))
        lines.append(
            f"【候选{i}】\n"
            f"  名称：{card.get('name', '')}\n"
            f"  品牌：{card.get('brand', '')}\n"
            f"  价格：{card.get('price', '')}元\n"
            f"  类别：{card.get('category', '')}"
        )

    lines.append(
        "\n你只能推荐上述候选商品。"
        "回答时必须用【候选N】引用，例如\"推荐【候选1】，因为...\""
        "严禁编造候选序号，如不存在【候选5】时不能提到它。"
    )
    return "\n".join(lines), index_to_id


def build_indexed_grounded_messages(
    user_message: str,
    cards: list[dict],
    intent: str,
) -> tuple[list[dict], dict[int, str]]:
    """
    构建要求LLM用序号引用的 messages。

    Returns:
        (OpenAI messages列表, 序号到ID映射)
    """
    product_context, index_to_id = build_indexed_product_context(cards)

    user_prompt = f"""用户需求：
{user_message}

识别意图：
{intent}

{product_context}

请基于候选商品回答。必须遵守：
1. 只推荐候选商品列表中的商品。
2. 用【候选N】引用商品，如"推荐【候选1】"或"【候选1】和【候选2】都不错"。
3. 严禁编造候选序号。
4. 价格、品牌必须与候选商品一致。
5. 不要编造优惠、库存、销量、功效。
6. 用自然导购口吻回答，不要截断句子。
"""
    return [
        {"role": "system", "content": INDEXED_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ], index_to_id
