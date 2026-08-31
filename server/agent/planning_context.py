"""
Purpose: 规划上下文压缩：将对话历史、候选商品、购物车状态压缩为适合 LLM 读取的上下文格式。
"""

from dataclasses import dataclass
from typing import Any

from server.session.state import SessionState


MAX_CONTEXT_CANDIDATES = 8
MAX_CONTEXT_CART_ITEMS = 8
MAX_CONTEXT_HISTORY_TURNS = 6


@dataclass(frozen=True)
class ProductContext:
    id: str
    name: str
    brand: str
    category: str
    price: str


@dataclass(frozen=True)
class CartItemContext:
    product_id: str
    name: str
    brand: str
    quantity: int
    price: str


@dataclass(frozen=True)
class PlanningContext:
    recent_turns: tuple[str, ...]
    history_summary: str
    user_profile: dict[str, Any]
    candidates: tuple[ProductContext, ...]
    cart_items: tuple[CartItemContext, ...]
    pending_cart_action: dict[str, Any]


def load_planning_context(session: SessionState) -> PlanningContext:
    return PlanningContext(
        recent_turns=tuple(
            f"{turn.role}: {turn.content}"
            for turn in session.history[-MAX_CONTEXT_HISTORY_TURNS:]
            if turn.content.strip()
        ),
        history_summary=session.history_summary.strip(),
        user_profile=session.user_profile.model_dump(),
        candidates=tuple(
            compact_product_context(card)
            for card in session.candidate_product_cards[:MAX_CONTEXT_CANDIDATES]
        ),
        cart_items=tuple(
            compact_cart_item_context(item)
            for item in session.cart[:MAX_CONTEXT_CART_ITEMS]
        ),
        pending_cart_action=dict(session.pending_cart_action),
    )


def compact_product_context(card: dict) -> ProductContext:
    return ProductContext(
        id=str(card.get("id", "")).strip(),
        name=str(card.get("name", "")).strip(),
        brand=str(card.get("brand", "")).strip(),
        category=str(card.get("category", "")).strip(),
        price=format_context_price(card.get("price", "")),
    )


def compact_cart_item_context(item: dict) -> CartItemContext:
    return CartItemContext(
        product_id=str(item.get("product_id", "")).strip(),
        name=str(item.get("name", "")).strip(),
        brand=str(item.get("brand", "")).strip(),
        quantity=max(0, int(item.get("quantity", 0) or 0)),
        price=format_context_price(item.get("price", "")),
    )


def format_context_price(value: object) -> str:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if price.is_integer():
        return str(int(price))
    return f"{price:.2f}".rstrip("0").rstrip(".")


def format_recent_turns(context: PlanningContext) -> str:
    return "\n".join(context.recent_turns) if context.recent_turns else "无"


def format_candidate_context(context: PlanningContext) -> str:
    if not context.candidates:
        return "无"
    return "\n".join(
        (
            f"{index}. id={item.id}; name={item.name}; brand={item.brand}; "
            f"category={item.category}; price={item.price}"
        )
        for index, item in enumerate(context.candidates, 1)
    )


def format_cart_context(context: PlanningContext) -> str:
    if not context.cart_items:
        return "无"
    return "\n".join(
        (
            f"{index}. product_id={item.product_id}; name={item.name}; "
            f"brand={item.brand}; quantity={item.quantity}; price={item.price}"
        )
        for index, item in enumerate(context.cart_items, 1)
    )
