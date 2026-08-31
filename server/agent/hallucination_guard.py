"""
幻觉检测守卫 —— 确保 LLM 输出与真实商品数据一致。

三层检测架构：
  Layer 1: 商品存在性校验（ID 级）
  Layer 2: 属性事实校验（字段级）
  Layer 3: SQL 真实数据校验（数据库级）

处理策略：
  - 可修正幻觉（价格/库存数字错误）→ 自动替换
  - 严重幻觉（编造商品、属性错配）→ 纠错 Prompt → LLM 重生成（最多 2 次）
  - 重生成失败 → 降级到确定性模板

依赖：
  - Layer 1+2: 纯本地，仅需 cards
  - Layer 3: 需要 CommerceGateway / mysql_bridge
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────
# 数据模型
# ───────────────────────────────────────────────

@dataclass(frozen=True)
class HallucinationViolation:
    """单条违规记录。"""

    layer: int                          # 1/2/3
    type: str                           # 违规类型，如 "price_mismatch"
    product: str = ""                   # 涉及商品名称
    field: str = ""                     # 涉及字段
    claimed: Any = None                 # LLM 声称的值
    actual: Any = None                  # 真实值
    severity: str = "warn"              # warn / block


@dataclass(frozen=True)
class HallucinationReport:
    """幻觉检测报告。"""

    safe: bool = True
    violations: list[HallucinationViolation] = field(default_factory=list)
    action: str = "pass"                # pass / auto_correct / regenerate / fallback
    corrected_answer: str | None = None
    regenerate_prompt: str | None = None
    needs_human_review: bool = False


# ───────────────────────────────────────────────
# Layer 1: 商品存在性校验
# ───────────────────────────────────────────────

def _normalize(text: str) -> str:
    """标准化文本用于匹配。"""
    return re.sub(r"\s+", "", str(text)).lower()


def _extract_mentioned_products(answer: str, cards: list[dict]) -> list[dict]:
    """
    从回答中提取提到的商品及其匹配状态。
    返回每个候选商品的匹配信息。
    """
    normalized_answer = _normalize(answer)
    mentioned = []

    for card in cards:
        name = str(card.get("name", "")).strip()
        brand = str(card.get("brand", "")).strip()
        matched = False
        match_reason = ""

        # 商品全名匹配
        if name and _normalize(name) in normalized_answer:
            matched = True
            match_reason = "name_exact"
        # 品牌匹配（至少 2 个字符，避免单字误匹配）
        elif brand and len(brand) >= 2 and _normalize(brand) in normalized_answer:
            matched = True
            match_reason = "brand_match"
        # 商品名分段匹配（取前 10 个字符）
        elif name and len(name) >= 4:
            short_name = _normalize(name[:10])
            if short_name in normalized_answer:
                matched = True
                match_reason = "name_partial"

        mentioned.append({
            "card": card,
            "matched": matched,
            "match_reason": match_reason,
        })

    return mentioned


def check_product_existence(answer: str, cards: list[dict]) -> list[HallucinationViolation]:
    """
    Layer 1: 检查回答中提到的商品是否存在于候选列表中。
    通过提取回答中的商品名/品牌名与 cards 匹配。
    """
    violations = []
    if not answer.strip() or not cards:
        return violations

    mentioned = _extract_mentioned_products(answer, cards)
    matched_count = sum(1 for m in mentioned if m["matched"])

    # 如果完全没匹配到任何候选商品，且回答不是拒绝推荐
    if matched_count == 0:
        # 检查是否是拒绝推荐的回答
        decline_terms = ("没有", "未找到", "暂无", "无法", "不能", "不支持")
        is_decline = any(term in answer for term in decline_terms)
        if not is_decline:
            violations.append(HallucinationViolation(
                layer=1,
                type="no_product_match",
                product="",
                severity="block",
            ))

    return violations


# ───────────────────────────────────────────────
# Layer 2: 属性事实校验
# ───────────────────────────────────────────────

# 价格正则：匹配 ¥100、100元、价格：100 等
PRICE_PATTERN = re.compile(
    r"(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:元|块)|"
    r"(?:价格|售价|到手价|约|大概)\s*[：:]\s*(\d+(?:\.\d+)?))"
)

# 库存正则
STOCK_PATTERN = re.compile(
    r"(?:库存|现货|剩余|还有|仅剩)\s*[：:]?\s*(\d+)\s*(?:件|个|台|支)?|"
    r"(?:库存|现货|充足|紧张|售罄|卖完|断货)"
)

# 序号引用模式：【候选1】、候选1、第1款 等
INDEX_PATTERN = re.compile(r"[【\[]?候选\s*(\d+)[】\]]?|第\s*(\d+)\s*[款个件]|(\d+)[号款]")


def _extract_answer_prices(answer: str) -> list[tuple[str, float]]:
    """从回答中提取所有价格声明。
    返回 [(上下文片段, 价格值), ...]
    """
    results = []
    for match in PRICE_PATTERN.finditer(answer):
        # 取三个捕获组中不为空的那个
        raw = match.group(1) or match.group(2) or match.group(3) or ""
        try:
            price = float(raw)
            # 取前后 15 个字符作为上下文
            start = max(0, match.start() - 15)
            end = min(len(answer), match.end() + 15)
            context = answer[start:end]
            results.append((context, price))
        except (ValueError, TypeError):
            continue
    return results


def _extract_answer_stock(answer: str) -> list[tuple[str, int | str]]:
    """从回答中提取所有库存声明。
    返回 [(上下文片段, 库存值或状态), ...]
    """
    results = []
    for match in STOCK_PATTERN.finditer(answer):
        raw = match.group(1)
        start = max(0, match.start() - 15)
        end = min(len(answer), match.end() + 15)
        context = answer[start:end]
        if raw:
            try:
                results.append((context, int(raw)))
            except ValueError:
                pass
        else:
            # 匹配的是状态词
            matched_text = match.group(0)
            results.append((context, matched_text))
    return results


def _find_product_for_context(context: str, cards: list[dict]) -> dict | None:
    """根据上下文片段，判断涉及哪个商品。"""
    normalized = _normalize(context)
    best_match = None
    best_score = 0

    for card in cards:
        name = str(card.get("name", "")).strip()
        brand = str(card.get("brand", "")).strip()
        score = 0

        if name and _normalize(name[:15]) in normalized:
            score += 3
        if brand and len(brand) >= 2 and _normalize(brand) in normalized:
            score += 2
        # 序号引用
        card_idx = cards.index(card) + 1
        if f"候选{card_idx}" in context or f"第{card_idx}" in context:
            score += 5

        if score > best_score:
            best_score = score
            best_match = card

    return best_match


def check_attribute_facts(answer: str, cards: list[dict]) -> list[HallucinationViolation]:
    """
    Layer 2: 检查回答中的属性声明是否与候选卡片一致。
    精确匹配，零阈值。
    """
    violations = []
    if not answer.strip() or not cards:
        return violations

    # ── 价格校验 ──
    claimed_prices = _extract_answer_prices(answer)
    for context, claimed_price in claimed_prices:
        card = _find_product_for_context(context, cards)
        if card is None:
            # 无法确定涉及哪个商品，保守起见跳过
            continue

        actual_price = card.get("price")
        if actual_price is not None:
            # 精确匹配，零阈值
            if abs(float(actual_price) - claimed_price) > 0.01:
                violations.append(HallucinationViolation(
                    layer=2,
                    type="price_mismatch",
                    product=str(card.get("name", "")),
                    field="price",
                    claimed=claimed_price,
                    actual=actual_price,
                    severity="block",
                ))

    # ── 库存校验 ──
    claimed_stocks = _extract_answer_stock(answer)
    for context, claimed_stock in claimed_stocks:
        card = _find_product_for_context(context, cards)
        if card is None:
            continue

        actual_stock = card.get("stock")
        if actual_stock is not None:
            if isinstance(claimed_stock, int):
                # 精确匹配具体数字
                if int(actual_stock) != claimed_stock:
                    violations.append(HallucinationViolation(
                        layer=2,
                        type="stock_mismatch",
                        product=str(card.get("name", "")),
                        field="stock",
                        claimed=claimed_stock,
                        actual=actual_stock,
                        severity="block",
                    ))
            else:
                # 状态词校验
                actual = int(actual_stock)
                status_word = str(claimed_stock)
                # "库存充足"但 actual <= 5 → 幻觉
                if ("充足" in status_word or "很多" in status_word) and actual <= 5:
                    violations.append(HallucinationViolation(
                        layer=2,
                        type="stock_status_mismatch",
                        product=str(card.get("name", "")),
                        field="stock",
                        claimed=status_word,
                        actual=f"{actual}件",
                        severity="warn",
                    ))
                # "售罄"但 actual > 0 → 幻觉
                if ("售罄" in status_word or "断货" in status_word or "卖完" in status_word) and actual > 0:
                    violations.append(HallucinationViolation(
                        layer=2,
                        type="stock_status_mismatch",
                        product=str(card.get("name", "")),
                        field="stock",
                        claimed=status_word,
                        actual=f"{actual}件",
                        severity="block",
                    ))

    return violations


# ───────────────────────────────────────────────
# Layer 3: SQL 真实数据校验
# ───────────────────────────────────────────────

async def check_sql_grounding(
    answer: str,
    cards: list[dict],
    commerce_gateway=None,
) -> list[HallucinationViolation]:
    """
    Layer 3: 通过 CommerceGateway / MySQL 查询真实数据并比对。
    精确匹配，零阈值。

    如果 commerce_gateway 为 None，则跳过此层。
    """
    violations = []
    if not answer.strip() or not cards or commerce_gateway is None:
        return violations

    for card in cards:
        product_id = card.get("id", "")
        if not product_id:
            continue

        # 尝试从 commerce_gateway 获取真实数据
        try:
            real_price = await commerce_gateway.get_price(product_id)
            real_stock = await commerce_gateway.get_inventory(product_id)
        except Exception as exc:
            logger.warning("CommerceGateway query failed for %s: %s", product_id, exc)
            continue

        card_price = card.get("price")
        card_stock = card.get("stock")

        # 比对价格：cards vs SQL
        if real_price is not None and card_price is not None:
            if abs(float(real_price) - float(card_price)) > 0.01:
                # cards 里的价格和数据库不一致！这是数据同步问题
                logger.error(
                    "Data sync issue: card price %s != SQL price %s for %s",
                    card_price, real_price, product_id,
                )
                # 以 SQL 为准，但这不是 LLM 的幻觉，是数据问题
                # 仍记录，但标记为 data_sync_issue
                violations.append(HallucinationViolation(
                    layer=3,
                    type="data_sync_issue_price",
                    product=str(card.get("name", "")),
                    field="price",
                    claimed=card_price,
                    actual=real_price,
                    severity="warn",
                ))

        # 比对回答中的价格声明 vs SQL 真实价格
        claimed_prices = _extract_answer_prices(answer)
        for context, claimed_price in claimed_prices:
            matched_card = _find_product_for_context(context, cards)
            if matched_card and matched_card.get("id") == product_id:
                sql_price = real_price if real_price is not None else card_price
                if sql_price is not None and abs(float(sql_price) - claimed_price) > 0.01:
                    violations.append(HallucinationViolation(
                        layer=3,
                        type="sql_price_mismatch",
                        product=str(card.get("name", "")),
                        field="price",
                        claimed=claimed_price,
                        actual=sql_price,
                        severity="block",
                    ))

    return violations


# ───────────────────────────────────────────────
# 自动修正
# ───────────────────────────────────────────────

def auto_correct_answer(answer: str, violations: list[HallucinationViolation]) -> str:
    """
    对可修正的幻觉进行自动替换。
    只修正数值型字段（价格、库存数字）。
    """
    corrected = answer
    replaced = []

    for v in violations:
        if v.type in ("price_mismatch", "sql_price_mismatch") and v.claimed is not None and v.actual is not None:
            # 处理价格：claimed 可能是 float(4999.0) 但文本中是 "4999"
            claimed_float = float(v.claimed)
            actual_float = float(v.actual)
            # 生成可能的字符串形式（带 .0 和不带 .0）
            claimed_strs = {str(claimed_float), str(int(claimed_float)) if claimed_float == int(claimed_float) else str(claimed_float)}
            actual_str = str(int(actual_float)) if actual_float == int(actual_float) else str(actual_float)

            # 尝试替换多种格式（处理中英文空格）
            found = False
            for claimed_str in claimed_strs:
                # 基础模式（无空格）
                base_patterns = [
                    f"{claimed_str}元",
                    f"¥{claimed_str}",
                    f"￥{claimed_str}",
                    f"价格：{claimed_str}",
                    f"售价：{claimed_str}",
                    f"到手价：{claimed_str}",
                    f"约{claimed_str}元",
                ]
                # 处理空格的变体（中文数字后常带空格）
                space_patterns = [
                    f"{claimed_str} 元",
                    f"¥{claimed_str} ",
                    f"￥{claimed_str} ",
                    f"价格：{claimed_str} ",
                    f"售价：{claimed_str} ",
                    f"到手价：{claimed_str} ",
                    f"约{claimed_str} 元",
                ]
                all_patterns = base_patterns + space_patterns
                for pat in all_patterns:
                    if pat in corrected:
                        corrected = corrected.replace(pat, f"{actual_str}元", 1)
                        replaced.append(f"{v.product}: price {claimed_str}→{actual_str}")
                        found = True
                        break
                if found:
                    break

        elif v.type == "stock_mismatch" and v.claimed is not None and v.actual is not None:
            claimed_str = str(int(v.claimed)) if float(v.claimed) == int(v.claimed) else str(v.claimed)
            actual_str = str(int(v.actual)) if float(v.actual) == int(v.actual) else str(v.actual)
            base_patterns = [
                f"库存{claimed_str}件",
                f"库存：{claimed_str}件",
                f"仅剩{claimed_str}件",
                f"还有{claimed_str}件",
                f"剩余{claimed_str}件",
            ]
            space_patterns = [
                f"库存 {claimed_str} 件",
                f"库存： {claimed_str} 件",
                f"仅剩 {claimed_str} 件",
                f"还有 {claimed_str} 件",
                f"剩余 {claimed_str} 件",
            ]
            for pat in base_patterns + space_patterns:
                if pat in corrected:
                    corrected = corrected.replace(pat, f"库存{actual_str}件", 1)
                    replaced.append(f"{v.product}: stock {claimed_str}→{actual_str}")
                    break

    if replaced:
        logger.info("Auto-corrected hallucinations: %s", replaced)

    return corrected


# ───────────────────────────────────────────────
# 纠错 Prompt 构建
# ───────────────────────────────────────────────

def build_regenerate_prompt(
    user_message: str,
    cards: list[dict],
    original_answer: str,
    violations: list[HallucinationViolation],
) -> str:
    """
    构建 LLM 纠错的 prompt。
    商品信息直接从 cards 构建，和第一次生成时完全一致。
    """
    # 商品上下文（和第一次生成时一样）
    product_lines = []
    for i, card in enumerate(cards, 1):
        name = card.get("name", "未知商品")
        brand = card.get("brand", "")
        price = card.get("price", "")
        stock = card.get("stock", "")
        line = f"【候选{i}】{brand} {name}"
        if price:
            line += f" — 售价：{price}元"
        if stock is not None:
            line += f" — 库存：{stock}件"
        product_lines.append(line)

    product_context = "\n".join(product_lines)

    # 错误说明
    error_lines = []
    for v in violations:
        if v.type == "price_mismatch":
            error_lines.append(
                f"- 你说「{v.product}」售价{v.claimed}元，实际售价是{v.actual}元"
            )
        elif v.type == "stock_mismatch":
            error_lines.append(
                f"- 你说「{v.product}」库存{v.claimed}件，实际库存是{v.actual}件"
            )
        elif v.type == "stock_status_mismatch":
            error_lines.append(
                f"- 你说「{v.product}」库存状态是「{v.claimed}」，实际库存是{v.actual}"
            )
        elif v.type == "no_product_match":
            error_lines.append(
                "- 你的回答中没有明确引用候选商品，请确保提到具体的商品名称"
            )
        elif v.type in ("sql_price_mismatch", "data_sync_issue_price"):
            error_lines.append(
                f"- 「{v.product}」价格数据校验失败：声称{v.claimed}元，"
                f"数据库记录为{v.actual}元"
            )

    error_detail = "\n".join(error_lines) if error_lines else "回答中存在事实错误。"

    prompt = f"""你之前的回答存在事实错误，请根据以下真实数据重新生成。

