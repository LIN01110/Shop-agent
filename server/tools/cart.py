"""
Purpose: 购物车工具：解析购物车操作（加购/删除/改数量/查看/结算），执行后返回 cart_update 事件。
"""

from __future__ import annotations

from typing import Any

from server.agent.context import select_contextual_product
from server.nlu.quantity import is_purchase_quantity_expression
from server.rag.brand_aliases import alias_in_text, default_brand_catalog, message_mentions_brand
from server.session.state import SessionState
from server.tools.cart_operations import (
    CartOperation,
    extract_ordinal_index,
    parse_cart_operations,
)


class CartTool:
    name = "manage_cart"

    def run(self, message: str, session: SessionState, plan=None) -> dict[str, Any]:
        pending_answer = handle_pending_cart_action(message, session)
        if pending_answer is not None:
            answer = pending_answer
        else:
            operations = parse_cart_operations(message, plan)
            answer = execute_cart_operations(operations, session=session, plan=plan)

        return {
            "answer": answer,
            "cart": build_cart_payload(session.cart),
            "needs_clarification": bool(session.pending_cart_action) or is_clarification_answer(answer),
        }


def execute_cart_operations(
    operations: list[CartOperation],
    *,
    session: SessionState,
    plan=None,
    sync_to_db: bool = False,  # Phase 5: 是否同步到持久化购物车
) -> str:
    if not operations:
        return build_cart_summary(session.cart)

    working_cart = [dict(item) for item in session.cart]
    messages: list[str] = []
    last_touched_product_id: str | None = None
    use_plan_quantity = len(operations) == 1

    for operation in operations:
        if operation.action == "view":
            messages.append(build_cart_summary(working_cart))
            continue
        if operation.action == "checkout":
            messages.append(build_checkout_answer_from_cart(working_cart))
            continue
        if operation.action == "clear":
            if not working_cart:
                messages.append("购物车目前已经是空的。")
            else:
                working_cart.clear()
                messages.append("已清空购物车。")
            last_touched_product_id = None
            continue
        if operation.action == "add":
            if is_target_purchase_quantity_expression(operation.segment):
                return prepare_purchase_confirmation(operation, session=session, plan=plan)
            resolved = resolve_candidate_product(operation, session=session, plan=plan)
            if isinstance(resolved, CartClarification):
                return abort_transaction_answer(resolved.message)
            quantity = operation.quantity or (getattr(plan, "quantity", None) if use_plan_quantity else None) or 1
            item = apply_add(working_cart, resolved, quantity, set_quantity=False)
            last_touched_product_id = str(item.get("product_id", ""))
            messages.append(f"已将 {item['name']} 加入购物车，数量 {item['quantity']}。")
            continue

        resolved_index = resolve_cart_item_index(
            operation,
            working_cart,
            last_touched_product_id=last_touched_product_id,
        )
        if isinstance(resolved_index, CartClarification):
            return abort_transaction_answer(resolved_index.message)
        if operation.action == "remove":
            item = working_cart.pop(resolved_index)
            last_touched_product_id = None
            messages.append(f"已从购物车删除 {item['name']}。")
            continue

        if operation.action == "update_quantity":
            quantity = operation.quantity or (getattr(plan, "quantity", None) if use_plan_quantity else None)
            item = working_cart[resolved_index]
            if operation.quantity_delta:
                item["quantity"] = max(1, int(item.get("quantity", 1)) + operation.quantity_delta)
            elif quantity is not None:
                item["quantity"] = max(1, quantity)
            else:
                return abort_transaction_answer("我还没识别到要修改成几件，可以说“把第二个数量改成 2”。")
            last_touched_product_id = str(item.get("product_id", ""))
            messages.append(f"已将 {item['name']} 的数量改为 {item['quantity']}。")

    session.cart = working_cart
    
    # Phase 5: 购物车变更后，如有 user_id 则触发持久化同步
    # NOTE: 实际 DB 写入在 api/chat.py 或 auth 登录时统一合并，
    #       此处仅做标记以便后续异步任务捕获。
    if sync_to_db and session.cart:
        session._cart_dirty = True  # type: ignore[attr-defined]

    if not messages:
        return build_cart_summary(session.cart)
    return " ".join(messages) + build_cart_totals_sentence(session.cart)


def handle_pending_cart_action(message: str, session: SessionState) -> str | None:
    pending = session.pending_cart_action
    if not pending:
        return None

    if is_cancel_expression(message):
        session.pending_cart_action = {}
        return "好的，已取消刚才的购物车操作。"

    candidates = [item for item in pending.get("candidates", []) if isinstance(item, dict)]
    if not candidates:
        session.pending_cart_action = {}
        return "刚才的待确认购物车操作已经失效，你可以重新说一次要买哪款。"

    selected = select_pending_candidate(message, candidates)
    if selected is None and is_confirm_expression(message):
        if len(candidates) == 1:
            selected = candidates[0]
        else:
            return build_pending_choice_prompt(candidates, pending)
    if selected is None:
        return build_pending_choice_prompt(candidates, pending)

    quantity = int(pending.get("quantity") or 1)
    set_quantity = bool(pending.get("set_quantity", False))
    item = apply_add(session.cart, selected, quantity, set_quantity=set_quantity)
    session.pending_cart_action = {}
    return f"已将 {item['name']} 加入购物车，数量 {item['quantity']}。{build_cart_totals_sentence(session.cart)}"


