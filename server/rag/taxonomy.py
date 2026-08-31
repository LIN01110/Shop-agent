"""
Purpose: 商品类目体系：从 product_taxonomy.json 加载类目，提供别名匹配和推断能力。
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TAXONOMY_PATH = ROOT_DIR / "data" / "product_taxonomy.json"


@dataclass(frozen=True)
class ProductType:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    query_aliases: tuple[str, ...]
    typo_tolerant_aliases: tuple[str, ...]
    normalization_replacements: tuple[tuple[str, str], ...]
    typo_tolerance: int
    compound_aliases: tuple[tuple[str, ...], ...]
    exclude_terms: tuple[str, ...]
    required_categories: tuple[str, ...]
    match_fields: tuple[str, ...]


@dataclass(frozen=True)
class ProductTypeMatch:
    product_type_id: str
    alias: str


@lru_cache
def load_product_taxonomy(path: str | None = None) -> dict[str, ProductType]:
    taxonomy_path = Path(path) if path else DEFAULT_TAXONOMY_PATH
    payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    product_types: dict[str, ProductType] = {}
    for item in payload.get("product_types", []):
        product_type = ProductType(
            id=item["id"],
            display_name=item["display_name"],
            aliases=tuple(item.get("aliases", [])),
            query_aliases=tuple(item.get("query_aliases", [])),
            typo_tolerant_aliases=tuple(item.get("typo_tolerant_aliases", [])),
            normalization_replacements=tuple(
                (str(pair[0]), str(pair[1]))
                for pair in item.get("normalization_replacements", [])
                if isinstance(pair, list | tuple) and len(pair) == 2
            ),
            typo_tolerance=int(item.get("typo_tolerance", 0)),
            compound_aliases=tuple(tuple(group) for group in item.get("compound_aliases", [])),
            exclude_terms=tuple(item.get("exclude_terms", [])),
            required_categories=tuple(item.get("required_categories", [])),
            match_fields=tuple(item.get("match_fields", ["name", "sub_category", "tags"])),
        )
        product_types[product_type.id] = product_type
    return product_types


def extract_product_type_matches(message: str) -> list[ProductTypeMatch]:
    matches: list[ProductTypeMatch] = []
    for product_type in load_product_taxonomy().values():
        normalized_message = normalize_query_text(message, product_type.normalization_replacements)
        normalized_excludes = tuple(
            normalize_query_text(term, product_type.normalization_replacements)
            for term in product_type.exclude_terms
        )
        for alias in sorted(query_aliases(product_type), key=len, reverse=True):
            normalized_alias = normalize_query_text(alias, product_type.normalization_replacements)
            if not normalized_alias:
                continue
            if normalized_alias in normalized_message and not alias_match_is_excluded(
                normalized_message,
                normalized_alias,
                normalized_excludes,
            ):
                matches.append(ProductTypeMatch(product_type_id=product_type.id, alias=alias))
                break
        else:
            for group in product_type.compound_aliases:
                normalized_group = [
                    normalize_query_text(term, product_type.normalization_replacements)
                    for term in group
                ]
                if group and all(term in normalized_message for term in normalized_group):
                    matches.append(ProductTypeMatch(product_type_id=product_type.id, alias="+".join(group)))
                    break
            else:
                fuzzy_alias = fuzzy_product_type_alias(message, product_type)
                if fuzzy_alias:
                    matches.append(ProductTypeMatch(product_type_id=product_type.id, alias=fuzzy_alias))
    return matches


def canonicalize_product_type(value: str) -> str:
    value = value.strip()
    product_types = load_product_taxonomy()
    if value in product_types:
        return value
    for product_type in product_types.values():
        if value == product_type.display_name or value in query_aliases(product_type):
            return product_type.id
        if any(value == "+".join(group) for group in product_type.compound_aliases):
            return product_type.id
    return value


def product_type_matches(value: str, metadata: dict) -> bool:
    canonical = canonicalize_product_type(value)
    return canonical in infer_product_type_ids(metadata)


def infer_product_type_ids(metadata: dict) -> list[str]:
    explicit = explicit_product_type_ids(metadata)
    if explicit:
        return explicit
    return infer_product_type_ids_from_fields(metadata)


def infer_product_type_ids_from_fields(metadata: dict) -> list[str]:
    matched: list[str] = []
    for product_type in load_product_taxonomy().values():
        if not metadata_matches_required_categories(metadata, product_type.required_categories):
            continue
        haystack = build_match_haystack(metadata, product_type.match_fields)
        if any(
            alias.lower() in haystack
            and not alias_match_is_excluded(haystack, alias.lower(), product_type.exclude_terms)
            for alias in product_type.aliases
        ) or any(
            all(term.lower() in haystack for term in group)
            for group in product_type.compound_aliases
            if group
        ):
            matched.append(product_type.id)
    return matched


def query_aliases(product_type: ProductType) -> tuple[str, ...]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in (
        product_type.display_name,
        *product_type.aliases,
        *product_type.query_aliases,
    ):
        if value and value not in seen:
            seen.add(value)
            aliases.append(value)
    return tuple(aliases)


def fuzzy_product_type_alias(message: str, product_type: ProductType) -> str:
    max_distance = max(0, product_type.typo_tolerance)
    if max_distance <= 0 or not product_type.typo_tolerant_aliases:
        return ""
    normalized_message = normalize_query_text(message, product_type.normalization_replacements)
    normalized_excludes = tuple(
        normalize_query_text(term, product_type.normalization_replacements)
        for term in product_type.exclude_terms
    )
    for alias in sorted(product_type.typo_tolerant_aliases, key=len, reverse=True):
        normalized_alias = normalize_query_text(alias, product_type.normalization_replacements)
        if len(normalized_alias) < 4:
            continue
        if alias_match_is_excluded(normalized_message, normalized_alias, normalized_excludes):
            continue
        if fuzzy_contains(normalized_message, normalized_alias, max_distance):
            return alias
    return ""


def normalize_query_text(text: str, replacements: tuple[tuple[str, str], ...] = ()) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    for source, target in replacements:
        normalized = normalized.replace(source.lower(), target.lower())
    return re.sub(r"[\s\-_·・/\\]+", "", normalized)


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


def explicit_product_type_ids(metadata: dict) -> list[str]:
    raw = metadata.get("product_types") or metadata.get("product_type") or []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = []

    product_types = load_product_taxonomy()
    canonical_ids: list[str] = []
    for value in values:
        canonical = canonicalize_product_type(value)
        if canonical in product_types and canonical not in canonical_ids:
            canonical_ids.append(canonical)
    return canonical_ids


def product_type_display_names(product_type_ids: list[str]) -> list[str]:
    product_types = load_product_taxonomy()
    names: list[str] = []
    for value in product_type_ids:
        canonical = canonicalize_product_type(value)
        product_type = product_types.get(canonical)
        if product_type and product_type.display_name not in names:
            names.append(product_type.display_name)
    return names


def enrich_product_type_metadata(metadata: dict) -> dict:
    enriched = dict(metadata)
    product_types = (
        infer_product_type_ids_from_fields(enriched)
        if has_product_type_source_fields(enriched)
        else infer_product_type_ids(enriched)
    )
    if product_types:
        enriched["product_types"] = product_types
    else:
        enriched.pop("product_types", None)
    return enriched


def has_product_type_source_fields(metadata: dict) -> bool:
    return any(field in metadata for field in ["name", "category", "sub_category", "tags"])


def build_match_haystack(metadata: dict, fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for field in fields:
        value = metadata.get(field, "")
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value))
    return " ".join(parts).lower()


def metadata_matches_required_categories(metadata: dict, required_categories: tuple[str, ...]) -> bool:
    if not required_categories:
        return True
    category = str(metadata.get("category", "")).lower()
    if not category:
        return True
    return any(required.lower() in category for required in required_categories)


def alias_match_is_excluded(text: str, alias: str, exclude_terms: tuple[str, ...]) -> bool:
    if not exclude_terms:
        return False
    lowered_text = text.lower()
    lowered_alias = alias.lower()
    alias_spans = [
        (match.start(), match.end())
        for match in re.finditer(re.escape(lowered_alias), lowered_text)
    ]
    if not alias_spans:
        return False

    excluded_spans: list[tuple[int, int]] = []
    for term in exclude_terms:
        lowered_term = term.lower()
        excluded_spans.extend(
            (match.start(), match.end())
            for match in re.finditer(re.escape(lowered_term), lowered_text)
        )
    if not excluded_spans:
        return False

    return all(
        any(
            spans_overlap(alias_start, alias_end, excluded_start, excluded_end)
            for excluded_start, excluded_end in excluded_spans
        )
        for alias_start, alias_end in alias_spans
    )


def spans_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and right_start < left_end
