"""
Purpose: 查询规划回归评测脚本：读取query_plan_cases.json，检查intent、route、品类、预算、否定和引用解析。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT_DIR / "server" / "eval" / "query_plan_cases.json"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.agent.rule_signals import build_rule_signals
from server.agent.semantic_rules import build_rule_plan
from server.session.state import SessionState


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic query planning and route signals.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to query plan cases JSON.")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    known_gaps: list[dict[str, Any]] = []
    for case in cases:
        actual = evaluate_case(case)
        expected = case.get("expected", {})
        errors = compare_expected(expected, actual)
        if errors:
            item = {"id": case.get("id", ""), "query": case.get("query", ""), "errors": errors, "actual": actual}
            if case.get("status") == "known_gap":
                known_gaps.append(item)
            else:
                failures.append(item)

    passed = len(cases) - len(failures) - len(known_gaps)
    print(f"Query plan eval: {passed}/{len(cases)} passed, {len(known_gaps)} known gaps, {len(failures)} failed")
    for failure in failures:
        print_case_result("FAIL", failure)
    for gap in known_gaps:
        print_case_result("KNOWN_GAP", gap)
    return 1 if failures else 0


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    session = build_session(case.get("session", {}), case.get("id", "eval"))
    query = str(case["query"])
    plan = build_rule_plan(query, session)
    signals = build_rule_signals(query, plan, session)
    filters = [(item.kind, item.value) for item in plan.filters]
    return {
        "intent": plan.intent,
        "route": signals.route,
        "route_reasons": list(signals.reasons),
        "reference_type": plan.reference_type,
        "reference_index": plan.reference_index,
        "reference_text": plan.reference_text,
        "quantity": plan.quantity,
        "product_types": values_for_kind(filters, "product_type"),
        "exclude_product_types": values_for_kind(filters, "exclude_product_type"),
        "brands": values_for_kind(filters, "brand"),
        "exclude_brands": values_for_kind(filters, "exclude_brand"),
        "keywords": values_for_kind(filters, "keyword"),
        "exclusions": values_for_kind(filters, "exclude"),
        "max_price": first_value_for_kind(filters, "max_price"),
        "min_price": first_value_for_kind(filters, "min_price"),
    }


def build_session(payload: dict[str, Any], case_id: str) -> SessionState:
    session = SessionState(session_id=f"eval-{case_id}")
    session.candidate_product_cards = list(payload.get("shown_products", []))
    session.candidate_products = [
        str(item.get("id", ""))
        for item in session.candidate_product_cards
        if item.get("id")
    ]
    return session


def compare_expected(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, list):
            missing = [item for item in expected_value if item not in (actual_value or [])]
            if missing:
                errors.append(f"{key}: missing {missing}, actual={actual_value}")
        elif actual_value != expected_value:
            errors.append(f"{key}: expected {expected_value!r}, actual={actual_value!r}")
    return errors


def print_case_result(label: str, result: dict[str, Any]) -> None:
    print(f"\n[{label}] {result['id']}: {result['query']}")
    for error in result["errors"]:
        print(f"  - {error}")
    print(f"  actual={json.dumps(result['actual'], ensure_ascii=False, sort_keys=True)}")


def values_for_kind(filters: list[tuple[str, str]], kind: str) -> list[str]:
    return [value for item_kind, value in filters if item_kind == kind]


def first_value_for_kind(filters: list[tuple[str, str]], kind: str) -> str | None:
    values = values_for_kind(filters, kind)
    return values[0] if values else None


if __name__ == "__main__":
    raise SystemExit(main())
