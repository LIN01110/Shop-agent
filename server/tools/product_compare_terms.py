"""
Purpose: 对比维度提取：从用户查询中提取对比维度（适合干皮/性价比）。
"""

import re


def extract_comparison_terms(query: str) -> list[str]:
    pattern = r"(.+?)\s*(?:和|与|跟|vs|VS|v\.s\.)\s*(.+)"
    match = re.search(pattern, query)
    if not match:
        return []

    left = clean_comparison_term(match.group(1))
    right = clean_comparison_term(cut_comparison_tail(match.group(2)))
    return [term for term in [left, right] if term]


def cut_comparison_tail(value: str) -> str:
    tail_markers = [
        "哪个",
        "哪款",
        "谁",
        "更",
        "区别",
        "差异",
        "对比",
        "比较",
        "这两款",
        "这两个",
        "哪个更",
        "哪款更",
    ]
    indexes = [value.find(marker) for marker in tail_markers if value.find(marker) > 0]
    if not indexes:
        return value
    return value[: min(indexes)]


def clean_comparison_term(value: str) -> str:
    term = value.strip(" ，,。？?；;：:")
    term = re.sub(
        r"^(帮我|请|麻烦|想问|我想|推荐|对比一下|比较一下|分析一下|对比|比较|看看|一个)+",
        "",
        term,
    )
    return term.strip(" ，,。？?；;：:")
