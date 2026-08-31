"""
Purpose: 类目回归评测脚本：读取taxonomy_query_cases.json，验证类目分类准确性。
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT_DIR / "server" / "eval" / "taxonomy_query_cases.json"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.nlu.taxonomy_classifier import classify_taxonomy_query
from server.rag.taxonomy_governance import build_taxonomy_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate taxonomy query classification coverage.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--failures", default=str(ROOT_DIR / "server" / "runtime" / "taxonomy_eval_failures.jsonl"))
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    for case in cases:
        actual = evaluate_case(case)
        errors = compare_case(case.get("expected", {}), actual)
        if errors:
            failures.append(
                {
                    "id": case.get("id", ""),
                    "query": case.get("query", ""),
                    "errors": errors,
                    "actual": actual,
                }
            )

    passed = len(cases) - len(failures)
    summary = {
        "manifest": build_taxonomy_manifest(product_data_path=ROOT_DIR / "data" / "products_ref.json").as_metadata(),
        "total": len(cases),
        "passed": passed,
        "failed": len(failures),
        "pass_rate": passed / len(cases) if cases else 0.0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    failure_path = Path(args.failures)
    failure_path.parent.mkdir(parents=True, exist_ok=True)
    failure_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in failures),
        encoding="utf-8",
    )
    for failure in failures:
        print(f"[FAIL] {failure['id']}: {failure['query']}")
        for error in failure["errors"]:
            print(f"  - {error}")
    return 1 if failures else 0


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    result = classify_taxonomy_query(str(case["query"]))
    return {
        "product_types": [item.value for item in result.product_types],
        "categories": [item.value for item in result.categories],
        "sources": [item.source for item in (*result.product_types, *result.categories)],
        "used_embedding": result.used_embedding,
    }


def compare_case(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["product_types", "categories"]:
        if key not in expected:
            continue
        expected_values = list(expected.get(key) or [])
        actual_values = list(actual.get(key) or [])
        missing = [item for item in expected_values if item not in actual_values]
        unexpected = [item for item in actual_values if item not in expected_values]
        if missing:
            errors.append(f"{key}: missing {missing}, actual={actual_values}")
        if expected.get(f"exact_{key}", True) and unexpected:
            errors.append(f"{key}: unexpected {unexpected}, expected={expected_values}")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
