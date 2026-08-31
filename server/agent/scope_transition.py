"""
Purpose: 商品范围切换策略：检测到品类切换时清理旧过滤条件，避免旧品类/预算污染新需求。
"""

from dataclasses import dataclass
from typing import Literal

from server.agent.semantic_schema import SemanticPlan
from server.session.state import FilterCondition, SessionState, product_scope_values


ScopeTransitionType = Literal[
    "continue_refinement",
    "replace_product_scope",
    "refine_product_scope",
    "expand_product_scope",
    "compare_scope",
    "bundle_new_task",
    "bundle_with_seed",
    "cart_action",
    "uncertain",
]


@dataclass(frozen=True)
class ScopeTransition:
    transition_type: ScopeTransitionType
    reason: str
    confidence: float
    reset_fields: tuple[str, ...] = ()
    preserve_fields: tuple[str, ...] = ("history", "user_profile", "cart")
    incoming_scope_values: tuple[str, ...] = ()
    existing_scope_values: tuple[str, ...] = ()
    seed_product_ids: tuple[str, ...] = ()

    def as_metadata(self) -> dict:
        return {
            "type": self.transition_type,
            "reason": self.reason,
            "confidence": self.confidence,
            "from_scope": infer_scope_name(self.existing_scope_values),
            "to_scope": transition_target_scope(self.transition_type, self.incoming_scope_values, self.existing_scope_values),
            "clear": list(self.reset_fields),
            "preserve": list(self.preserve_fields),
            "reset_fields": list(self.reset_fields),
            "preserve_fields": list(self.preserve_fields),
            "incoming_scope_values": list(self.incoming_scope_values),
            "existing_scope_values": list(self.existing_scope_values),
            "seed_product_ids": list(self.seed_product_ids),
        }