用户需求：
{user_message}

候选商品（真实数据，以此为唯一依据）：
{product_context}

你之前的错误回答：
{original_answer}

检测到的具体错误：
{error_detail}

要求：
1. 严格基于候选商品的真实数据回答，价格、库存必须与候选商品完全一致
2. 不要编造任何候选商品中不存在的信息
3. 用【候选N】格式引用商品，如"推荐【候选1】"或"【候选1】和【候选2】都不错"
4. 不要编造优惠、库存状态（如"充足""紧张"等主观描述）
5. 用自然导购口吻回答，不要截断句子
"""

    return prompt


# ───────────────────────────────────────────────
# 确定性降级模板
# ───────────────────────────────────────────────

def build_fallback_template(cards: list[dict]) -> str:
    """
    确定性降级模板 —— 完全不经过 LLM，直接拼接真实数据。
    适用于：LLM 重生成失败后的保底，或用户问题极简单时。
    """
    if not cards:
        return "抱歉，暂未找到匹配的商品。您可以尝试调整需求，比如放宽预算或品牌限制。"

    lines = ["为您找到以下商品："]
    for i, card in enumerate(cards[:5], 1):
        name = str(card.get("name", "未知商品")).strip()
        brand = str(card.get("brand", "")).strip()
        price = card.get("price")
        stock = card.get("stock")

        parts = [f"{i}. "]
        if brand:
            parts.append(f"{brand} ")
        parts.append(f"{name}")
        if price is not None:
            parts.append(f" — {price}元")
        if stock is not None and stock <= 5:
            parts.append(f"（库存紧张：仅剩{stock}件）")
        elif stock is not None and stock == 0:
            parts.append("（暂时缺货）")

        lines.append("".join(parts))

    if len(cards) > 5:
        lines.append(f"\n...还有 {len(cards) - 5} 款相似商品")

    return "\n".join(lines)


# ───────────────────────────────────────────────
# 主入口：幻觉检测守卫
# ───────────────────────────────────────────────

async def guard_hallucination(
    *,
    answer: str,
    cards: list[dict],
    user_message: str = "",
    commerce_gateway=None,
    enable_sql_check: bool = False,
    strict_mode: bool = False,
    auto_correct: bool = True,
    max_regenerate: int = 2,
    regenerate_count: int = 0,
) -> HallucinationReport:
    """
    幻觉检测主入口。

    流程：
      1. 运行三层检测（Layer 1→2→3）
      2. 分类处理：
         - 无违规 → pass
         - 可修正（数值错误）→ auto_correct
         - 严重（编造商品、属性错配）→ regenerate（如未超限）
         - 重生成超限 → fallback
      3. 返回报告 + 处理后的回答

    Args:
        answer: LLM 生成的回答
        cards: 检索到的候选商品卡片
        user_message: 用户原始提问（用于构建纠错 prompt）
        commerce_gateway: CommerceGateway 实例（Layer 3 用）
        enable_sql_check: 是否启用 Layer 3 SQL 校验
        strict_mode: 是否严格拦截（True 时不可修正的直接 fallback）
        auto_correct: 是否自动修正可修正的幻觉
        max_regenerate: 最大重生成次数
        regenerate_count: 当前已重生成次数（递归调用时递增）
    """
    # ── 三层检测 ──
    all_violations: list[HallucinationViolation] = []

    # Layer 1: 商品存在性
    all_violations.extend(check_product_existence(answer, cards))

    # Layer 2: 属性事实
    all_violations.extend(check_attribute_facts(answer, cards))

    # Layer 3: SQL 校验（可选）
    if enable_sql_check and commerce_gateway is not None:
        try:
            sql_violations = await check_sql_grounding(answer, cards, commerce_gateway)
            all_violations.extend(sql_violations)
        except Exception as exc:
            logger.warning("SQL grounding check failed: %s", exc)

    # ── 无违规，直接通过 ──
    if not all_violations:
        return HallucinationReport(safe=True, action="pass")

    # ── 分类违规 ──
    correctable = []      # 可自动修正
    needs_regenerate = []  # 需要 LLM 重生成
    for v in all_violations:
        if v.type in ("price_mismatch", "sql_price_mismatch", "stock_mismatch"):
            correctable.append(v)
        else:
            needs_regenerate.append(v)

    # ── 尝试自动修正 ──
    corrected_answer = None
    if auto_correct and correctable and not needs_regenerate:
        corrected = auto_correct_answer(answer, correctable)
        if corrected != answer:
            corrected_answer = corrected
            logger.info("Auto-corrected answer for %d violations", len(correctable))
            # 修正后返回，但标记为已修正
            return HallucinationReport(
                safe=True,
                violations=correctable,
                action="auto_correct",
                corrected_answer=corrected_answer,
            )

    # ── 需要重生成 ──
    has_block = any(v.severity == "block" for v in all_violations)

    if has_block:
        if strict_mode:
            # 严格模式：直接 fallback，不重试
            return HallucinationReport(
                safe=False,
                violations=all_violations,
                action="fallback",
                corrected_answer=build_fallback_template(cards),
                needs_human_review=True,
            )

        if regenerate_count < max_regenerate:
            # 构建纠错 prompt，让上层重调用 LLM
            regenerate_prompt = build_regenerate_prompt(
                user_message=user_message,
                cards=cards,
                original_answer=answer,
                violations=all_violations,
            )
            return HallucinationReport(
                safe=False,
                violations=all_violations,
                action="regenerate",
                regenerate_prompt=regenerate_prompt,
            )
        else:
            # 重生成次数超限
            logger.warning(
                "Hallucination guard: regenerate limit exceeded (%d/%d)",
                regenerate_count, max_regenerate,
            )
            return HallucinationReport(
                safe=False,
                violations=all_violations,
                action="fallback",
                corrected_answer=build_fallback_template(cards),
                needs_human_review=True,
            )

    # ── 只有 warn 级别违规，允许通过但记录 ──
    return HallucinationReport(
        safe=True,
        violations=all_violations,
        action="pass",
    )


# ───────────────────────────────────────────────
# 日志记录
# ───────────────────────────────────────────────

def log_hallucination_event(
    report: HallucinationReport,
    session_id: str = "",
    trace_id: str = "",
    log_path: str = "server/runtime/hallucination.jsonl",
) -> None:
    """将幻觉事件写入 JSONL 日志。"""
    event = {
        "timestamp": time.time(),
        "session_id": session_id,
        "trace_id": trace_id,
        "action": report.action,
        "safe": report.safe,
        "needs_human_review": report.needs_human_review,
        "violations": [
            {
                "layer": v.layer,
                "type": v.type,
                "product": v.product,
                "field": v.field,
                "claimed": v.claimed,
                "actual": v.actual,
                "severity": v.severity,
            }
            for v in report.violations
        ],
    }

    try:
        import os
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to write hallucination log: %s", exc)


import time
