"""
Purpose: 购物车操作解析：从自然语言中解析具体购物车操作（来两件/删除/第二个）。
"""

import re
from dataclasses import dataclass
from typing import Literal

from server.nlu.quantity import (
    extract_ordinal_index,
    extract_quantity,
    extract_quantity_delta,
    extract_quantity_parse,
    is_add_quantity_expression,
)


CartOperationAction = Literal["add", "remove", "update_quantity", "view", "checkout", "clear"]


@dataclass(frozen=True)
class CartOperation:
    action: CartOperationAction
    segment: str
    target_index: int | None = None
    quantity: int | None = None
    quantity_unit: str | None = None
    quantity_delta: int = 0


def parse_cart_operations(message: str, plan=None) -> list[CartOperation]:
    segments = split_operation_segments(message)
    operations = [operation for segment in segments if (operation := parse_cart_operation(segment, plan))]
    if operations:
        return operations

    plan_action = getattr(plan, "cart_action", "none")
    if plan_action and plan_action != "none":
        parsed_quantity = extract_quantity_parse(message)
        return [
            CartOperation(
                action=normalize_plan_action(plan_action),
                segment=message.strip(),
                target_index=extract_ordinal_index(message),
                quantity=getattr(plan, "quantity", None) or (parsed_quantity.value if parsed_quantity else None),
                quantity_unit=parsed_quantity.unit if parsed_quantity else None,
                quantity_delta=extract_quantity_delta(message),
            )
        ]
    parsed_quantity = extract_quantity_parse(message)
    return [
        CartOperation(
            action="add",
            segment=message.strip(),
            quantity=parsed_quantity.value if parsed_quantity else None,
            quantity_unit=parsed_quantity.unit if parsed_quantity else None,
        )
    ]


def split_operation_segments(message: str) -> list[str]:
    text = message.strip()
    if not text:
        return []
    pieces = re.split(r"(?:，|,|；|;|。|\s*(?:然后|接着|并且|同时|再)\s*)", text)
    return [piece.strip() for piece in pieces if piece.strip()]


def parse_cart_operation(segment: str, plan=None) -> CartOperation | None:
    action = infer_operation_action(segment, plan)
    if action is None:
        return None
    parsed_quantity = extract_quantity_parse(segment)
    return CartOperation(
        action=action,
        segment=segment,
        target_index=extract_ordinal_index(segment),
        quantity=parsed_quantity.value if parsed_quantity else None,
        quantity_unit=parsed_quantity.unit if parsed_quantity else None,
        quantity_delta=extract_quantity_delta(segment),
    )


def infer_operation_action(segment: str, plan=None) -> CartOperationAction | None:
    if any(word in segment for word in ["下单", "结算", "提交订单"]):
        return "checkout"
    if any(word in segment for word in ["清空购物车", "购物车清空", "全部删除", "全删", "清空"]):
        return "clear"
    if any(word in segment for word in ["查看购物车", "购物车里", "购物车有什么", "购物车"]) and not is_add_expression(segment):
        return "view"
    if any(word in segment for word in ["删除", "删掉", "移除", "去掉", "拿掉", "不要这个"]):
        return "remove"
    if any(word in segment for word in ["数量", "改成", "改为", "设为", "加一件", "减一件", "多加", "少一件"]):
        return "update_quantity"
    if is_add_expression(segment):
        return "add"

    plan_action = getattr(plan, "cart_action", "none")
    if plan_action and plan_action != "none":
        return normalize_plan_action(plan_action)
    return None


def normalize_plan_action(action: str) -> CartOperationAction:
    if action in {"add", "remove", "update_quantity", "view", "checkout"}:
        return action  # type: ignore[return-value]
    return "add"


def is_add_expression(segment: str) -> bool:
    return is_add_quantity_expression(segment)
