import re

from server.commerce.facts import get_commerce_gateway
from server.commerce.models import ProductFacts


DESCRIPTION_FAQ_MARKERS = (" 问：", "\n问：", " Q:", "\nQ:")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?；;])\s*")
PRIORITY_EVIDENCE_TERMS = (
    "核心卖点",
    "主打",
    "搭载",
    "搭配",
    "适合",
    "适用",
    "场景",
    "日常",
    "建议",
)


def build_reason(metadata: dict, query: str) -> str:
    tags = metadata.get("tags", [])
    matched = matched_query_terms(tags, query)
    evidence = build_evidence_summary(metadata, query=query, matched_terms=matched)
    if matched and evidence:
        return f"匹配你提到的{format_terms(matched)}，{evidence}"
    if evidence:
        return evidence
    if matched:
        return f"匹配你提到的{format_terms(matched)}，可作为当前需求的候选商品。"
    return "与当前检索条件相近，可继续结合预算、尺码或使用场景确认。"


def build_product_evidence(
    metadata: dict,
    hit: dict,
    *,
    query: str,
    reason: str,
    product_facts: ProductFacts | None = None,
) -> dict:
    matched_terms = matched_query_terms(metadata.get("tags", []), query)
    product_facts = product_facts or get_commerce_gateway().enrich_product(metadata)
    price_fact = product_facts.fact("price")
    stock_fact = product_facts.fact("stock")
    retrieval_score = float(hit.get("raw_score", hit.get("score", 0.0)))
    rerank_score = float(hit.get("score", retrieval_score))
    return {
        "source": "product_catalog",
        "product_id": product_facts.product_id,
        "sku_id": product_facts.sku_id,
        "name": product_facts.fact("name").value if product_facts.fact("name").available else metadata["name"],
        "brand": product_facts.fact("brand").value if product_facts.fact("brand").available else metadata["brand"],
        "category": product_facts.fact("category").value if product_facts.fact("category").available else metadata["category"],
        "product_types": metadata.get("product_types", []),
        "price": float(price_fact.value if price_fact.available else metadata["price"]),
        "stock": stock_fact.value if stock_fact.available else metadata.get("stock"),
        "field_sources": product_facts.field_sources,
        "missing_fields": product_facts.missing_fields,
        "conflicts": product_facts.conflicts,
        "reason": reason,
        "highlights": build_evidence_highlights(metadata, matched_terms=matched_terms, query=query),
        "matched_terms": matched_terms,
        "score": rerank_score,
        "retrieval_score": retrieval_score,
        "rerank_score": rerank_score,
        "retrieval_rank": hit.get("retrieval_rank"),
    }


def matched_query_terms(tags: list, query: str) -> list[str]:
    normalized_query = str(query).lower()
    matched: list[str] = []
    for tag in tags:
        value = str(tag).strip()
        if not value:
            continue
        if len(value) > 18 and ":" not in value:
            continue
        if value.lower() in normalized_query and value not in matched:
            matched.append(value)
    return matched[:4]


def format_terms(terms: list[str]) -> str:
    if not terms:
        return "核心条件"
    return "、".join(terms[:3])


def build_evidence_summary(metadata: dict, *, query: str, matched_terms: list[str]) -> str:
    highlights = build_evidence_highlights(metadata, matched_terms=matched_terms, query=query)
    return join_complete_sentences(highlights, max_sentences=2)


def build_evidence_highlights(metadata: dict, *, matched_terms: list[str], query: str = "") -> list[str]:
    description = clean_description(str(metadata.get("description", "")))
    if not description:
        return []

    sentences = split_sentences(description)
    if not sentences:
        return []

    scored: list[tuple[int, int, str]] = []
    query_terms = [term for term in matched_terms if len(term) >= 2]
    normalized_query = query.lower()
    for index, sentence in enumerate(sentences):
        score = 0
        for term in query_terms:
            if term in sentence:
                score += 12
        for term in PRIORITY_EVIDENCE_TERMS:
            if term in sentence:
                score += 6
        if normalized_query and any(chunk and chunk in sentence.lower() for chunk in normalized_query.split()):
            score += 4
        if index == 0:
            score += 3
        if "注意" in sentence and score < 10:
            score -= 3
        scored.append((score, -index, sentence))

    selected = [item[2] for item in sorted(scored, reverse=True)[:2]]
    selected.sort(key=lambda sentence: sentences.index(sentence))
    return selected


def clean_description(description: str) -> str:
    text = " ".join(description.split())
    for marker in DESCRIPTION_FAQ_MARKERS:
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    return text.strip()


def split_sentences(text: str) -> list[str]:
    sentences = [piece.strip() for piece in SENTENCE_SPLIT_PATTERN.split(text) if piece.strip()]
    return [sentence for sentence in sentences if sentence]


def join_complete_sentences(sentences: list[str], max_sentences: int = 2) -> str:
    selected = sentences[: max(max_sentences, 1)]
    return "".join(ensure_sentence_ending(sentence) for sentence in selected).strip()


def ensure_sentence_ending(sentence: str) -> str:
    stripped = sentence.strip()
    if not stripped:
        return ""
    if stripped[-1] in "。！？!?；;":
        return stripped
    return f"{stripped}。"