def prepare_purchase_confirmation(operation: CartOperation, *, session: SessionState, plan=None) -> str:
    candidates = resolve_purchase_candidates(operation, session=session, plan=plan)
    if isinstance(candidates, CartClarification):
        return f"{candidates.message} 我还没有修改购物车。"

    quantity = operation.quantity or getattr(plan, "quantity", None) or 1
    quantity_unit = operation.quantity_unit or "件"
    session.pending_cart_action = {
        "action": "add",
        "quantity": quantity,
        "quantity_unit": quantity_unit,
        "set_quantity": True,
        "candidates": candidates,
        "source": "purchase_confirmation",
    }
    if len(candidates) == 1:
        product = candidates[0]
        return (
            f"我理解你想买 {quantity} {quantity_unit} {product['brand']} 的这款：{product['name']}。"
            "确认把它加入购物车吗？你可以回复“确认”或“取消”。"
        )
    return build_pending_choice_prompt(candidates, session.pending_cart_action)


def resolve_purchase_candidates(
    operation: CartOperation,
    *,
    session: SessionState,
    plan=None,
) -> list[dict] | CartClarification:
    candidates = session.candidate_product_cards
    if not candidates:
        return CartClarification("我还没有可购买的候选商品。你可以先让我推荐商品。")

    if operation.target_index is not None:
        if 0 <= operation.target_index < len(candidates):
            return [candidates[operation.target_index]]
        return CartClarification(f"当前推荐列表里没有第 {operation.target_index + 1} 个商品。")

    matches = find_matching_items(operation.segment, candidates, id_field="id")
    if matches:
        return matches
    mentioned_brands = extract_mentioned_brands(operation.segment)
    if mentioned_brands:
        brand_text = "、".join(mentioned_brands[:3])
        return CartClarification(
            f"当前推荐列表里没有匹配到 {brand_text} 的商品。"
            f"你可以先让我“推荐{brand_text}运动鞋”，我找到具体商品后再帮你加购。"
        )

    contextual = select_contextual_product(operation.segment, candidates, plan)
    if contextual is not None:
        return [contextual]
    return CartClarification(build_candidate_clarification(candidates))


def select_pending_candidate(message: str, candidates: list[dict]) -> dict | None:
    index = extract_ordinal_index(message)
    if index is not None and 0 <= index < len(candidates):
        return candidates[index]

    matches = find_matching_items(message, candidates, id_field="id")
    if len(matches) == 1:
        return matches[0]
    return None


def build_pending_choice_prompt(candidates: list[dict], pending: dict) -> str:
    quantity = int(pending.get("quantity") or 1)
    quantity_unit = str(pending.get("quantity_unit") or "件")
    options = "；".join(
        f"{index + 1}. {item['brand']} {item['name']}，¥{int(float(item.get('price', 0)))}"
        for index, item in enumerate(candidates[:5])
    )
    return f"我需要先确认你想买哪一款，数量 {quantity} {quantity_unit}：{options}。你可以回复“第一款”“第二款”或“取消”。"


def is_confirm_expression(message: str) -> bool:
    text = message.strip().lower()
    return text in {"确认", "是", "是的", "对", "对的", "可以", "好的", "ok", "yes", "加吧", "加入"}


def is_cancel_expression(message: str) -> bool:
    text = message.strip().lower()
    return text in {"取消", "算了", "不要了", "先不用", "不用了", "cancel", "no"}


def extract_mentioned_brands(message: str) -> list[str]:
    brands: list[str] = []
    for group in default_brand_catalog().groups:
        if any(alias_in_text(message, alias) for alias in group.aliases):
            if group.canonical not in brands:
                brands.append(group.canonical)
    return brands


class CartClarification:
    def __init__(self, message: str) -> None:
        self.message = message


def abort_transaction_answer(message: str) -> str:
    return f"{message} 我还没有修改购物车。"


def is_clarification_answer(answer: str) -> bool:
    if "我还没有修改购物车" not in answer:
        return False
    return any(word in answer for word in ["需要", "确认", "可以先", "请用", "哪一个", "哪一款"])


def resolve_candidate_product(operation: CartOperation, *, session: SessionState, plan=None) -> dict | CartClarification:
    candidates = session.candidate_product_cards
    if not candidates:
        return CartClarification("我还没有可加购的候选商品。你可以先让我推荐或对比商品。")

    if operation.target_index is not None:
        if 0 <= operation.target_index < len(candidates):
            return candidates[operation.target_index]
        return CartClarification(f"当前推荐列表里没有第 {operation.target_index + 1} 个商品。")

    text_match = resolve_item_by_text(operation.segment, candidates, id_field="id")
    if isinstance(text_match, CartClarification):
        return text_match
    if text_match is not None:
        return text_match

    contextual = select_contextual_product(operation.segment, candidates, plan)
    if contextual is not None:
        return contextual

    if len(candidates) == 1:
        return candidates[0]
    return CartClarification(build_candidate_clarification(candidates))


