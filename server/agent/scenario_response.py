"""
Purpose: 场景组合回答构建：将多槽位商品组合格式化为自然语言回答（防晒→穿搭→出行→拍照）。
"""

from typing import Any


def build_bundle_answer(bundle: Any, grouped_cards: list[tuple[Any, list[dict]]]) -> str:
    pieces = [f"我按“{bundle.title}”来搭配。{bundle.summary}"]
    for slot, cards in grouped_cards:
        slot_label = getattr(slot, "label", None) or getattr(slot, "name", "未命名槽位")
        if cards:
            card = cards[0]
            price = int(float(card["price"]))
            pieces.append(f"{slot_label}：{card['name']}，¥{price}，{card['reason']}")
        else:
            pieces.append(f"{slot_label}：当前商品库里暂时没有足够匹配的商品。")
    pieces.append("你可以继续补充预算、品牌偏好或删掉某一类，我会按这套方案继续收敛。")
    return " ".join(pieces)
