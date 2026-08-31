"""
Purpose: 汇总 Benchmark 全部结果 → results/benchmark_summary.md
读取：retrieval_quality.json / latency.json / structured_output.json / hallucination.json / agent_eval.log
输出：Markdown 报告（含简历可用的中文表述草稿 + 数据口径说明）。
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

RESULTS_DIR = ROOT_DIR / "results"


def load_json(name: str) -> dict:
    path = RESULTS_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_agent_eval() -> dict:
    path = RESULTS_DIR / "agent_eval.log"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    passed = len(re.findall(r"\[PASS\]", text))
    failed = len(re.findall(r"\[FAIL\]", text))
    turns = re.search(r"Turns:\s*(\d+)/(\d+)\s*passed", text)
    return {
        "cases_passed": passed,
        "cases_failed": failed,
        "turns_passed": int(turns.group(1)) if turns else 0,
        "turns_total": int(turns.group(2)) if turns else 0,
    }


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def main() -> None:
    retrieval = load_json("retrieval_quality.json")
    latency = load_json("latency.json")
    structured = load_json("structured_output.json")
    hallucination = load_json("hallucination.json")
    agent_eval = parse_agent_eval()

    lines: list[str] = []
    lines.append("# Shop_Agent Benchmark 评测报告")
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("> 数据口径：商品库 100 SKU（4 大类）；检索 ground truth 为规则自动标注；")
    lines.append("> LLM 相关评测使用 DeepSeek API（deepseek-v4-flash）；幻觉判定为 GroundingGuard 规则集自动判定。")
    lines.append("")

    # 1. 检索质量
    if retrieval:
        configs = retrieval["configs"]
        raw = configs["raw_recall"]["overall"]
        nlu = configs["pipeline_nlu"]["overall"]
        lines.append("## 1. 检索质量（82 条规则标注查询）")
        lines.append("")
        lines.append("| 配置 | Recall@5 | Recall@10 | NDCG@10 | MRR |")
        lines.append("|---|---|---|---|---|")
        for name, label in [("raw_recall", "向量库裸召回"), ("pipeline_rerank", "管道+重排序"), ("pipeline_nlu", "管道+NLU过滤（生产路径）")]:
            o = configs[name]["overall"]
            lines.append(f"| {label} | {pct(o['recall@5'])} | {pct(o['recall@10'])} | {pct(o['ndcg@10'])} | {pct(o['mrr'])} |")
        lines.append("")
        lift_r5 = (nlu["recall@5"] - raw["recall@5"]) / raw["recall@5"] if raw["recall@5"] else 0
        lift_ndcg = (nlu["ndcg@10"] - raw["ndcg@10"]) / raw["ndcg@10"] if raw["ndcg@10"] else 0
        lines.append(f"生产路径相对裸召回：Recall@10 {pct(raw['recall@10'])}→{pct(nlu['recall@10'])}，NDCG@10 {pct(raw['ndcg@10'])}→{pct(nlu['ndcg@10'])}（相对提升 {lift_ndcg:.1%}），MRR {pct(raw['mrr'])}→{pct(nlu['mrr'])}。")
        lines.append("")

    # 2. 延迟
    if latency:
        r = latency.get("retrieval", {}).get("total_ms", {})
        h = latency.get("http_chat", {})
        d = latency.get("deepseek", {}).get("latency_ms", {})
        lines.append("## 2. 延迟")
        lines.append("")
        lines.append("| 环节 | P50 | P90 | P99 |")
        lines.append("|---|---|---|---|")
        if r:
            lines.append(f"| 检索管道（进程内，100 SKU） | {r.get('p50')}ms | {r.get('p90')}ms | {r.get('p99')}ms |")
        if h and "ttfb_ms" in h:
            lines.append(f"| /chat 首事件 TTFB（SSE） | {h['ttfb_ms'].get('p50')}ms | {h['ttfb_ms'].get('p90')}ms | {h['ttfb_ms'].get('p99')}ms |")
            lines.append(f"| /chat 完整流（模板回答路径） | {h['total_ms'].get('p50')}ms | {h['total_ms'].get('p90')}ms | {h['total_ms'].get('p99')}ms |")
        if d:
            lines.append(f"| DeepSeek 生成（非流式，~256 tokens） | {d.get('p50')}ms | {d.get('p90')}ms | — |")
        lines.append("")
        lines.append("注：/chat 完整流为确定性模板回答路径（当前 Ark LLM 密钥异常自动降级）；LLM 生成延迟单独以 DeepSeek 实测为准。")
        lines.append("")

    # 3. 幻觉
    if hallucination:
        s = hallucination["summary"]
        pure = s["pure_llm"]["violation_rate"]
        rag = s["rag"]["violation_rate"]
        guard_rate = s["rag_guard"]["guard_trigger_rate"]
        post = s["rag_guard"]["post_guard"]["violation_rate"]
        lines.append("## 3. 幻觉抑制（24 个导购问题，DeepSeek 三臂对比）")
        lines.append("")
        lines.append("| 实验组 | 违规率 |")
        lines.append("|---|---|")
        lines.append(f"| 纯 LLM（无商品上下文） | {pct(pure)} |")
        lines.append(f"| RAG（检索卡片注入） | {pct(rag)} |")
        lines.append(f"| RAG + GroundingGuard（最终输出） | {pct(post)} |")
        lines.append("")
        drop = (pure - post) / pure if pure else 0
        lines.append(f"GroundingGuard 在 RAG 回答上的触发率 {pct(guard_rate)}；最终输出违规率相对纯 LLM 降低 {drop:.1%}。")
        types = s["rag"].get("violation_type_counts", {})
        if types:
            lines.append(f"RAG 组主要违规类型：{', '.join(f'{k}({v})' for k, v in list(types.items())[:3])}。")
        lines.append("")

    # 4. 结构化输出
    if structured:
        st = structured["strategies"]
        naive = st["naive_prompt"]
        strict = st["schema_constrained"]
        lines.append("## 4. LLM 结构化 JSON 输出（40 个商品样本，DeepSeek）")
        lines.append("")
        lines.append("| 策略 | 解析成功率 | 字段完整率 | 类型合规率 |")
        lines.append("|---|---|---|---|")
        lines.append(f"| 普通提示词 | {pct(naive['parse_success_rate'])} | {pct(naive['field_complete_rate'])} | {pct(naive['type_valid_rate'])} |")
        lines.append(f"| Schema 约束 + JSON mode | {pct(strict['parse_success_rate'])} | {pct(strict['field_complete_rate'])} | {pct(strict['type_valid_rate'])} |")
        lines.append("")
        lines.append("注：DeepSeek API 无视觉模型，本项评测文本 LLM 的结构化输出稳定性，对应结构化 JSON 输出方案的工程价值。")
        lines.append("")

    # 5. Agent 路由
    if agent_eval:
        lines.append("## 5. Agent 路由评估（evaluate_agent.py，9 个多轮用例）")
        lines.append("")
        lines.append(f"- 用例通过：{agent_eval['cases_passed']}/{agent_eval['cases_passed'] + agent_eval['cases_failed']}")
        lines.append(f"- 轮次通过：{agent_eval['turns_passed']}/{agent_eval['turns_total']}（{pct(agent_eval['turns_passed'] / agent_eval['turns_total']) if agent_eval['turns_total'] else '—'}）")
        lines.append("")

    # 简历草稿
    lines.append("---")
    lines.append("")
    lines.append("## 简历可用表述（基于以上实测数据）")
    lines.append("")
    if retrieval:
        nlu = retrieval["configs"]["pipeline_nlu"]["overall"]
        lines.append(f"- 设计并实现「召回 → 7 级结构化后过滤 → 规则重排序」检索管道，在 82 条规则标注查询上 Recall@10 达 {pct(nlu['recall@10'])}、NDCG@10 达 {pct(nlu['ndcg@10'])}，相对向量裸召回 NDCG@10 提升 {((nlu['ndcg@10'] - raw['ndcg@10']) / raw['ndcg@10']):.1%}（100 SKU 商品库）")
    if latency and r:
        lines.append(f"- 检索管道单次查询 P50 延迟 {r.get('p50')}ms、P99 {r.get('p99')}ms；/chat SSE 首事件 TTFB P50 {h['ttfb_ms'].get('p50')}ms（100 SKU 规模）")
    if hallucination:
        lines.append(f"- 设计 GroundingGuard 规则后校验（优惠/价格/绝对化承诺/候选引用四类拦截），24 个导购问题三臂对比中，最终输出违规率从纯 LLM 的 {pct(pure)} 降至 {pct(post)}（DeepSeek 实测）")
    if structured:
        lines.append(f"- 通过 Schema 约束 Prompt + JSON mode 将 LLM 结构化输出解析成功率从 {pct(naive['parse_success_rate'])} 提升至 {pct(strict['parse_success_rate'])}，字段完整率 {pct(strict['field_complete_rate'])}（40 样本实测）")
    if agent_eval:
        lines.append(f"- Agent 多轮路由评估 {agent_eval['turns_passed']}/{agent_eval['turns_total']} 轮次通过（意图路由 + 上下文继承 + 反选过滤）")

    report = "\n".join(lines)
    out_path = RESULTS_DIR / "benchmark_summary.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"报告已生成: {out_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()
