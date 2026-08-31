"""
Purpose: 商品分类体系：从 category_taxonomy.json 加载分类，提供分类匹配和推断。
"""

import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
from pathlib import Path

from server.rag.scoring import tokenize


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CATEGORY_TAXONOMY_PATH = ROOT_DIR / "data" / "category_taxonomy.json"
DEFAULT_PRODUCT_DATA_PATH = ROOT_DIR / "data" / "products_ref.json"
GENERIC_QUERY_TOKENS = frozenset(
    {
        "推荐",
        "推荐一款",
        "一款",
        "一个",
        "一件",
        "一双",
        "一下",
        "看看",
        "所有",
        "全部",
        "商品",
        "产品",
    }
)


@dataclass(frozen=True)
class Category:
    id: str
    display_name: str
    catalog_categories: tuple[str, ...]
    aliases: tuple[str, ...]
    query_aliases: tuple[str, ...]
    typo_tolerant_aliases: tuple[str, ...]
    normalization_replacements: tuple[tuple[str, str], ...]
    typo_tolerance: int
    match_fields: tuple[str, ...]


@dataclass(frozen=True)
class CategoryMatch:
    category_id: str
    alias: str
    source: str = "taxonomy"


@dataclass(frozen=True)
class CategoryProfile:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    exact_terms: tuple[str, ...]
    tokens: frozenset[str]


@lru_cache
def load_category_taxonomy(path: str | None = None) -> dict[str, Category]:
    taxonomy_path = Path(path) if path else DEFAULT_CATEGORY_TAXONOMY_PATH
    payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    categories: dict[str, Category] = {}
    for item in payload.get("categories", []):
        category = Category(
            id=item["id"],
            display_name=item["display_name"],
            catalog_categories=tuple(item.get("catalog_categories", [])),
            aliases=tuple(item.get("aliases", [])),
            query_aliases=tuple(item.get("query_aliases", [])),
            typo_tolerant_aliases=tuple(item.get("typo_tolerant_aliases", [])),
            normalization_replacements=tuple(
                (str(pair[0]), str(pair[1]))
                for pair in item.get("normalization_replacements", [])
                if isinstance(pair, list | tuple) and len(pair) == 2
            ),
            typo_tolerance=int(item.get("typo_tolerance", 0)),
            match_fields=tuple(item.get("match_fields", ["category"])),
        )
        categories[category.id] = category
    return categories


def extract_category_matches(message: str) -> list[CategoryMatch]:
    matches: list[CategoryMatch] = []
    for category in load_category_taxonomy().values():
        normalized_message = normalize_category_text(message, category.normalization_replacements)
        for alias in sorted(query_aliases(category), key=len, reverse=True):
            normalized_alias = normalize_category_text(alias, category.normalization_replacements)
            if normalized_alias and normalized_alias in normalized_message:
                matches.append(CategoryMatch(category_id=category.id, alias=alias))
                break
        else:
            fuzzy_alias = fuzzy_category_alias(message, category)
            if fuzzy_alias:
                matches.append(CategoryMatch(category_id=category.id, alias=fuzzy_alias))
    seen = {match.category_id for match in matches}
    for match in classify_category_from_catalog(message):
        if match.category_id in seen:
            continue
        seen.add(match.category_id)
        matches.append(match)
    return matches


def canonicalize_category(value: str) -> str:
    value = value.strip()
    configured = canonicalize_configured_category(value)
    if configured != value:
        return configured

    for profile in load_category_profiles().values():
        if value == profile.id or value == profile.display_name or value in profile.aliases:
            return profile.id
    if value.startswith("catalog."):
        return value
    if value:
        return dynamic_category_id(value)
    return value


def canonicalize_configured_category(value: str) -> str:
    value = value.strip()
    categories = load_category_taxonomy()
    if value in categories:
        return value
    for category in categories.values():
        if value == category.display_name or value in category.catalog_categories or value in query_aliases(category):
            return category.id
    return value