def resolve_cart_item_index(
    operation: CartOperation,
    cart: list[dict],
    *,
    last_touched_product_id: str | None,
) -> int | CartClarification:
    if not cart:
        return CartClarification("购物车目前是空的，暂时没有商品可以操作。")

    if operation.target_index is not None:
        if 0 <= operation.target_index < len(cart):
            return operation.target_index
        return CartClarification(f"购物车里没有第 {operation.target_index + 1} 个商品。")

    text_match = resolve_item_by_text(operation.segment, cart, id_field="product_id")
    if isinstance(text_match, CartClarification):
        return text_match
    if text_match is not None:
        return cart.index(text_match)

    if last_touched_product_id:
        for index, item in enumerate(cart):
            if str(item.get("product_id", "")) == last_touched_product_id:
                return index

    if len(cart) == 1:
        return 0
    return CartClarification(build_cart_target_clarification(cart))


def resolve_item_by_text(segment: str, items: list[dict], *, id_field: str) -> dict | CartClarification | None:
    unique_matches = find_matching_items(segment, items, id_field=id_field)
    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(unique_matches) > 1:
        return CartClarification("我找到了多个可能的商品，请用“第几个”或商品完整名称再说一次。")
    return None


def find_matching_items(segment: str, items: list[dict], *, id_field: str) -> list[dict]:
    matches: list[dict] = []
    normalized = segment.lower()
    for item in items:
        brand = str(item.get("brand", "")).strip()
        name = str(item.get("name", "")).strip()
        item_id = str(item.get(id_field, "")).strip()
        if brand and message_mentions_brand(segment, brand):
            matches.append(item)
            continue
        if name and name.lower() in normalized:
            matches.append(item)
            continue
        if item_id and item_id.lower() in normalized:
            matches.append(item)

    return dedupe_items(matches, id_field=id_field)


def apply_add(cart: list[dict], product: dict, quantity: int, *, set_quantity: bool = False) -> dict:
    existing = next((item for item in cart if item.get("product_id") == product.get("id")), None)
    if existing:
        existing["quantity"] = max(1, quantity) if set_quantity else int(existing.get("quantity", 0)) + quantity
        return existing
    item = build_cart_item(product, quantity)
    cart.append(item)
    return item


def is_target_purchase_quantity_expression(segment: str) -> bool:
    return is_purchase_quantity_expression(segment)


def dedupe_items(items: list[dict], *, id_field: str) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        item_id = str(item.get(id_field, ""))
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def build_cart_target_clarification(cart: list[dict]) -> str:
    options = "；".join(f"{index + 1}. {item['name']} × {item['quantity']}" for index, item in enumerate(cart))
    return f"购物车里有多个商品，我需要确认你要操作哪一个：{options}。"


def build_candidate_clarification(candidates: list[dict]) -> str:
    options = "；".join(f"{index + 1}. {item['name']}" for index, item in enumerate(candidates[:5]))
    return f"当前推荐列表有多个商品，我需要确认你要加哪一个：{options}。"


def build_checkout_answer_from_cart(cart: list[dict]) -> str:
    if not cart:
        return "购物车目前是空的，暂时无法下单。你可以先把商品加入购物车。"

    return (
        "已为你生成模拟订单确认："
        f"{build_cart_summary(cart)} "
        "当前 Demo 不会真实支付或提交地址，只展示购物车到下单确认的闭环。"
    )


def build_cart_item(product: dict, quantity: int) -> dict[str, Any]:
    return {
        "product_id": product.get("id", ""),
        "name": product.get("name", ""),
        "brand": product.get("brand", ""),
        "category": product.get("category", ""),
        "price": float(product.get("price", 0)),
        "quantity": quantity,
        "image_url": product.get("image_url", ""),
        "detail_url": product.get("detail_url", ""),
    }


def build_cart_payload(cart: list[dict]) -> dict[str, Any]:
    total_quantity = sum(int(item.get("quantity", 0)) for item in cart)
    total_price = sum(float(item.get("price", 0)) * int(item.get("quantity", 0)) for item in cart)
    return {
        "items": cart,
        "total_quantity": total_quantity,
        "total_price": round(total_price, 2),
        "is_empty": total_quantity == 0,
    }


def build_cart_summary(cart: list[dict]) -> str:
    if not cart:
        return "购物车目前是空的。"

    items = [
        f"{index + 1}. {item['name']} × {item['quantity']}，¥{int(float(item['price']) * int(item['quantity']))}"
        for index, item in enumerate(cart)
    ]
    return "购物车中有：" + "；".join(items) + f"。{build_cart_totals_sentence(cart)}"


def build_cart_totals_sentence(cart: list[dict]) -> str:
    payload = build_cart_payload(cart)
    return f"当前共 {payload['total_quantity']} 件，合计 ¥{int(payload['total_price'])}。"
