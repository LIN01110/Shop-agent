"""
Purpose: LLM 结构化 JSON 输出稳定性评测（替代原方案的 VLM 解析评测——DeepSeek API 无视觉模型，
         故改为评测文本 LLM 的结构化输出能力，对应简历中"结构化 JSON 输出"的工程价值）。

任务：给定商品名称+描述，要求模型抽取结构化字段 JSON。
两种策略对比：
  naive  — 普通提示词（只说"输出 JSON"）
  strict — Schema 约束提示词 + response_format=json_object

指标：JSON 解析成功率、必需字段完整率、字段类型合规率、非空率。
"""

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from benchmark_common import deepseek_chat, load_products, save_json

REQUIRED_FIELDS = ["color", "style_tags", "target_users", "key_features"]
LIST_FIELDS = ["style_tags", "target_users", "key_features"]

NAIVE_PROMPT = """请从以下商品信息中抽取关键属性，以 JSON 格式输出。
需要包含字段：color（颜色）、style_tags（风格标签列表）、target_users（适用人群列表）、key_features（核心卖点列表）。

商品名称：{name}
商品描述：{description}"""

STRICT_PROMPT = """你是信息抽取引擎。从给定商品信息中抽取属性，严格按以下 JSON Schema 输出，不要输出任何额外文字。

Schema：
{{
  "color": "字符串，商品主色调，无法判断时填空字符串",
  "style_tags": "字符串数组，1-3 个风格标签，如 简约/运动/商务",
  "target_users": "字符串数组，1-3 类适用人群",
  "key_features": "字符串数组，1-4 个核心卖点"
}}

商品名称：{name}
商品描述：{description}"""


def extract_json(text: str) -> dict | None:
    """尽力解析 JSON：先直接解析，失败则截取首个 { 到末个 } 再试。"""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def validate(parsed: dict) -> dict:
    missing = [field for field in REQUIRED_FIELDS if field not in parsed]
    type_errors = [
        field for field in LIST_FIELDS
        if field in parsed and not isinstance(parsed[field], list)
    ]
    empty_fields = [
        field for field in REQUIRED_FIELDS
        if field in parsed and (parsed[field] == "" or parsed[field] == [])
    ]
    return {
        "complete": not missing,
        "missing": missing,
        "type_valid": not type_errors,
        "type_errors": type_errors,
        "non_empty": len(empty_fields) == 0 and not missing,
    }


def run_strategy(name: str, prompt_template: str, json_mode: bool, products: list[dict]) -> dict:
    records: list[dict] = []
    for product in products:
        prompt = prompt_template.format(
            name=product["name"],
            description=str(product.get("description", ""))[:300],
        )
        try:
            result = deepseek_chat(
                [{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.1,
                json_mode=json_mode,
            )
            raw = result["content"]
            parsed = extract_json(raw)
            error = ""
        except Exception as exc:  # noqa: BLE001
            raw, parsed, error = "", None, str(exc)[:200]

        if parsed is None:
            records.append({"product_id": product["id"], "parse_ok": False, "error": error, "raw": raw[:200]})
        else:
            check = validate(parsed)
            records.append({
                "product_id": product["id"],
                "parse_ok": True,
                "complete": check["complete"],
                "type_valid": check["type_valid"],
                "non_empty": check["non_empty"],
                "parsed": parsed,
            })

    total = len(records)
    parsed_ok = [r for r in records if r["parse_ok"]]
    return {
        "strategy": name,
        "total": total,
        "parse_success_rate": round(len(parsed_ok) / total, 4),
        "field_complete_rate": round(sum(1 for r in parsed_ok if r.get("complete")) / total, 4),
        "type_valid_rate": round(sum(1 for r in parsed_ok if r.get("type_valid")) / total, 4),
        "non_empty_rate": round(sum(1 for r in parsed_ok if r.get("non_empty")) / total, 4),
        "records": records,
    }


def main() -> None:
    products = load_products()
    # 每个大类取 10 个，共 40 个样本
    sample: list[dict] = []
    for category in sorted({p["category"] for p in products}):
        sample.extend([p for p in products if p["category"] == category][:10])
    print(f"评测样本数: {len(sample)}", flush=True)

    which = sys.argv[1] if len(sys.argv) > 1 else "both"

    naive = strict = None
    if which in ("naive", "both"):
        naive = run_strategy("naive_prompt", NAIVE_PROMPT, False, sample)
        print(f"naive : 解析成功率={naive['parse_success_rate']:.1%} 字段完整率={naive['field_complete_rate']:.1%} 类型合规率={naive['type_valid_rate']:.1%}", flush=True)
        save_json({"strategy": naive["strategy"], "metrics": {k: v for k, v in naive.items() if k != "records"}, "records": naive["records"]}, "structured_naive.json")

    if which in ("strict", "both"):
        strict = run_strategy("schema_constrained", STRICT_PROMPT, True, sample)
        print(f"strict: 解析成功率={strict['parse_success_rate']:.1%} 字段完整率={strict['field_complete_rate']:.1%} 类型合规率={strict['type_valid_rate']:.1%}", flush=True)
        save_json({"strategy": strict["strategy"], "metrics": {k: v for k, v in strict.items() if k != "records"}, "records": strict["records"]}, "structured_strict.json")

    # 合并结果（若分段运行，从分片文件合并；分片不齐时跳过合并）
    naive_shard = ROOT_DIR / "results" / "structured_naive.json"
    strict_shard = ROOT_DIR / "results" / "structured_strict.json"
    if naive is None:
        if not naive_shard.exists():
            print("naive 分片不存在，跳过合并")
            return
        naive_data = json.loads(naive_shard.read_text(encoding="utf-8"))
        naive = {"records": naive_data["records"], **naive_data["metrics"]}
    if strict is None:
        if not strict_shard.exists():
            print("strict 分片不存在，跳过合并")
            return
        strict_data = json.loads(strict_shard.read_text(encoding="utf-8"))
        strict = {"records": strict_data["records"], **strict_data["metrics"]}

    out = save_json(
        {
            "meta": {
                "task": "商品信息结构化抽取（文本 LLM，替代 VLM 图片解析评测）",
                "sample_size": len(sample),
                "note": "DeepSeek API 无视觉模型，VLM 图片结构化解析评测不适用；本评测对应简历中结构化 JSON 输出的工程能力",
            },
            "strategies": {
                "naive_prompt": {k: v for k, v in naive.items() if k != "records"},
                "schema_constrained": {k: v for k, v in strict.items() if k != "records"},
            },
            "records": {"naive_prompt": naive["records"], "schema_constrained": strict["records"]},
        },
        "structured_output.json",
    )
    print(f"结果已保存: {out}")


if __name__ == "__main__":
    main()
