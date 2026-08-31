"""
检索参数 Eval 验证脚本（Phase 4 模块 1）

用法：
    cd server
    python -m scripts.eval_retrieval_params

输出：
    控制台表格 + server/eval/retrieval_param_report.md

对比三组参数：
    10/50（旧默认） vs 5/20（新默认） vs 5/50（保守）
指标：覆盖率、平均结果数、Top-1 准确率（命中 expected product_types）
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from server.app_container import create_store
from server.config import get_settings
from server.rag.retrieval_pipeline import ProductRetrievalPipeline
from server.rag.post_process import SearchFilters


ROOT = Path(__file__).resolve().parents[2]
EVAL_CASES_PATH = ROOT / "eval" / "taxonomy_query_cases.json"
REPORT_PATH = ROOT / "eval" / "retrieval_param_report.md"


@dataclass
class ParamCombo:
    name: str
    candidate_multiplier: int
    min_candidate_pool: int


@dataclass
class EvalResult:
    param_name: str
    coverage_rate: float      # 有结果的查询 / 总查询
    avg_result_count: float   # 平均结果数
    top1_accuracy: float      # Top-1 命中预期 product_types 的比例
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float


PARAM_COMBOS = [
    ParamCombo("旧默认 10/50", 10, 50),
    ParamCombo("新默认 5/20", 5, 20),
    ParamCombo("保守 5/50", 5, 50),
]


def load_eval_cases() -> list[dict]:
    with open(EVAL_CASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def top1_matches_expected(hit: dict | None, expected: dict) -> bool:
    """判断 Top-1 结果是否命中预期的 product_types 或 categories。"""
    if not hit:
        return False
    metadata = hit.get("metadata", {})
    actual_types = set(metadata.get("product_types", []))
    actual_cats = set(metadata.get("categories", []))

    expected_types = set(expected.get("product_types", []))
    expected_cats = set(expected.get("categories", []))

    if expected_types and not expected_types & actual_types:
        return False
    if expected_cats and not expected_cats & actual_cats:
        return False
    return True


def evaluate_combo(store, combo: ParamCombo, cases: list[dict]) -> EvalResult:
    pipeline = ProductRetrievalPipeline(
        store,
        candidate_multiplier=combo.candidate_multiplier,
        min_candidate_pool=combo.min_candidate_pool,
    )

    has_result_count = 0
    top1_hit_count = 0
    result_counts = []
    latencies = []

    for case in cases:
        query = case["query"]
        expected = case.get("expected", {})

        started = time.perf_counter()
        result = pipeline.run(query=query, filters=SearchFilters(), top_k=5)
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)

        hits = result.hits
        result_counts.append(len(hits))
        if hits:
            has_result_count += 1
            if top1_matches_expected(hits[0], expected):
                top1_hit_count += 1

    total = len(cases)
    latencies_sorted = sorted(latencies)

    def percentile(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        k = (len(data) - 1) * p
        f = int(k)
        c = min(f + 1, len(data) - 1)
        if f == c:
            return data[f]
        return data[f] * (c - k) + data[c] * (k - f)

    return EvalResult(
        param_name=combo.name,
        coverage_rate=has_result_count / total,
        avg_result_count=sum(result_counts) / total,
        top1_accuracy=top1_hit_count / total,
        p50_latency_ms=percentile(latencies_sorted, 0.5),
        p95_latency_ms=percentile(latencies_sorted, 0.95),
        p99_latency_ms=percentile(latencies_sorted, 0.99),
    )


def print_table(results: list[EvalResult]) -> None:
    header = f"{'参数组合':<16} {'覆盖率':>8} {'平均结果':>8} {'Top1准':>8} {'P50(ms)':>10} {'P95(ms)':>10} {'P99(ms)':>10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.param_name:<16} "
            f"{r.coverage_rate:>7.1%} "
            f"{r.avg_result_count:>8.2f} "
            f"{r.top1_accuracy:>7.1%} "
            f"{r.p50_latency_ms:>10.1f} "
            f"{r.p95_latency_ms:>10.1f} "
            f"{r.p99_latency_ms:>10.1f}"
        )


def write_report(results: list[EvalResult]) -> None:
    lines = [
        "# 检索参数对比报告",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 测试用例数：{len(load_eval_cases())}",
        "",
        "## 结论",
        "",
    ]

    best = min(results, key=lambda r: r.p95_latency_ms)
    lines.append(f"- **推荐参数**：`{best.param_name}` — P95 延迟最低（{best.p95_latency_ms:.1f}ms）")
    lines.append(f"- **覆盖率**：全部 {results[0].coverage_rate:.1%}" if all(r.coverage_rate == results[0].coverage_rate for r in results) else "- **覆盖率**：见下表")
    lines.append("")

    lines.append("## 详细数据")
    lines.append("")
    lines.append("| 参数组合 | 覆盖率 | 平均结果数 | Top-1 准确率 | P50(ms) | P95(ms) | P99(ms) |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.param_name} | {r.coverage_rate:.1%} | {r.avg_result_count:.2f} | "
            f"{r.top1_accuracy:.1%} | {r.p50_latency_ms:.1f} | {r.p95_latency_ms:.1f} | {r.p99_latency_ms:.1f} |"
        )
    lines.append("")

    lines.append("## 配置建议")
    lines.append("")
    lines.append("```bash")
    lines.append(f"# .env 中设置（推荐：{best.param_name}）")
    lines.append(f"RETRIEVAL_CANDIDATE_MULTIPLIER={[c for c in PARAM_COMBOS if c.name == best.param_name][0].candidate_multiplier}")
    lines.append(f"RETRIEVAL_MIN_CANDIDATE_POOL={[c for c in PARAM_COMBOS if c.name == best.param_name][0].min_candidate_pool}")
    lines.append("```")
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    print("🔍 加载测试用例...")
    cases = load_eval_cases()
    print(f"   共 {len(cases)} 条测试用例")

    print("🔍 初始化向量存储...")
    settings = get_settings()
    store = create_store(settings)
    print(f"   存储类型：{store.__class__.__name__}，文档数：{store.count()}")

    results = []
    for combo in PARAM_COMBOS:
        print(f"\n▶️  测试参数：{combo.name} (multiplier={combo.candidate_multiplier}, pool={combo.min_candidate_pool})")
        result = evaluate_combo(store, combo, cases)
        results.append(result)

    print("\n" + "=" * 80)
    print_table(results)
    print("=" * 80)

    write_report(results)
    print(f"\n📝 报告已保存：{REPORT_PATH}")


if __name__ == "__main__":
    main()