def category_matches(value: str, metadata: dict) -> bool:
    canonical = canonicalize_category(value)
    return canonical in infer_category_ids(metadata)


def infer_category_ids(metadata: dict) -> list[str]:
    explicit = explicit_category_ids(metadata)
    if explicit:
        return explicit

    matched: list[str] = []
    for category in load_category_taxonomy().values():
        haystack = build_match_haystack(metadata, category.match_fields)
        catalog_terms = tuple(term.lower() for term in category.catalog_categories)
        if any(term and term in haystack for term in catalog_terms):
            matched.append(category.id)
            continue
        if any(alias.lower() in haystack for alias in category.aliases):
            matched.append(category.id)
    if matched:
        return matched

    raw_category = str(metadata.get("category", "")).strip()
    if raw_category:
        return [canonicalize_category(raw_category)]
    return []


def explicit_category_ids(metadata: dict) -> list[str]:
    raw = metadata.get("category_ids") or metadata.get("categories") or metadata.get("category_id") or []
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        values = []

    categories = load_category_taxonomy()
    canonical_ids: list[str] = []
    for value in values:
        canonical = canonicalize_category(value)
        if (canonical in categories or canonical.startswith("catalog.")) and canonical not in canonical_ids:
            canonical_ids.append(canonical)
    return canonical_ids


def category_display_names(category_ids: list[str]) -> list[str]:
    categories = load_category_taxonomy()
    names: list[str] = []
    for value in category_ids:
        canonical = canonicalize_category(value)
        category = categories.get(canonical)
        if category and category.display_name not in names:
            names.append(category.display_name)
            continue
        profile = load_category_profiles().get(canonical)
        if profile and profile.display_name not in names:
            names.append(profile.display_name)
    return names


@lru_cache
def load_category_profiles(data_path: str | None = None) -> dict[str, CategoryProfile]:
    profiles: dict[str, CategoryProfile] = {}
    terms_by_id: dict[str, list[str]] = defaultdict(list)
    display_by_id: dict[str, str] = {}

    for category in load_category_taxonomy().values():
        terms = [
            category.display_name,
            *category.catalog_categories,
            *category.aliases,
            *category.query_aliases,
            *category.typo_tolerant_aliases,
        ]
        terms_by_id[category.id].extend(terms)
        display_by_id[category.id] = category.display_name

    catalog_path = resolve_product_data_path(data_path)
    if catalog_path.exists():
        try:
            products = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            products = []
        for item in products if isinstance(products, list) else []:
            raw_category = str(item.get("category", "")).strip()
            if not raw_category:
                continue
            category_id = canonicalize_configured_category(raw_category)
            if category_id == raw_category:
                category_id = dynamic_category_id(raw_category)
            display_by_id.setdefault(category_id, raw_category)
            terms_by_id[category_id].append(raw_category)
            append_catalog_terms(terms_by_id[category_id], item)

    for category_id, terms in terms_by_id.items():
        cleaned_terms = dedupe_terms(terms)
        profile_text = " ".join(cleaned_terms)
        profiles[category_id] = CategoryProfile(
            id=category_id,
            display_name=display_by_id.get(category_id, category_id),
            aliases=tuple(cleaned_terms),
            exact_terms=tuple(
                term
                for term in cleaned_terms
                if len(normalize_category_text(term)) >= 2 and normalize_category_text(term) not in GENERIC_QUERY_TOKENS
            ),
            tokens=frozenset(token for token in tokenize(profile_text) if token not in GENERIC_QUERY_TOKENS),
        )
    return profiles


def classify_category_from_catalog(message: str, *, min_score: float = 8.0, margin: float = 2.0) -> list[CategoryMatch]:
    scored: list[tuple[float, CategoryProfile, str]] = []
    normalized_message = normalize_category_text(message)
    query_tokens = {token for token in tokenize(message) if token not in GENERIC_QUERY_TOKENS}
    if not normalized_message or not query_tokens:
        return []

    for profile in load_category_profiles().values():
        score, alias = score_category_profile(normalized_message, query_tokens, profile)
        if score >= min_score:
            scored.append((score, profile, alias))

    if not scored:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_profile, best_alias = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if second_score and best_score - second_score < margin:
        return []
    return [CategoryMatch(category_id=best_profile.id, alias=best_alias, source="catalog_profile")]


