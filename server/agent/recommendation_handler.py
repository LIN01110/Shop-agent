"""
答辩重点 🟠（推荐兜底 Handler）
任务：Day 2 任务 2.3 — 理解 RecommendationHandler 完整链路。
核心：registry.search_products() → RetrievalPipelineResult → LLM 生成自然语言回答（带系统提示词约束）
      → GroundingGuard 校验 → 若拦截则 yield guardrail 事件 + 降级模板回答
      → yield product_card 事件 → yield done 事件。
高频追问：
  - “为什么 LLM 回答要先缓冲再校验，而不是直接流式转发？”
    → 需要完整文本做事实校验，拦截后才能降级
  - “推荐链路失败后如何降级？”
    → guardrail 事件 + 模板回答 + 仍返回商品卡片
"""

from collections.abc import AsyncIterator
import asyncio
from contextlib import suppress
from dataclasses import asdict, dataclass
import logging
import re

from server.agent.grounding import guard_grounded_answer, guard_indexed_answer
from server.agent.hallucination_guard import (
    guard_hallucination,
    build_fallback_template,
    log_hallucination_event,
)
from server.llm.prompt import build_indexed_grounded_messages
from server.agent.product_discovery import build_product_discovery_policy, resolve_product_cards_for_answer
from server.agent.responses import (
    build_done_payload,
    build_grounded_answer,
    stream_text,
)
from server.agent.workflow import AgentTurnContext


MARKDOWN_EMPHASIS_PATTERN = re.compile(r"(\*\*|__)(.*?)\1")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductSearchExecution:
    cards: list[dict]
    diagnostics: object | None = None
    degraded: bool = False
    reason: str = ""


