"""
Purpose: 对比商品选择：从候选商品中选出适合对比的商品对。
"""

from dataclasses import replace

from server.rag.post_process import SearchFilters
from server.tools.product_compare_terms import extract_comparison_terms
from server.tools.product_search import ProductSearchTool


def collect_comparison_cards(
    search_tool: ProductSearchTool,
    query: str,
    filters: SearchFilters,
    top_k: int = 2,
) -> list[dict]:
    terms = extract_comparison_terms(query)
    cards: list[dict] = []
    seen: set[str] = set()
    term_filters = replace(filters, max_price=None)

    for term in terms:
        candidates = search_tool.run(query=f"{term} {query}", filters=term_filters, top_k=max(top_k * 4, 8))
        selected = first_matching_card(candidates, term, seen, filters.max_price) or first_unseen_card(
            candidates, seen, filters.max_price
        )
        if selected:
            cards.append(selected)
            seen.add(selected["id"])

    if len(cards) < top_k:
        for card in search_tool.run(query=query, filters=filters, top_k=max(top_k * 3, 5)):
            if card["id"] not in seen:
                cards.append(card)
                seen.add(card["id"])
            if len(cards) >= top_k:
                break

    return cards[:top_k]


def first_matching_card(
    candidates: list[dict],
    term: str,
    seen: set[str],
    max_price: float | None,
) -> dict | None:
    matches = [
        card
        for card in candidates
        if card["id"] not in seen and comparison_term_matches_card(term, card)
    ]
    return first_within_budget(matches, max_price) or (matches[0] if matches else None)


def first_unseen_card(candidates: list[dict], seen: set[str], max_price: float | None) -> dict | None:
    unseen = [card for card in candidates if card["id"] not in seen]
    return first_within_budget(unseen, max_price) or (unseen[0] if unseen else None)


def first_within_budget(candidates: list[dict], max_price: float | None) -> dict | None:
    if max_price is None:
        return None
    for card in candidates:
        if float(card.get("price", 0)) <= max_price:
            return card
    return None


def comparison_term_matches_card(term: str, card: dict) -> bool:
    needle = term.strip().lower()
    if not needle:
        return False

    haystack = " ".join(
        [
            str(card.get("name", "")),
            str(card.get("brand", "")),
            str(card.get("category", "")),
        ]
    ).lower()
    return needle in haystack
