"""
Purpose: LLM-as-Judge 评测（自洽版）。

背景：results/hallucination.json 是历史产物，其回答引用的价格与当前 products_ref.json 不一致，
直接评判会全部误判。本脚本改为：用当前商品库重新生成 pure_llm / rag 两臂回答（DeepSeek），
对每条回答同时做 ①规则硬违规检测（与 GroundingGuard 核心规则对齐的轻量实现）
②LLM 三维度打分 + 二值违规判定，最后计算 Cohen's Kappa 一致性。

简化说明（面试时请如实讲）：rag 臂的商品卡按类目取前 5 个，未走向量检索；
评测重点是"LLM 裁判与规则守卫的一致性"，不是检索质量。

用法（需 .env 配置 DEEPSEEK_API_KEY）:
  python scripts/evaluate_llm_judge.py            # 断点续跑，直到 24 条 × 2 臂全部完成
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

QUESTIONS_SOURCE = ROOT_DIR / "results" / "hallucination.json"
PRODUCTS = ROOT_DIR / "data" / "products_ref.json"
DEFAULT_OUTPUT = ROOT_DIR / "results" / "llm_judge.json"

ARMS = ["pure_llm", "rag"]

RAG_PROMPT = """你是电商导购助手。只能基于以下候选商品回答，不得编造商品、价格、优惠、库存、功效。
候选商品：
{cards}
用户问题：{question}
像真实导购一样回答：先给最推荐的一款及理由，再给备选差异。完整自然句，不用 Markdown。"""

PURE_LLM_PROMPT = """用户问题：{question}
请像电商导购一样推荐一款商品并说明理由。完整自然句，不用 Markdown。"""

JUDGE_PROMPT = """你是电商导购回答的严格评审。基于【商品证据】评估【回答】。

【商品证据】（商品名: 价格元）
{evidence}

【用户问题】{question}

【回答】{answer}