class RecommendationHandler:
    def matches(self, context: AgentTurnContext) -> bool:
        return True

    async def handle(self, context: AgentTurnContext) -> AsyncIterator[dict]:
        product_search = context.registry.get("search_products")
        discovery_policy = build_product_discovery_policy(context.plan)
        search_execution = await execute_product_search_with_budget(
            product_search=product_search,
            query=context.query,
            filters=context.filters,
            top_k=discovery_policy.top_k,
            timeout_seconds=context.retrieval_timeout_seconds,
        )
        cards = search_execution.cards
        if search_execution.diagnostics is not None:
            context.metadata["retrieval"] = asdict(search_execution.diagnostics)
        if search_execution.degraded:
            context.metadata["retrieval_degraded"] = {
                "reason": search_execution.reason,
                "timeout_seconds": context.retrieval_timeout_seconds,
            }
        context.metadata["product_discovery"] = discovery_policy.as_metadata()
        context.metadata["catalog_listing"] = discovery_policy.presentation_mode == "listing"
        if should_suppress_unscoped_product_cards(context):
            context.metadata["catalog_gap"] = {
                "reason": "no_structured_product_scope",
                "suppressed_product_ids": [str(card.get("id", "")) for card in cards],
            }
            cards = []
        context.session.candidate_products = [card["id"] for card in cards]
        context.session.candidate_product_cards = cards

        fallback_answer = build_grounded_answer(
            context.message,
            cards,
            context.intent.value,
            presentation_mode=discovery_policy.presentation_mode,
        )
        answer = fallback_answer
        llm_answer = (
            await collect_llm_answer_with_budget(context, cards)
            if discovery_policy.allow_llm_answer
            else ""
        )
        if llm_answer:
            # 判断是否使用序号引用模式
            indexed_map = context.metadata.get("indexed_candidates")
            if indexed_map and isinstance(indexed_map, dict):
                guarded = guard_indexed_answer(
                    answer=clean_answer_text(llm_answer),
                    index_to_id=indexed_map,
                    cards=cards,
                    fallback_answer=fallback_answer,
                    allowed_extra_prices=[context.filters.max_price] if context.filters.max_price is not None else [],
                )
            else:
                guarded = guard_grounded_answer(
                    answer=clean_answer_text(llm_answer),
                    cards=cards,
                    fallback_answer=fallback_answer,
                    allowed_extra_prices=[context.filters.max_price] if context.filters.max_price is not None else [],
                )
            if not guarded.safe:
                yield {
                    "event": "guardrail",
                    "data": {
                        "action": guarded.action,
                        "violations": guarded.violations,
                    },
                }
            context.metadata["grounding"] = {
                "safe": guarded.safe,
                "violations": guarded.violations,
                "citations": guarded.citations,
            }

            # ── 幻觉检测（新增）──
            from server.config import get_settings
            settings = get_settings()
            hallucination_answer = guarded.answer if guarded.safe else fallback_answer

            if settings.enable_hallucination_guard:
                h_report = await guard_hallucination(
                    answer=hallucination_answer,
                    cards=cards,
                    user_message=context.message,
                    enable_sql_check=False,  # SQL 校验默认关闭，需手动开启
                    strict_mode=settings.hallucination_guard_strict_mode,
                    auto_correct=True,
                    max_regenerate=2,
                )

                # 记录幻觉事件
                log_hallucination_event(
                    report=h_report,
                    session_id=context.session_id,
                    trace_id=context.trace_id,
                    log_path=settings.hallucination_log_path or "server/runtime/hallucination.jsonl",
                )

                # 记录幻觉事件到 metrics
                from server.monitoring.metrics import record_hallucination_event
                record_hallucination_event(
                    session_id=context.session_id,
                    trace_id=context.trace_id,
                    action=h_report.action,
                    layers=[v.layer for v in h_report.violations],
                    violation_types=[v.type for v in h_report.violations],
                )

                context.metadata["hallucination"] = {
                    "safe": h_report.safe,
                    "action": h_report.action,
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
                        for v in h_report.violations
                    ],
                    "needs_human_review": h_report.needs_human_review,
                }

                # 根据幻觉检测结果处理回答
                if h_report.action == "auto_correct" and h_report.corrected_answer:
                    # 自动修正：使用修正后的回答
                    hallucination_answer = h_report.corrected_answer
                    context.metadata["answer_source"] = "llm_auto_corrected"
                elif h_report.action == "regenerate" and h_report.regenerate_prompt:
                    # 需要重生成：最多重试 2 次
                    regenerate_answer = await _try_regenerate_answer(
                        context, cards, h_report.regenerate_prompt, max_attempts=2
                    )
                    if regenerate_answer:
                        hallucination_answer = regenerate_answer
                        context.metadata["answer_source"] = "llm_regenerated"
                    else:
                        # 重生成失败，降级到模板
                        hallucination_answer = build_fallback_template(cards)
                        context.metadata["answer_source"] = "hallucination_fallback"
                elif h_report.action == "fallback":
                    # 严重幻觉，直接降级
                    hallucination_answer = h_report.corrected_answer or build_fallback_template(cards)
                    context.metadata["answer_source"] = "hallucination_fallback"
                elif not h_report.safe:
                    # 其他不安全情况，使用 fallback
                    hallucination_answer = build_fallback_template(cards)
                    context.metadata["answer_source"] = "hallucination_fallback"

                answer = hallucination_answer
            else:
                # 幻觉检测关闭，使用 grounding 结果
                context.metadata["answer_source"] = "llm" if guarded.safe else "grounded_fallback_guardrail"
                answer = guarded.answer
        else:
            context.metadata["grounding"] = {
                "safe": True,
                "violations": [],
                "citations": [],
            }
            context.metadata["answer_source"] = "grounded_fallback"

        card_resolution = resolve_product_cards_for_answer(
            answer=answer,
            cards=cards,
            policy=discovery_policy,
        )
        cards = card_resolution.cards
        context.metadata["card_binding"] = card_resolution.metadata
        context.session.candidate_products = [card["id"] for card in cards]
        context.session.candidate_product_cards = cards

        async for item in stream_text(answer):
            yield item

        context.session.add_assistant_message(answer)

        for card in cards:
            yield {"event": "product_card", "data": card}

        yield {
            "event": "done",
            "data": build_done_payload(
                context.session_id,
                context.session,
                needs_clarification=False,
                plan=context.plan,
                trace_id=context.trace_id,
            ),
        }


async def collect_llm_answer_with_budget(context: AgentTurnContext, cards: list[dict]) -> str:
    if context.llm_client is None or not cards:
        return ""
    budget = max(0.0, float(context.recommendation_llm_budget_seconds or 0.0))
    if budget <= 0:
        return ""

    task = asyncio.create_task(collect_llm_answer(context, cards))
    try:
        return await asyncio.wait_for(task, timeout=budget)
    except TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        context.metadata["llm_timeout_budget_seconds"] = budget
        return ""
    except Exception as exc:
        logger.warning("LLM answer stream failed; using grounded fallback answer: %s", exc)
        return ""


async def _try_regenerate_answer(
    context: AgentTurnContext,
    cards: list[dict],
    regenerate_prompt: str,
    max_attempts: int = 2,
) -> str | None:
    """
    尝试用纠错 prompt 让 LLM 重生成回答。
    每次重生成后都跑幻觉检测，直到通过或次数用完。
    """
    from server.config import get_settings
    settings = get_settings()

    for attempt in range(1, max_attempts + 1):
        logger.info("Hallucination regenerate attempt %d/%d", attempt, max_attempts)

        # 构建 messages（使用 user prompt 格式）
        messages = [
            {"role": "system", "content": "你是一名电商导购助手。请根据候选商品和用户需求回答。"},
            {"role": "user", "content": regenerate_prompt},
        ]

        try:
            # 调用 LLM 重生成
            chunks: list[str] = []
            stream = context.llm_client.stream_messages(messages)  # type: ignore[union-attr]
            async for token in stream:
                chunks.append(token)
            regenerated = "".join(chunks)

            if not regenerated.strip():
                continue

            # 对重生成的回答再次进行幻觉检测
            h_report = await guard_hallucination(
                answer=regenerated,
                cards=cards,
                user_message=context.message,
                enable_sql_check=False,
                strict_mode=settings.hallucination_guard_strict_mode,
                auto_correct=True,
                max_regenerate=0,  # 递归调用时不再重试
                regenerate_count=attempt,
            )

            if h_report.safe or h_report.action == "auto_correct":
                # 重生成通过检测
                return h_report.corrected_answer or regenerated

            # 更新 prompt 用于下一次重试
            if h_report.regenerate_prompt:
                regenerate_prompt = h_report.regenerate_prompt

        except Exception as exc:
            logger.warning("Regenerate attempt %d failed: %s", attempt, exc)
            continue

    logger.warning("All %d regenerate attempts failed", max_attempts)
    return None


