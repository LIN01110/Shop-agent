"""
VLM 结构化输出解析器。

升级点：让 VLM 直接输出 JSON，替代关键词正则匹配，获得更精确、更稳定的结构化理解。

设计原则：
- 向下兼容：新的 VLMStructuredProvider 实现 ImageUnderstandingProvider 协议
- 容错降级：JSON 解析失败时回退到原始文本解析
- 丰富字段：比旧版多 brand_confidence、visual_tags、style_tags 等字段
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from server.llm.vlm_client import VLMClient


@dataclass(frozen=True)
class StructuredImageUnderstandingResult:
    """VLM 对图片的结构化理解结果（Phase 1 升级版）。"""

    # 基础字段（与旧版兼容）
    raw_description: str = ""
    product_type_hint: str = ""
    color_hint: str = ""
    style_hint: str = ""
    fabric_hint: str = ""
    pattern_hint: str = ""
    occasion_hint: str = ""
    brand_hint: str = ""
    confidence: str = "medium"

    # Phase 1 新增字段
    brand_confidence: str = "low"           # 品牌识别置信度
    visual_tags: list[str] = field(default_factory=list)   # 视觉标签（如 ["V领", "修身", "碎花"]）
    style_tags: list[str] = field(default_factory=list)    # 风格标签（如 ["法式", "复古", "通勤"]）
    material_tags: list[str] = field(default_factory=list) # 材质标签（如 ["棉", "雪纺"]）
    color_tags: list[str] = field(default_factory=list)    # 颜色标签（细粒度，如 ["藏青", "酒红"]）
    scene_tags: list[str] = field(default_factory=list)    # 场景标签（如 ["约会", "通勤", "春夏"]）
    detail_mentions: list[str] = field(default_factory=list)  # 细节描述（如 ["蕾丝边", "珍珠扣"]）

    # 商品属性结构化
    attributes: dict = field(default_factory=dict)  # {"领口": "V领", "袖长": "短袖", ...}

    def to_legacy_result(self) -> dict:
        """转换为旧版字典格式，用于兼容现有代码。"""
        return {
            "raw": self.raw_description,
            "product_type": self.product_type_hint,
            "color": self.color_hint,
            "style": self.style_hint,
            "fabric": self.fabric_hint,
            "pattern": self.pattern_hint,
            "occasion": self.occasion_hint,
            "brand": self.brand_hint,
            "confidence": self.confidence,
            # 新增字段透传
            "visual_tags": self.visual_tags,
            "style_tags": self.style_tags,
            "material_tags": self.material_tags,
            "color_tags": self.color_tags,
            "scene_tags": self.scene_tags,
            "detail_mentions": self.detail_mentions,
            "attributes": self.attributes,
        }


class StructuredImageUnderstandingProvider(Protocol):
    def understand_structured(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> StructuredImageUnderstandingResult:
        ...


class VLMStructuredProvider:
    """
    基于 VLM 结构化 JSON 输出的图像理解实现。

    与旧版 VLMImageUnderstandingProvider 的区别：
    - Prompt 要求输出严格 JSON 格式
    - 解析器处理 JSON 而非正则匹配
    - 字段更丰富（visual_tags, style_tags, detail_mentions 等）
    - 品牌识别有独立置信度评估
    """

    DEFAULT_JSON_PROMPT = (
        "你是一个专业的商品视觉分析助手。请分析这张图片中的商品，"
        "按以下 JSON 格式输出结果（只输出 JSON，不要任何其他文字）：\n\n"
        "{\n"
        '  "product_type": "商品品类，如连衣裙、运动鞋、精华等",\n'
        '  "color": "主要颜色，用常见中文颜色词",\n'
        '  "colors": ["主要颜色", "次要颜色"],\n'
        '  "style": "款式描述，如V领修身碎花",\n'
        '  "visual_tags": ["领口类型", "袖长", "版型", "长度"],\n'
        '  "style_tags": ["风格标签，如法式、复古、通勤、甜美"],\n'
        '  "material": "面料/材质",\n'
        '  "material_tags": ["材质标签"],\n'
        '  "pattern": "图案/花纹",\n'
        '  "occasion": "适用场景",\n'
        '  "scene_tags": ["场景标签，如约会、通勤、春夏"],\n'
        '  "details": ["细节特征，如蕾丝边、珍珠扣、开叉"],\n'
        '  "brand": "识别到的品牌名，没有则空字符串",\n'
        '  "brand_confidence": "high/medium/low",\n'
        '  "attributes": {"领口": "V领", "袖长": "短袖", "版型": "修身"},\n'
        '  "overall_description": "整体描述，100字以内"\n'
        "}\n\n"
        "注意：\n"
        "1. 如果图片中没有商品（如风景、人物），product_type 填 null\n"
        "2. 品牌不确定时 brand 为空字符串，brand_confidence 为 low\n"
        "3. 只输出 JSON，不要 markdown 代码块标记"
    )

    def __init__(
        self,
        vlm_client: VLMClient,
        prompt_template: str = "",
        fallback_to_legacy: bool = True,
    ) -> None:
        self.vlm_client = vlm_client
        self.prompt_template = prompt_template or self.DEFAULT_JSON_PROMPT
        self.fallback_to_legacy = fallback_to_legacy

    def understand_structured(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> StructuredImageUnderstandingResult:
        raw = self.vlm_client.describe_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=self.prompt_template,
        )
        if not raw:
            return StructuredImageUnderstandingResult(
                raw_description="",
                confidence="low",
            )

        # 尝试解析 JSON
        parsed = self._parse_json_output(raw)
        if parsed:
            return self._build_from_json(parsed, raw)

        # JSON 解析失败，尝试提取 JSON 片段
        extracted = self._extract_json_fragment(raw)
        if extracted:
            return self._build_from_json(extracted, raw)

        # 完全失败，回退到旧版关键词提取（如果启用）
        if self.fallback_to_legacy:
            return self._fallback_legacy_parse(raw)

        return StructuredImageUnderstandingResult(
            raw_description=raw,
            confidence="low",
        )

    def _parse_json_output(self, raw: str) -> dict | None:
        """尝试直接解析整个输出为 JSON。"""
        text = raw.strip()
        # 去除 markdown 代码块标记
        if text.startswith("```"):
            lines = text.splitlines()
            # 去掉开头的 ```json 或 ```
            while lines and lines[0].startswith("```"):
                lines.pop(0)
            # 去掉结尾的 ```
            while lines and lines[-1].strip() == "```":
                lines.pop()
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _extract_json_fragment(self, raw: str) -> dict | None:
        """从文本中提取 JSON 片段。"""
        # 匹配 {...} 最外层结构
        match = re.search(r'\{[\s\S]*?\}', raw)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def _build_from_json(self, data: dict, raw: str) -> StructuredImageUnderstandingResult:
        """从解析的 JSON 构建结构化结果。"""

        # 颜色处理：优先用 colors 数组，否则用 color 字符串
        colors = self._safe_list(data.get("colors", []))
        color_hint = data.get("color", "")
        if not color_hint and colors:
            color_hint = colors[0]

        # 置信度评估
        confidence = self._estimate_confidence(data)
        brand_confidence = data.get("brand_confidence", "low")

        return StructuredImageUnderstandingResult(
            raw_description=raw,
            product_type_hint=data.get("product_type") or "",
            color_hint=color_hint,
            style_hint=data.get("style") or "",
            fabric_hint=data.get("material") or "",
            pattern_hint=data.get("pattern") or "",
            occasion_hint=data.get("occasion") or "",
            brand_hint=data.get("brand") or "",
            confidence=confidence,
            brand_confidence=brand_confidence,
            visual_tags=self._safe_list(data.get("visual_tags", [])),
            style_tags=self._safe_list(data.get("style_tags", [])),
            material_tags=self._safe_list(data.get("material_tags", [])),
            color_tags=colors if colors else ([color_hint] if color_hint else []),
            scene_tags=self._safe_list(data.get("scene_tags", [])),
            detail_mentions=self._safe_list(data.get("details", [])),
            attributes=data.get("attributes") or {},
        )

    def _fallback_legacy_parse(self, raw: str) -> StructuredImageUnderstandingResult:
        """回退到旧版关键词提取逻辑。"""
        from server.inputs.vlm_understanding import (
            _COLOR_KEYWORDS,
            _FABRIC_KEYWORDS,
            _OCCASION_KEYWORDS,
            _PATTERN_KEYWORDS,
            _PRODUCT_TYPE_KEYWORDS,
            _STYLE_KEYWORDS,
            _extract_brand,
            _extract_field,
        )

        lower = raw.lower()

        color = _extract_field(lower, _COLOR_KEYWORDS)
        product_type = _extract_field(lower, _PRODUCT_TYPE_KEYWORDS)
        fabric = _extract_field(lower, _FABRIC_KEYWORDS)
        pattern = _extract_field(lower, _PATTERN_KEYWORDS)
        style = _extract_field(lower, _STYLE_KEYWORDS)
        occasion = _extract_field(lower, _OCCASION_KEYWORDS)
        brand = _extract_brand(raw)

        # 估算置信度
        score = 0
        if len(raw) > 20:
            score += 1
        if color:
            score += 1
        if product_type:
            score += 2
        if style:
            score += 1
        confidence = "high" if score >= 4 else ("medium" if score >= 2 else "low")

        return StructuredImageUnderstandingResult(
            raw_description=raw,
            product_type_hint=product_type,
            color_hint=color,
            style_hint=style,
            fabric_hint=fabric,
            pattern_hint=pattern,
            occasion_hint=occasion,
            brand_hint=brand,
            confidence=confidence,
            color_tags=[color] if color else [],
        )

    @staticmethod
    def _safe_list(value) -> list[str]:
        """安全地将值转换为字符串列表。"""
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if v is not None and str(v).strip()]
        if isinstance(value, str):
            # 逗号分隔
            return [v.strip() for v in value.split(",") if v.strip()]
        return []

    def _estimate_confidence(self, data: dict) -> str:
        """基于 JSON 字段完整度评估置信度。"""
        score = 0
        critical_fields = ["product_type", "color", "style"]
        for field in critical_fields:
            if data.get(field):
                score += 2

        optional_fields = ["material", "pattern", "occasion", "brand"]
        for field in optional_fields:
            if data.get(field):
                score += 1

        list_fields = ["visual_tags", "style_tags", "details"]
        for field in list_fields:
            val = data.get(field, [])
            if isinstance(val, list) and len(val) > 0:
                score += 1

        if score >= 10:
            return "high"
        if score >= 5:
            return "medium"
        return "low"


class NoOpStructuredImageUnderstandingProvider:
    """空实现。"""

    def understand_structured(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> StructuredImageUnderstandingResult:
        return StructuredImageUnderstandingResult(raw_description="", confidence="low")
