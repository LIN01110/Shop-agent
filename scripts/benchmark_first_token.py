"""
Purpose: 首Token延迟压测脚本：测量/chat接口返回第一个token的延迟，验证是否满足1秒要求。
"""

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from typing import Any

import httpx


DEFAULT_CASES = [
    {"name": "eye_cream", "message": "推荐一款保湿眼霜，预算250以内"},
    {"name": "sports_shoes", "message": "推荐一款运动鞋"},
    {"name": "sanya_bundle", "message": "下周去三亚度假，帮我搭配一套从防晒到穿搭的方案"},
    {"name": "phone_clarify", "message": "推荐一款手机"},
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark SSE perceived and useful latency for /chat.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/chat")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--threshold-ms", type=float, default=1000.0)
    parser.add_argument(
        "--stop-after-first-useful",
        action="store_true",
        help="Close the stream after the first non-empty token. Full-response metrics will be unavailable.",
    )
    parser.add_argument(
        "--wait-done",
        action="store_true",
        help="Deprecated compatibility flag. The benchmark waits for done by default.",
    )
    parser.add_argument("--case-file", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    cases = load_cases(args.case_file) if args.case_file else DEFAULT_CASES
    results = []
    print(
        "case\truns\tp50_useful_ms\tp95_useful_ms\tavg_useful_ms\t"
        "p50_product_ms\tp95_done_ms\tpass"
    )
    for case in cases:
        for index in range(args.warmup):
            session_id = f"latency-warmup-{case['name']}-{index}-{int(time.time() * 1000)}"
            measure_chat_latency(args.url, session_id, case, stop_after_first_useful=args.stop_after_first_useful)

        runs = []
        for index in range(args.runs):
            session_id = f"latency-{case['name']}-{index}-{int(time.time() * 1000)}"
            runs.append(
                measure_chat_latency(args.url, session_id, case, stop_after_first_useful=args.stop_after_first_useful)
            )

        first_useful = summarize([item["first_useful_token_ms"] for item in runs])
        first_product = summarize_optional([item.get("first_product_card_ms") for item in runs])
        done = summarize_optional([item.get("done_ms") for item in runs])
        passed = first_useful["p95_ms"] <= args.threshold_ms
        results.append(
            {
                "case": case["name"],
                "message": case["message"],
                "runs": args.runs,
                "warmup": args.warmup,
                "threshold_ms": args.threshold_ms,
                "first_status": summarize_optional([item.get("first_status_ms") for item in runs]),
                "first_useful_token": first_useful,
                "first_product_card": first_product,
                "done": done,
                "completed_runs": sum(1 for item in runs if item.get("completed")),
                "pass": passed,
            }
        )
        print(
            f"{case['name']}\t{args.runs}\t{first_useful['p50_ms']:.2f}\t{first_useful['p95_ms']:.2f}\t"
            f"{first_useful['avg_ms']:.2f}\t{format_optional_metric(first_product, 'p50_ms')}\t"
            f"{format_optional_metric(done, 'p95_ms')}\t{passed}"
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "threshold_ms": args.threshold_ms,
        "warmup": args.warmup,
        "results": results,
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\nWrote report to {args.output}")

    return 0 if all(item["pass"] for item in results) else 1


def measure_chat_latency(
    url: str,
    session_id: str,
    case: dict[str, Any],
    *,
    stop_after_first_useful: bool,
) -> dict[str, float | bool | None]:
    payload = {
        "session_id": session_id,
        "message": case["message"],
    }
    if case.get("image_base64"):
        payload["image_base64"] = case["image_base64"]
        payload["image_mime_type"] = case.get("image_mime_type", "")
        payload["image_filename"] = case.get("image_filename", "")

    started = time.perf_counter()
    metrics: dict[str, float | bool | None] = {
        "first_status_ms": None,
        "first_useful_token_ms": None,
        "first_product_card_ms": None,
        "done_ms": None,
        "completed": False,
    }
    current_event = ""
    with httpx.stream(
        "POST",
        url,
        json=payload,
        timeout=httpx.Timeout(120.0, read=120.0),
        trust_env=False,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                current_event = line.removeprefix("event: ").strip()
                continue
            if not line.startswith("data: "):
                continue

            now_ms = (time.perf_counter() - started) * 1000
            data = parse_sse_json(line.removeprefix("data: "))
            if current_event == "status" and metrics["first_status_ms"] is None:
                metrics["first_status_ms"] = now_ms
            elif current_event == "token" and metrics["first_useful_token_ms"] is None:
                if str(data.get("text", "")).strip():
                    metrics["first_useful_token_ms"] = now_ms
                    if stop_after_first_useful:
                        break
            elif current_event == "product_card" and metrics["first_product_card_ms"] is None:
                metrics["first_product_card_ms"] = now_ms
            elif current_event == "done":
                metrics["done_ms"] = now_ms
                metrics["completed"] = True
                break

    total_ms = (time.perf_counter() - started) * 1000
    if metrics["first_useful_token_ms"] is None:
        metrics["first_useful_token_ms"] = total_ms
    if metrics["done_ms"] is None and not stop_after_first_useful:
        metrics["done_ms"] = total_ms
    return metrics


def parse_sse_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_cases(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize_optional(values: list[float | None]) -> dict[str, float] | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return summarize(present)


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "avg_ms": statistics.fmean(values),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def percentile(values: list[float], percentile_value: int) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def format_optional_metric(summary: dict[str, float] | None, key: str) -> str:
    if summary is None:
        return "n/a"
    return f"{summary[key]:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