def _should_use_lite_model(context: AgentTurnContext, cards: list[dict]) -> bool:
    """
    判断当前查询是否适合使用小模型（Lite Model）。

    简单查询标准：
      - 单个商品详情查询（ask_product_detail）
      - 简单推荐（recommend）且候选商品 <= 3 个
      - 无复杂上下文追问（reference_type == none）

    复杂查询（不用小模型）：
      - 对比（compare）、场景搭配（bundle）
      - 候选商品 > 5 个
      - 有明确的上下文引用（reference_type != none）
    """
    if context.lite_llm_client is None:
        return False

    intent = context.plan.intent
    if intent in {"compare", "bundle"}:
        return False
    if len(cards) > 5:
        return False
    if context.plan.reference_type != "none":
        return False
    if intent == "ask_product_detail":
        return True
    if intent == "recommend" and len(cards) <= 3:
        return True
    return False


async def collect_llm_answer(context: AgentTurnContext, cards: list[dict]) -> str:
    chunks: list[str] = []

    # 判断是否使用序号引用模式（防ID幻觉）
    from server.config import get_settings
    settings = get_settings()
    use_indexed = settings.use_indexed_grounding

    # 选择模型：简单查询用小模型，复杂查询用大模型
    use_lite = _should_use_lite_model(context, cards)
    selected_client = context.lite_llm_client if use_lite else context.llm_client
    model_tag = "lite" if use_lite else "pro"

    if use_lite:
        logger.info("Using lite model for simple query (intent=%s, cards=%d)",
                    context.plan.intent, len(cards))
        context.metadata["llm_model"] = model_tag

    if use_indexed:
        messages, index_to_id = build_indexed_grounded_messages(
            context.message, cards, context.intent.value
        )
        context.metadata["indexed_candidates"] = index_to_id
        stream = selected_client.stream_messages(  # type: ignore[union-attr]
            messages,
            max_tokens=settings.llm_answer_max_tokens if settings.llm_answer_max_tokens > 0 else None,
        )
    else:
        stream = selected_client.stream_answer(  # type: ignore[union-attr]
            context.message,
            cards,
            context.intent.value,
        )

    async for token in stream:
        chunks.append(token)
    return "".join(chunks)


async def execute_product_search_with_budget(
    *,
    product_search,
    query: str,
    filters,
    top_k: int,
    timeout_seconds: float,
) -> ProductSearchExecution:
    def run_search() -> ProductSearchExecution:
        if hasattr(product_search, "run_with_diagnostics"):
            result = product_search.run_with_diagnostics(query=query, filters=filters, top_k=top_k)
            return ProductSearchExecution(cards=result.cards, diagnostics=result.diagnostics)
        cards = product_search.run(query=query, filters=filters, top_k=top_k)
        return ProductSearchExecution(cards=cards)

    try:
        if timeout_seconds <= 0:
            return await asyncio.to_thread(run_search)
        return await asyncio.wait_for(asyncio.to_thread(run_search), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning("Product retrieval timed out after %.3fs; using empty fallback.", timeout_seconds)
        return ProductSearchExecution(cards=[], degraded=True, reason="retrieval_timeout")
    except Exception as exc:
        logger.warning("Product retrieval failed; using empty fallback: %s", exc)
        return ProductSearchExecution(cards=[], degraded=True, reason="retrieval_error")


def clean_answer_text(answer: str) -> str:
    text = MARKDOWN_EMPHASIS_PATTERN.sub(lambda match: match.group(2), answer)
    return text.replace("```", "").replace("`", "").strip()


def should_suppress_unscoped_product_cards(context: AgentTurnContext) -> bool:
    if context.plan.intent not in {"recommend", "browse"}:
        return False
    if context.plan.reference_type != "none":
        return False
    filters = context.filters
    return not any(
        [
            filters.product_types,
            filters.categories,
            filters.brands,
            filters.preferred_brands,
            filters.keywords,
            filters.should_keywords,
            filters.facets,
        ]
    )
