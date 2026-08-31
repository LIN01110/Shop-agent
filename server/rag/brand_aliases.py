"""
Purpose: 品牌别名处理：从 brand_aliases.json 加载品牌别名，支持品牌提及识别和归一化。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PRODUCT_DATA_PATH = ROOT_DIR / "data" / "products_ref.json"
DEFAULT_ALIAS_PATH = ROOT_DIR / "data" / "brand_aliases.json"

ASCII_RE = re.compile(r"^[a-z0-9][a-z0-9\s\-\.'&]*$")
ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
SCRIPT_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")
SEPARATOR_RE = re.compile(r"[\s_\-·•/\\|]+")
NON_SEARCH_CHAR_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

try:  # Optional in local dev; listed in requirements for production parity.
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover - environment-dependent optional dependency.
    lazy_pinyin = None


@dataclass(frozen=True)
class BrandAliasGroup:
    canonical: str
    aliases: tuple[str, ...]
    normalized_aliases: frozenset[str]
    compact_aliases: frozenset[str]


class BrandAliasCatalog:
    """Data-driven brand resolver for bilingual and fuzzy brand matching.

    This follows the same shape as production search stacks: normalize first,
    expand synonyms from a governed dictionary, then optionally allow tightly
    bounded fuzzy matching. Hard filters use exact alias groups by default.
    """

    def __init__(self, groups: Iterable[BrandAliasGroup]) -> None:
        self.groups = tuple(groups)
        self.alias_to_group: dict[str, BrandAliasGroup] = {}
        self.compact_alias_to_group: dict[str, BrandAliasGroup] = {}
        for group in self.groups:
            for alias in group.normalized_aliases:
                self.alias_to_group.setdefault(alias, group)
            for alias in group.compact_aliases:
                self.compact_alias_to_group.setdefault(alias, group)

    @classmethod
    def from_files(
        cls,
        product_data_path: Path = DEFAULT_PRODUCT_DATA_PATH,
        alias_path: Path = DEFAULT_ALIAS_PATH,
    ) -> "BrandAliasCatalog":
        product_brands = load_product_brands(product_data_path)
        manual_groups = load_manual_alias_groups(alias_path)
        canonical_aliases = merge_manual_and_product_brands(product_brands, manual_groups)

        groups: list[BrandAliasGroup] = []
        for canonical, aliases in sorted(canonical_aliases.items(), key=lambda item: normalize_text(item[0])):
            surface_aliases = sorted(expand_surface_aliases([canonical, *aliases]), key=lambda item: (len(item), item))
            normalized_aliases = frozenset(normalize_text(alias) for alias in surface_aliases if normalize_text(alias))
            compact_aliases = frozenset(compact_text(alias) for alias in surface_aliases if compact_text(alias))
            groups.append(
                BrandAliasGroup(
                    canonical=canonical,
                    aliases=tuple(surface_aliases),
                    normalized_aliases=normalized_aliases,
                    compact_aliases=compact_aliases,
                )
            )
        return cls(groups)

    def expand(self, values: Iterable[str]) -> list[str]:
        expanded: list[str] = []
        for value in values:
            group = self.resolve(value)
            aliases = group.aliases if group else tuple(expand_surface_aliases([value]))
            for alias in aliases:
                append_unique(expanded, str(alias).strip().casefold())
                append_unique(expanded, normalize_text(alias))
                compact = compact_text(alias)
                if compact and compact != normalize_text(alias):
                    append_unique(expanded, compact)
        return expanded

    def resolve(self, value: str, *, allow_fuzzy: bool = False) -> BrandAliasGroup | None:
        normalized = normalize_text(value)
        compact = compact_text(value)
        if not normalized and not compact:
            return None

        exact = self.alias_to_group.get(normalized) or self.compact_alias_to_group.get(compact)
        if exact is not None:
            return exact
        if allow_fuzzy:
            return self.fuzzy_resolve(value)
        return None

    def fuzzy_resolve(self, value: str, *, threshold: float = 0.92) -> BrandAliasGroup | None:
        compact = compact_text(value)
        if len(compact) < 4 or not compact.isascii():
            return None

        best: tuple[float, BrandAliasGroup] | None = None
        for alias, group in self.compact_alias_to_group.items():
            if len(alias) < 4 or not alias.isascii():
                continue
            ratio = SequenceMatcher(None, compact, alias).ratio()
            if ratio >= threshold and (best is None or ratio > best[0]):
                best = (ratio, group)
        return best[1] if best else None

    def matches(self, query_brand: str, candidate_brand: str) -> bool:
        query_group = self.resolve(query_brand)
        candidate_group = self.resolve(candidate_brand)
        if query_group is not None and candidate_group is not None:
            return query_group.canonical == candidate_group.canonical
        return alias_in_text(candidate_brand, query_brand) or alias_in_text(query_brand, candidate_brand)

    def is_known_brand_alias(self, value: str) -> bool:
        return self.resolve(value) is not None

    def contains_brand(self, text: str, brand: str) -> bool:
        group = self.resolve(brand)
        aliases = group.aliases if group else tuple(expand_surface_aliases([brand]))
        return any(alias_in_text(text, alias) for alias in aliases)

    def matched_aliases(self, text: str, brand: str) -> list[str]:
        group = self.resolve(brand)
        aliases = group.aliases if group else tuple(expand_surface_aliases([brand]))
        return [alias for alias in aliases if alias_in_text(text, alias)]

    def extract_mentions(self, text: str) -> list[str]:
        mentions: list[str] = []
        for group in self.groups:
            if any(alias_in_text(text, alias) for alias in group.aliases):
                append_unique(mentions, group.canonical)
        return mentions


def load_product_brands(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        products = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    brands: list[str] = []
    for item in products:
        if isinstance(item, dict):
            brand = str(item.get("brand", "")).strip()
            if brand:
                append_unique(brands, brand)
    return brands


def load_manual_alias_groups(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, set[str]] = {}
    for item in payload.get("brands", []):
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical", "")).strip()
        if not canonical:
            continue
        groups.setdefault(canonical, set()).add(canonical)
        for alias in item.get("aliases", []):
            alias = str(alias).strip()
            if alias:
                groups[canonical].add(alias)
    return groups


def merge_manual_and_product_brands(
    product_brands: Iterable[str],
    manual_groups: dict[str, set[str]],
) -> dict[str, set[str]]:
    canonical_aliases = {canonical: set(aliases) for canonical, aliases in manual_groups.items()}
    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in canonical_aliases.items():
        for alias in expand_surface_aliases(aliases):
            alias_to_canonical[normalize_text(alias)] = canonical
            alias_to_canonical[compact_text(alias)] = canonical

    for brand in product_brands:
        variants = expand_surface_aliases([brand])
        canonical = next(
            (
                alias_to_canonical[key]
                for variant in variants
                for key in (normalize_text(variant), compact_text(variant))
                if key in alias_to_canonical
            ),
            brand,
        )
        canonical_aliases.setdefault(canonical, set()).add(brand)
    return canonical_aliases


def expand_surface_aliases(values: Iterable[str]) -> set[str]:
    aliases: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        normalized = normalize_text(value)
        compact = compact_text(value)
        aliases.update(item for item in [value, normalized, compact] if item)

        tokens = SCRIPT_TOKEN_RE.findall(normalized)
        if len(tokens) > 1:
            aliases.update(tokens)
            aliases.add(" ".join(tokens))
            aliases.add("-".join(tokens))
            aliases.add("".join(tokens))

        ascii_tokens = ASCII_TOKEN_RE.findall(normalized)
        if len(ascii_tokens) > 1:
            aliases.add(" ".join(ascii_tokens))
            aliases.add("-".join(ascii_tokens))
            aliases.add("".join(ascii_tokens))

        if lazy_pinyin is not None and CHINESE_RE.search(value):
            pinyin_tokens = lazy_pinyin(value)
            if pinyin_tokens:
                aliases.add(" ".join(pinyin_tokens))
                aliases.add("-".join(pinyin_tokens))
                aliases.add("".join(pinyin_tokens))
    return {alias for alias in aliases if alias}


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = SEPARATOR_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_text(value: str) -> str:
    text = normalize_text(value)
    text = NON_SEARCH_CHAR_RE.sub("", text)
    return text.strip()


def alias_in_text(text: str, alias: str) -> bool:
    normalized_text = normalize_text(text)
    normalized_alias = normalize_text(alias)
    if not normalized_text or not normalized_alias:
        return False
    if is_ascii_alias(normalized_alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])", normalized_text) is not None
    return normalized_alias in normalized_text


def is_ascii_alias(value: str) -> bool:
    return bool(ASCII_RE.match(value))


def append_unique(target: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in target:
        target.append(value)


@lru_cache(maxsize=1)
def default_brand_catalog() -> BrandAliasCatalog:
    return BrandAliasCatalog.from_files()


def expand_alias_terms(values: Iterable[str]) -> list[str]:
    return default_brand_catalog().expand(values)


def brand_matches(query_brand: str, candidate_brand: str) -> bool:
    return default_brand_catalog().matches(query_brand, candidate_brand)


def is_known_brand_alias(value: str) -> bool:
    return default_brand_catalog().is_known_brand_alias(value)


def message_mentions_brand(message: str, brand: str) -> bool:
    return default_brand_catalog().contains_brand(message, brand)


def matched_brand_aliases(message: str, brand: str) -> list[str]:
    return default_brand_catalog().matched_aliases(message, brand)


def extract_brand_mentions(message: str) -> list[str]:
    return default_brand_catalog().extract_mentions(message)