class ScopeTransitionPolicy:
    def decide(
        self,
        message: str,
        plan: SemanticPlan,
        session: SessionState,
        incoming_filters: list[FilterCondition],
    ) -> ScopeTransition:
        incoming_scopes = tuple(product_scope_values(incoming_filters))
        existing_scopes = tuple(product_scope_values(session.filters))
        seed_ids = tuple(str(item) for item in session.candidate_products if item)
        has_product_state = session.has_product_scoped_state()

        if plan.intent == "cart":
            return ScopeTransition(
                transition_type="cart_action",
                reason="cart operation should use existing candidates/cart state",
                confidence=0.96,
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if plan.intent == "compare":
            return ScopeTransition(
                transition_type="compare_scope",
                reason="comparison turns preserve current candidate context",
                confidence=0.88,
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if plan.intent == "bundle":
            if self._is_seeded_bundle(message, plan, session):
                return ScopeTransition(
                    transition_type="bundle_with_seed",
                    reason="bundle request references previous candidates as seeds",
                    confidence=0.84,
                    reset_fields=("filters", "exclusions", "pending_subject", "pending_cart_action"),
                    incoming_scope_values=incoming_scopes,
                    existing_scope_values=existing_scopes,
                    seed_product_ids=seed_ids,
                )
            return ScopeTransition(
                transition_type="bundle_new_task",
                reason="bundle request starts a scenario-level shopping task",
                confidence=0.9,
                reset_fields=(
                    "filters",
                    "exclusions",
                    "candidate_products",
                    "candidate_product_cards",
                    "pending_subject",
                    "pending_cart_action",
                ),
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if not incoming_scopes:
            if not has_product_state and not incoming_filters:
                return ScopeTransition(
                    transition_type="uncertain",
                    reason="no structured product scope is available yet",
                    confidence=0.58,
                    incoming_scope_values=incoming_scopes,
                    existing_scope_values=existing_scopes,
                    seed_product_ids=seed_ids,
                )
            return ScopeTransition(
                transition_type="continue_refinement",
                reason="turn has no new product scope and refines existing context",
                confidence=0.82,
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if self._has_expand_marker(message):
            return ScopeTransition(
                transition_type="expand_product_scope",
                reason="user explicitly asks to add another product scope",
                confidence=0.76,
                reset_fields=("candidate_products", "candidate_product_cards", "pending_subject", "pending_cart_action"),
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if not existing_scopes:
            if has_product_state:
                return ScopeTransition(
                    transition_type="replace_product_scope",
                    reason="explicit product scope replaces keyword-only product context",
                    confidence=0.82,
                    reset_fields=(
                        "filters",
                        "exclusions",
                        "candidate_products",
                        "candidate_product_cards",
                        "pending_subject",
                        "pending_cart_action",
                    ),
                    incoming_scope_values=incoming_scopes,
                    existing_scope_values=existing_scopes,
                    seed_product_ids=seed_ids,
                )
            return ScopeTransition(
                transition_type="continue_refinement",
                reason="first structured product scope for this session",
                confidence=0.78,
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if set(incoming_scopes) == set(existing_scopes):
            if self._looks_like_fresh_product_request(message) and not self._has_refinement_marker(message):
                return ScopeTransition(
                    transition_type="replace_product_scope",
                    reason="same product scope is restated as a fresh request, so stale constraints are released",
                    confidence=0.8,
                    reset_fields=(
                        "filters",
                        "exclusions",
                        "candidate_products",
                        "candidate_product_cards",
                        "pending_subject",
                        "pending_cart_action",
                    ),
                    incoming_scope_values=incoming_scopes,
                    existing_scope_values=existing_scopes,
                    seed_product_ids=seed_ids,
                )
            return ScopeTransition(
                transition_type="continue_refinement",
                reason="same product scope is being refined",
                confidence=0.78,
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        if self._is_scope_refinement(existing_scopes, incoming_scopes) and self._has_refinement_marker(message):
            return ScopeTransition(
                transition_type="refine_product_scope",
                reason="new scope narrows an existing broader scope",
                confidence=0.74,
                reset_fields=("candidate_products", "candidate_product_cards", "pending_subject", "pending_cart_action"),
                incoming_scope_values=incoming_scopes,
                existing_scope_values=existing_scopes,
                seed_product_ids=seed_ids,
            )

        return ScopeTransition(
            transition_type="replace_product_scope",
            reason="new structured product scope is unrelated to current scope",
            confidence=0.86,
            reset_fields=(
                "filters",
                "exclusions",
                "candidate_products",
                "candidate_product_cards",
                "pending_subject",
                "pending_cart_action",
            ),
            incoming_scope_values=incoming_scopes,
            existing_scope_values=existing_scopes,
            seed_product_ids=seed_ids,
        )

    def apply(self, session: SessionState, transition: ScopeTransition) -> None:
        if transition.transition_type in {"replace_product_scope", "bundle_new_task"}:
            session.reset_product_scope()
            return
        if transition.transition_type == "bundle_with_seed":
            session.clear_product_constraints()
            return
        if transition.transition_type in {"refine_product_scope", "expand_product_scope"}:
            session.clear_product_candidates()

    def _is_seeded_bundle(self, message: str, plan: SemanticPlan, session: SessionState) -> bool:
        if not session.candidate_product_cards and not session.candidate_products:
            return False
        if plan.reference_type != "none":
            return True
        return any(
            marker in message
            for marker in [
                "给它搭",
                "围绕它",
                "基于刚才",
                "刚才那款",
                "这款搭",
                "和它搭",
                "配上它",
                "搭配它",
            ]
        )

    def _has_expand_marker(self, message: str) -> bool:
        return any(
            marker in message
            for marker in [
                "也看看",
                "也看",
                "还看看",
                "还想看",
                "顺便看",
                "不只",
                "不限于",
                "同时看",
                "一起看",
            ]
        )

    def _has_refinement_marker(self, message: str) -> bool:
        return any(
            marker in message
            for marker in [
                "只看",
                "仅看",
                "限定",
                "换成",
                "改成",
                "筛",
                "筛选",
                "不要",
                "排除",
                "更便宜",
                "便宜一点",
                "贵一点",
                "预算",
                "以内",
                "以下",
                "以上",
                "左右",
            ]
        )

    def _looks_like_fresh_product_request(self, message: str) -> bool:
        return any(
            marker in message
            for marker in [
                "推荐",
                "我想看",
                "想看",
                "帮我找",
                "找一",
                "买一",
                "来一",
                "给我",
                "看看",
            ]
        )

    def _is_scope_refinement(self, existing_scopes: tuple[str, ...], incoming_scopes: tuple[str, ...]) -> bool:
        existing_items = [_parse_scope(item) for item in existing_scopes]
        incoming_items = [_parse_scope(item) for item in incoming_scopes]
        for existing_kind, existing_value in existing_items:
            for incoming_kind, incoming_value in incoming_items:
                if existing_kind == incoming_kind and existing_value == incoming_value:
                    return True
                if existing_kind == "category" and incoming_kind == "product_type":
                    if _root_namespace(existing_value) and _root_namespace(existing_value) == _root_namespace(incoming_value):
                        return True
                if existing_kind == "category" and incoming_kind == "category":
                    if incoming_value.startswith(f"{existing_value}."):
                        return True
                if existing_kind == "product_type" and incoming_kind == "category":
                    if existing_value.startswith(f"{incoming_value}."):
                        return True
                if existing_kind == "category" and incoming_value.startswith(f"{existing_value}."):
                    return True
        return False


def _parse_scope(scope: str) -> tuple[str, str]:
    if ":" not in scope:
        return "", scope
    return scope.split(":", 1)


def _root_namespace(value: str) -> str:
    return value.split(".", 1)[0] if "." in value else ""


def infer_scope_name(scope_values: tuple[str, ...]) -> str:
    if not scope_values:
        return "open_conversation"
    if len(scope_values) == 1:
        return "single_product_recommendation"
    return "multi_scope_recommendation"


def transition_target_scope(
    transition_type: ScopeTransitionType,
    incoming_scope_values: tuple[str, ...],
    existing_scope_values: tuple[str, ...],
) -> str:
    if transition_type in {"cart_action"}:
        return "cart_operation"
    if transition_type == "compare_scope":
        return "comparison"
    if transition_type in {"bundle_new_task", "bundle_with_seed"}:
        return "bundle_recommendation"
    if transition_type == "uncertain":
        return "clarification"
    if transition_type == "continue_refinement" and not incoming_scope_values:
        return infer_scope_name(existing_scope_values)
    return infer_scope_name(incoming_scope_values or existing_scope_values)