def score_category_profile(
    normalized_message: str,
    query_tokens: set[str],
    profile: CategoryProfile,
) -> tuple[float, str]:
    score = 0.0
    best_alias = ""
    for term in sorted(profile.exact_terms, key=len, reverse=True):
        normalized_term = normalize_category_text(term)
        if not normalized_term or normalized_term in GENERIC_QUERY_TOKENS:
            continue
        if normalized_term in normalized_message:
            score += 8.0 + min(len(normalized_term), 8) * 0.5
            if not best_alias:
                best_alias = term

    overlap = query_tokens & profile.tokens
    score += min(len(overlap), 8) * 1.0
    if not best_alias and overlap:
        best_alias = sorted(overlap, key=len, reverse=True)[0]
    return score, best_alias or profile.display_name


def resolve_product_data_path(data_path: str | None = None) -> Path:
    raw_path = data_path or os.getenv("PRODUCT_DATA_PATH")
    if not raw_path:
        return DEFAULT_PRODUCT_DATA_PATH
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def append_catalog_terms(target: list[str], item: dict) -> None:
    target.append(str(item.get("sub_category", "")).strip())
    tags = item.get("tags", [])
    if isinstance(tags, list):
        category = str(item.get("category", "")).strip()
        sub_category = str(item.get("sub_category", "")).strip()
        target.extend(
            str(tag).strip()
            for tag in tags[:6]
            if str(tag).strip() in {category, sub_category}
        )


def dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        value = str(term).strip()
        normalized = normalize_category_text(value)
        if not value or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def dynamic_category_id(display_name: str) -> str:
    normalized = normalize_category_text(display_name)
    digest = sha1(normalized.encode("utf-8")).hexdigest()[:10]
    return f"catalog.{digest}"


def query_aliases(category: Category) -> tuple[str, ...]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in (
        category.display_name,
        *category.catalog_categories,
        *category.aliases,
        *category.query_aliases,
    ):
        if value and value not in seen:
            seen.add(value)
            aliases.append(value)
    return tuple(aliases)


def fuzzy_category_alias(message: str, category: Category) -> str:
    max_distance = max(0, category.typo_tolerance)
    if max_distance <= 0 or not category.typo_tolerant_aliases:
        return ""
    normalized_message = normalize_category_text(message, category.normalization_replacements)
    for alias in sorted(category.typo_tolerant_aliases, key=len, reverse=True):
        normalized_alias = normalize_category_text(alias, category.normalization_replacements)
        if len(normalized_alias) < 4:
            continue
        if fuzzy_contains(normalized_message, normalized_alias, max_distance):
            return alias
    return ""


def normalize_category_text(text: str, replacements: tuple[tuple[str, str], ...] = ()) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    for source, target in replacements:
        normalized = normalized.replace(source.lower(), target.lower())
    return re.sub(r"[\s\-_\\]+", "", normalized)


def fuzzy_contains(text: str, needle: str, max_distance: int) -> bool:
    if not text or not needle:
        return False
    if needle in text:
        return True
    min_length = max(1, len(needle) - max_distance)
    max_length = len(needle) + max_distance
    for start in range(len(text)):
        for end in range(start + min_length, min(len(text), start + max_length) + 1):
            if levenshtein_distance(text[start:end], needle, max_distance) <= max_distance:
                return True
    return False


def levenshtein_distance(left: str, right: str, cutoff: int) -> int:
    if abs(len(left) - len(right)) > cutoff:
        return cutoff + 1
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        row_min = current[0]
        for j, right_char in enumerate(right, 1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > cutoff:
            return cutoff + 1
        previous = current
    return previous[-1]


def build_match_haystack(metadata: dict, fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for field in fields:
        value = metadata.get(field, "")
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value))
    return " ".join(parts).lower()
