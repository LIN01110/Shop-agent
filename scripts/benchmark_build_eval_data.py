"""
Purpose: 从 products_ref.json 自动构建检索评测数据集（查询 + 规则标注的相关商品）。
标注原则：相关性全部由商品字段规则判定（类目/子类目/品牌/价格），不引入人工主观标注，
保证可复现。查询类型覆盖：子类目直查、品牌+子类目、自然语言、价格约束、大类浏览。
"""

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from benchmark_common import EVAL_DIR, load_products

random.seed(42)

# 自然语言查询模板：按子类目给出更贴近真实用户的表达方式
NATURAL_TEMPLATES: dict[str, list[str]] = {
    "真无线耳机": ["我想买一副降噪蓝牙耳机", "有没有适合通勤的无线耳机"],
    "智能手机": ["想换一部拍照好的手机", "推荐一款续航强的手机"],
    "笔记本电脑": ["大学生买什么笔记本电脑比较合适", "有没有适合办公的轻薄本"],
    "平板电脑": ["买台平板用来看剧和上网课"],
    "跑步鞋": ["推荐一双适合日常跑步的鞋", "晨跑穿什么鞋比较好"],
    "篮球鞋": ["打外场篮球穿什么鞋耐磨"],
    "徒步鞋": ["周末去爬山需要一双防滑的鞋"],
    "卫衣": ["秋天想买件宽松的卫衣"],
    "精华": ["熬夜多，想买抗老精华", "有什么保湿精华推荐"],
    "面霜": ["冬天皮肤干燥，推荐一款保湿面霜", "敏感肌能用的面霜有哪些"],
    "眼霜": ["熬夜黑眼圈重，推荐一款眼霜"],
    "防晒": ["夏天户外用的防晒霜推荐"],
    "面膜": ["补水面膜哪款好用"],
    "咖啡": ["提神的速溶咖啡推荐", "上班族喝什么咖啡方便"],
    "功能饮料": ["加班熬夜喝什么功能饮料提神"],
    "牛奶": ["早餐喝的高钙牛奶推荐"],
    "坚果/零食": ["办公室囤点什么坚果零食好"],
    "方便食品": ["宿舍囤货，有什么方便食品推荐"],
}

PRICE_QUERY_TEMPLATES = [
    "{sub}，预算{budget}元以内",
    "{budget}元以内的{sub}有什么推荐",
]


def build_queries(products: list[dict]) -> list[dict]:
    by_sub: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_cat: dict[str, list[dict]] = defaultdict(list)
    by_brand_sub: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for product in products:
        by_sub[(product["category"], product["sub_category"])].append(product)
        by_cat[product["category"]].append(product)
        by_brand_sub[(product["category"], product["sub_category"], product["brand"])].append(product)

    queries: list[dict] = []
    counter = 0

    def add(query: str, query_type: str, relevant: list[dict], note: str) -> None:
        nonlocal counter
        if not relevant:
            return
        counter += 1
        queries.append({
            "query_id": f"q{counter:03d}",
            "query": query,
            "query_type": query_type,
            "relevant_ids": [p["id"] for p in relevant],
            "note": note,
        })

    # 1. 子类目直查（商品数 >= 2 的子类目）
    for (category, sub), items in sorted(by_sub.items()):
        if len(items) < 2:
            continue
        add(f"{sub}推荐", "subcategory", items, f"类目={category}/子类目={sub}")

    # 2. 品牌 + 子类目（限制 15 条，优先商品多的组合）
    combos = sorted(by_brand_sub.items(), key=lambda kv: -len(kv[1]))[:15]
    for (category, sub, brand), items in combos:
        add(f"{brand}的{sub}", "brand_sub", items, f"品牌={brand}/子类目={sub}")

    # 3. 自然语言查询
    for sub, templates in NATURAL_TEMPLATES.items():
        items = [p for (cat, s), ps in by_sub.items() if s == sub for p in ps]
        if not items:
            continue
        for template in templates:
            add(template, "natural", items, f"自然语言→子类目={sub}")

    # 4. 价格约束查询：取子类目内价格中位数附近做预算
    price_candidates = [(k, v) for k, v in sorted(by_sub.items()) if len(v) >= 2]
    random.shuffle(price_candidates)
    made = 0
    for (category, sub), items in price_candidates:
        if made >= 10:
            break
        prices = sorted(p["price"] for p in items)
        budget = round(prices[len(prices) // 2] * 1.1, -1)  # 中位数上浮10%取整
        relevant = [p for p in items if p["price"] <= budget]
        if not relevant:
            continue
        template = PRICE_QUERY_TEMPLATES[made % len(PRICE_QUERY_TEMPLATES)]
        add(template.format(sub=sub, budget=int(budget)), "price", relevant, f"子类目={sub}/预算<={int(budget)}")
        made += 1

    # 5. 大类浏览
    for category, items in sorted(by_cat.items()):
        add(f"{category}有什么值得买的", "category", items, f"大类={category}")

    return queries


def main() -> None:
    products = load_products()
    queries = build_queries(products)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVAL_DIR / "retrieval_queries.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for query in queries:
            fh.write(json.dumps(query, ensure_ascii=False) + "\n")

    type_counts: dict[str, int] = defaultdict(int)
    for query in queries:
        type_counts[query["query_type"]] += 1
    print(f"商品数: {len(products)}")
    print(f"生成查询: {len(queries)} 条 → {out_path}")
    for query_type, count in sorted(type_counts.items()):
        print(f"  {query_type}: {count}")


if __name__ == "__main__":
    main()
