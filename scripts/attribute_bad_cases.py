"""
Purpose: Bad Case 归因分析：读取检索评测报告（evaluate_esci_retrieval.py 输出），
         把 miss 查询按 6 类归因自动分类，输出归因分布与回归集。

归因类别（与面试话术对齐）:
  recall_missing    召回缺失：正例根本不在候选池（语义鸿沟/词表外表达）
  ranking_error     排序错误：正例进了候选但排在 top-k 之外
  near_duplicate    近似重复干扰：正例存在，但 top-k 被同款不同规格商品占满
  query_ambiguity   查询歧义：查询过短/过泛，命中多个语义簇
  brand_fragment    品牌碎片：查询主要由品牌词构成，缺少区分性
  generation_other  其他/未归类

用法:
  python scripts/attribute_bad_cases.py --report data/benchmarks/abo_full/report_bge_80k.json \
    --products data/benchmarks/abo_full/products.json --output server/eval/bad_case_regression_abo.json
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

CJK = re.compile(r"[一-鿿]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Attribute retrieval bad cases into 6 categories.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--queries", default="", help="queries.jsonl，用于按 query_id 找回正例 id")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    products = {p["id"]: p for p in json.loads(Path(args.products).read_text(encoding="utf-8"))}

    # miss_examples 只含 top-k retrieved_ids，正例 id 需从 queries.jsonl 按 query_id 关联
    positives_by_qid: dict[str, list[str]] = {}
    queries_path = args.queries or str(Path(args.report).parent / "queries.jsonl")
    if Path(queries_path).exists():
        for line in Path(queries_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                positives_by_qid[str(item.get("query_id"))] = item.get("positive_product_ids", [])

    cases = []
    for miss in report.get("miss_examples") or []:
        miss = dict(miss)
        miss["positive_product_ids"] = positives_by_qid.get(str(miss.get("query_id")), [])
        cases.append(miss)
    if not cases:
        print("no bad cases found in report")
        return 0

    attributed = []
    for case in cases:
        category, reason = attribute(case, products, top_k=args.top_k)
        attributed.append(
            {
                "query_id": case.get("query_id"),
                "query": case.get("query"),
                "positive_product_ids": case.get("positive_product_ids", []),
                "retrieved_ids": case.get("retrieved_ids", [])[: args.top_k],
                "attribution": category,
                "reason": reason,
            }
        )

    distribution = Counter(item["attribution"] for item in attributed)
    output = {
        "source_report": args.report,
        "total_bad_cases": len(attributed),
        "distribution": dict(distribution.most_common()),
        "cases": attributed,
    }
    output_path = Path(args.output) if args.output else Path(args.report).with_name("bad_case_attribution.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"bad_cases={len(attributed)}")
    for category, count in distribution.most_common():
        print(f"  {category}: {count} ({count / len(attributed):.1%})")
    print(f"Wrote {output_path}")
    return 0


def attribute(case: dict, products: dict, *, top_k: int) -> tuple[str, str]:
    query = str(case.get("query", ""))
    positive_ids = case.get("positive_product_ids", [])
    retrieved_ids = case.get("retrieved_ids", [])

    # 排序错误：正例在召回列表里但超出 top-k（报告里 retrieved_ids 为完整候选时才能判断）
    if any(pid in retrieved_ids[top_k:] for pid in positive_ids):
        return "ranking_error", "正例进入候选池但排在 top-k 之外"

    # 近似重复干扰：top-k 命中与正例同品牌同品类的商品占比过半
    if positive_ids and retrieved_ids:
        positive = products.get(positive_ids[0], {})
        p_key = (positive.get("brand"), positive.get("sub_category"))
        same_family = sum(
            1
            for rid in retrieved_ids[:top_k]
            if (products.get(rid, {}).get("brand"), products.get(rid, {}).get("sub_category")) == p_key
        )
        if p_key[0] and same_family >= max(2, top_k // 2):
            return "near_duplicate", f"top-{top_k} 中 {same_family} 个与正例同品牌同品类（近似重复干扰）"

    # 品牌碎片：去掉品牌词后 CJK 不足 4 个
    if positive_ids:
        brand = str(products.get(positive_ids[0], {}).get("brand", ""))
        stripped = query.replace(brand.split("(")[0], "") if brand and brand != "Unknown" else query
        if len(CJK.findall(stripped)) < 4:
            return "brand_fragment", "查询去除品牌词后区分性不足"

    # 查询歧义：查询很短
    if len(query) <= 8:
        return "query_ambiguity", "查询过短，命中多个语义簇"

    return "recall_missing", "正例未进入候选池（语义鸿沟或词表外表达）"


if __name__ == "__main__":
    raise SystemExit(main())
