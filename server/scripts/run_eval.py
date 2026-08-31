"""
端到端评估框架（Phase 4 模块 2）

用法：
    cd server
    python -m scripts.run_eval

功能：
    1. 加载 eval/end_to_end_cases.json
    2. 对每个用例：模拟完整请求 → Orchestrator → 校验输出事件流
    3. 输出 PASS/FAIL 报告
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from server.app_container import get_orchestrator
from server.config import get_settings


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "eval" / "end_to_end_cases.json"
REPORT_PATH = ROOT / "eval" / "end_to_end_report.md"


@dataclass
class E2ECase:
    id: str
    description: str
    request: dict
    expected_events: list[str]   # 期望出现的事件类型，如 ["token", "product_card", "done"]
    forbidden_events: list[str]  # 禁止出现的事件类型，如 ["error", "guardrail"]
    expected_in_answer: list[str]  # 期望回答中包含的关键词


@dataclass
class E2EResult:
    case_id: str
    passed: bool
    missing_events: list[str]
    unexpected_events: list[str]
    missing_keywords: list[str]
    latency_ms: float
    trace: list[dict]


def load_cases() -> list[E2ECase]:
    if not CASES_PATH.exists():
        print(f"⚠️  测试用例文件不存在：{CASES_PATH}")
        print("   请从 eval/end_to_end_cases.template.json 复制并补充用例")
        return []

    with open(CASES_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [E2ECase(**item) for item in raw]


async def run_case(orch, case: E2ECase) -> E2EResult:
    req = case.request
    events_seen = []
    answer_text = ""
    trace = []

    started = time.perf_counter()
    async for item in orch.stream_chat(
        session_id=req.get("session_id", f"eval_{case.id}"),
        user_message=req.get("message", ""),
        image_base64=req.get("image_base64", ""),
    ):
        trace.append(item)
        event = item.get("event", "")
        events_seen.append(event)
        if event == "token":
            answer_text += item.get("data", {}).get("content", "")

    latency_ms = (time.perf_counter() - started) * 1000

    missing_events = [e for e in case.expected_events if e not in events_seen]
    unexpected_events = [e for e in case.forbidden_events if e in events_seen]
    missing_keywords = [kw for kw in case.expected_in_answer if kw not in answer_text]

    passed = not missing_events and not unexpected_events and not missing_keywords

    return E2EResult(
        case_id=case.id,
        passed=passed,
        missing_events=missing_events,
        unexpected_events=unexpected_events,
        missing_keywords=missing_keywords,
        latency_ms=latency_ms,
        trace=trace,
    )


async def main() -> None:
    print("🧪 端到端评估框架")
    print("-" * 40)

    cases = load_cases()
    if not cases:
        return

    print(f"📋 加载 {len(cases)} 条测试用例")
    print("🔧 初始化 Orchestrator...")
    settings = get_settings()
    orch = get_orchestrator()

    results = []
    for case in cases:
        print(f"\n▶️  [{case.id}] {case.description}")
        result = await run_case(orch, case)
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"   {status} ({result.latency_ms:.0f}ms)")
        if result.missing_events:
            print(f"   缺失事件：{result.missing_events}")
        if result.unexpected_events:
            print(f"   异常事件：{result.unexpected_events}")
        if result.missing_keywords:
            print(f"   缺失关键词：{result.missing_keywords}")
        results.append(result)

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print("\n" + "=" * 40)
    print(f"结果：{passed}/{total} 通过 ({passed/total:.1%})")
    print("=" * 40)

    # 写报告
    lines = [
        "# 端到端评估报告",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 通过：{passed}/{total} ({passed/total:.1%})",
        "",
        "## 详细结果",
        "",
        "| 用例 | 状态 | 延迟(ms) | 缺失事件 | 异常事件 | 缺失关键词 |",
        "|---|---|---|---|---|---|"]
    for r in results:
        status = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.case_id} | {status} | {r.latency_ms:.0f} | "
            f"{', '.join(r.missing_events) or '-'} | "
            f"{', '.join(r.unexpected_events) or '-'} | "
            f"{', '.join(r.missing_keywords) or '-'} |"
        )
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n📝 报告已保存：{REPORT_PATH}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