请只输出 JSON，不要输出其他内容：
{{
  "faithfulness": 1-5,   // 事实忠实度：价格/品牌/商品是否存在且与证据一致
  "relevance": 1-5,      // 推荐理由相关性：推荐理由是否回应问题、与商品定位相符
  "fluency": 1-5,        // 话术自然度：是否像真实导购、表达完整自然
  "has_violation": true/false,  // 仅判定硬违规：①价格与证据不符 ②推荐证据中不存在的商品 ③编造优惠/满减/库存/销量 ④医疗或绝对化承诺。性能体验类主观描述不算违规
  "violation_reason": "一句话说明，无则空串"
}}"""

FORBIDDEN_PATTERNS = ["优惠", "满减", "折扣", "库存", "销量", "好评率", "根治", "治疗", "100%", "保证"]
PRICE_RE = re.compile(r"(\d{3,6}(?:\.\d{1,2})?)\s*元")


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-as-Judge with self-consistent fresh answers.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--recompute-rules", action="store_true", help="不重调 API，仅重算规则判定与汇总")
    args = parser.parse_args()

    env = load_env(ROOT_DIR / ".env")
    api_key = env.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    cfg = {
        "api_key": api_key,
        "base_url": env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        "model": env.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "judge_model": env.get("DEEPSEEK_JUDGE_MODEL", "deepseek-v4-pro"),
    }

    questions = [
        {"question": r["question"], "category": r["category"]}
        for r in json.loads(QUESTIONS_SOURCE.read_text(encoding="utf-8"))["records"]
    ]
    if args.limit > 0:
        questions = questions[: args.limit]
    products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
    evidence = "\n".join(f"- {p['name']}: {p['price']}元" for p in products)
    category_by_question = {q["question"]: q["category"] for q in questions}

    judged = load_partial(Path(args.output))

    if args.recompute_rules:
        for item in judged:
            category = category_by_question.get(item["question"], "")
            cards = [p for p in products if p["category"] == category][:5]
            item["rule_violation"] = rule_check(item["answer"], cards, item["arm"], item["question"])
        summary = summarize(judged)
        save_partial(Path(args.output), judged, cfg, summary=summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    done_keys = {(j["question"], j["arm"]) for j in judged}

    for i, q in enumerate(questions):
        cards = [p for p in products if p["category"] == q["category"]][:5]
        for arm in ARMS:
            if (q["question"], arm) in done_keys:
                continue
            answer = generate_answer(cfg, q["question"], cards, arm)
            rule_violation = rule_check(answer, cards, arm, q["question"])
            scores = judge_answer(cfg, evidence, q["question"], answer)
            judged.append(
                {
                    "question": q["question"],
                    "arm": arm,
                    "answer": answer,
                    "rule_violation": rule_violation,
                    "llm_scores": scores,
                }
            )
            save_partial(Path(args.output), judged, cfg)
            print(f"[{i + 1}/{len(questions)}] {arm} done", flush=True)
            time.sleep(0.2)

    summary = summarize(judged)
    save_partial(Path(args.output), judged, cfg, summary=summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_partial(path: Path) -> list[dict[str, Any]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("judged", [])
        except json.JSONDecodeError:
            return []
    return []


def save_partial(path: Path, judged: list[dict[str, Any]], cfg: dict[str, str], summary: dict | None = None) -> None:
    payload: dict[str, Any] = {
        "meta": {
            "generator_model": cfg["model"],
            "judge_model": cfg["judge_model"],
            "answers": len(judged),
            "note": "rag 臂商品卡按类目取前 5（未走向量检索）；规则检测为 GroundingGuard 核心规则轻量对齐版",
        },
        "summary": summary,
        "judged": judged,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def chat(cfg: dict[str, str], prompt: str, *, model: str | None = None, json_mode: bool = False) -> str:
    payload: dict[str, Any] = {
        "model": model or cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    with httpx.Client(timeout=httpx.Timeout(90.0)) as client:
        response = client.post(f"{cfg['base_url']}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def generate_answer(cfg: dict[str, str], question: str, cards: list[dict], arm: str) -> str:
    if arm == "rag":
        card_text = "\n".join(
            f"- {p['name']}，{p['price']}元，品牌{p['brand']}" for p in cards
        )
        return chat(cfg, RAG_PROMPT.format(cards=card_text, question=question))
    return chat(cfg, PURE_LLM_PROMPT.format(question=question))


NEGATION_PREFIXES = ("没有", "无", "无法", "不能", "不可", "不得", "未", "别", "暂不")
BRAND_ALIASES = {"Apple": ["苹果"], "Nike": ["耐克"], "Adidas": ["阿迪达斯"]}
BUDGET_RE = re.compile(r"预算\s*(\d{3,6}(?:\.\d{1,2})?)\s*元?")


def rule_check(answer: str, cards: list[dict], arm: str, question: str = "") -> bool:
    """GroundingGuard 核心规则的轻量对齐版：价格比对 + 商品存在性 + 违禁词（否定语境豁免）。"""
    for word in FORBIDDEN_PATTERNS:
        start = 0
        while True:
            idx = answer.find(word, start)
            if idx < 0:
                break
            prefix = answer[max(0, idx - 10):idx]
            if not any(neg in prefix for neg in NEGATION_PREFIXES):
                return True
            start = idx + len(word)
    if arm == "rag":
        card_prices = {round(float(p["price"]), 2) for p in cards}
        # 用户预算属于合法语境（真实 Guard 同样豁免）
        for budget in BUDGET_RE.findall(question):
            card_prices.add(round(float(budget), 2))
        claimed: set[float] = set()
        for match in PRICE_RE.finditer(answer):
            # 排除差价语境："低1000元"、"贵2000元" 等比较表述不是商品报价
            prefix = answer[max(0, match.start() - 2):match.start()]
            if any(w in prefix for w in ("低", "贵", "便宜", "差", "省", "降", "剩")):
                continue
            claimed.add(round(float(match.group(1)), 2))
        for price in claimed:
            if not any(abs(price - cp) <= 1.0 for cp in card_prices):
                return True
        if not any(product_mentioned(p, answer) for p in cards):
            return True
    return False


def product_mentioned(product: dict, answer: str) -> bool:
    brand = str(product.get("brand", ""))
    if brand and brand != "Unknown" and brand in answer:
        return True
    for alias in BRAND_ALIASES.get(brand, []):
        if alias in answer:
            return True
    name = str(product.get("name", ""))
    return bool(name) and (name[:6] in answer or name[-6:] in answer)


def judge_answer(cfg: dict[str, str], evidence: str, question: str, answer: str) -> dict[str, Any]:
    content = chat(
        cfg,
        JUDGE_PROMPT.format(evidence=evidence, question=question, answer=answer[:2000]),
        model=cfg["judge_model"],
        json_mode=True,
    )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        parsed = json.loads(content[start : end + 1])
    return {
        "faithfulness": float(parsed.get("faithfulness", 0)),
        "relevance": float(parsed.get("relevance", 0)),
        "fluency": float(parsed.get("fluency", 0)),
        "has_violation": bool(parsed.get("has_violation")),
        "violation_reason": str(parsed.get("violation_reason", ""))[:200],
    }


def summarize(judged: list[dict[str, Any]]) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for item in judged:
        by_arm.setdefault(item["arm"], []).append(item)

    arm_summary = {}
    for arm, items in by_arm.items():
        n = len(items)
        arm_summary[arm] = {
            "count": n,
            "avg_faithfulness": round(sum(i["llm_scores"]["faithfulness"] for i in items) / n, 3),
            "avg_relevance": round(sum(i["llm_scores"]["relevance"] for i in items) / n, 3),
            "avg_fluency": round(sum(i["llm_scores"]["fluency"] for i in items) / n, 3),
            "llm_violation_rate": round(sum(1 for i in items if i["llm_scores"]["has_violation"]) / n, 4),
            "rule_violation_rate": round(sum(1 for i in items if i["rule_violation"]) / n, 4),
        }

    pairs = [(i["llm_scores"]["has_violation"], i["rule_violation"]) for i in judged]
    n = len(pairs)
    agree = sum(1 for llm, rule in pairs if llm == rule) / n
    p_llm = sum(1 for llm, _ in pairs if llm) / n
    p_rule = sum(1 for _, rule in pairs if rule) / n
    p_expected = p_llm * p_rule + (1 - p_llm) * (1 - p_rule)
    kappa = (agree - p_expected) / (1 - p_expected) if p_expected < 1 else 1.0

    return {
        "by_arm": arm_summary,
        "agreement": {
            "observed_agreement": round(agree, 4),
            "cohens_kappa": round(kappa, 4),
            "interpretation": kappa_interpretation(kappa),
        },
    }


def kappa_interpretation(kappa: float) -> str:
    if kappa >= 0.8:
        return "almost perfect（几乎完全一致）"
    if kappa >= 0.6:
        return "substantial（高度一致）"
    if kappa >= 0.4:
        return "moderate（中等一致）"
    return "fair/poor（一致性弱）"


if __name__ == "__main__":
    raise SystemExit(main())
