"""
答辩重点 🟡（多模态输入）
任务：Day 1/2 场景 C（图片找货）。
核心：MultimodalInputProcessor 处理上传图片：
      1) 视觉匹配（Embedding / Signature）召回相似商品
      2) VLM 语义理解（可选）提取图片结构化特征
      3) 融合两者 → 生成 enriched image_summary
      4) yield image_analysis 事件 → 图片线索拼入查询
高频追问：
  - "图片上传后后端怎么处理？"
    → 视觉匹配召回 + VLM 语义理解 → 融合摘要 → SSE 推 image_analysis 事件
  - "视觉签名是什么？"
    → 用于快速相似度匹配的紧凑特征表示（12×12 RGB）
  - "VLM 在这里做什么？"
    → 对上传图片做细粒度语义理解（颜色、款式、品类、面料等），
      将自然语言理解结果与视觉匹配结果融合，生成更精准的检索 query
"""

from pathlib import Path

from server.inputs.base import ProcessedInput, TextProcessor
from server.inputs.image_similarity import ProductImageSimilarityIndex
from server.inputs.vlm_structured import (
    StructuredImageUnderstandingResult,
    VLMStructuredProvider,
)
from server.inputs.vlm_understanding import (
    ImageUnderstandingProvider,
    NoOpImageUnderstandingProvider,
)
from server.inputs.visual_embedding import ProductVisualEmbeddingIndex


class MultimodalInputProcessor:
    def __init__(
        self,
        product_data_path: Path,
        product_image_dir: Path,
        visual_embedding_index: ProductVisualEmbeddingIndex | None = None,
        image_understanding: ImageUnderstandingProvider | None = None,
        structured_vlm: VLMStructuredProvider | None = None,  # Phase 1 新增
    ) -> None:
        self.text_processor = TextProcessor()
        self.visual_index = ProductImageSimilarityIndex(product_data_path, product_image_dir)
        self.visual_embedding_index = visual_embedding_index
        self.image_understanding = image_understanding or NoOpImageUnderstandingProvider()
        self.structured_vlm = structured_vlm  # Phase 1 新增

    def process(
        self,
        raw: str,
        image_base64: str = "",
        image_bytes: bytes = b"",
        image_mime_type: str = "",
        image_filename: str = "",
    ) -> ProcessedInput:
        text = raw.strip()
        if not image_base64.strip() and not image_bytes:
            return self.text_processor.process(text)

        # Stage 1: 视觉匹配（Embedding / Signature）召回相似商品
        matches = self._match_image(
            image_base64,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )

        # Stage 2: VLM 语义理解（可选）提取图片结构化特征
        vlm_result = self._understand_image(image_bytes, image_mime_type)

        # Stage 3: 融合视觉匹配 + VLM 理解 → 生成 enriched image_summary
        if not matches:
            return self._build_fallback_input(text, vlm_result)

        return self._build_enriched_input(text, matches, vlm_result)

    def _match_image(self, image_base64: str, *, image_bytes: bytes, image_mime_type: str) -> list[dict]:
        if self.visual_embedding_index and self.visual_embedding_index.available:
            if image_bytes:
                matches = self.visual_embedding_index.match_image_bytes(
                    image_bytes,
                    image_mime_type=image_mime_type or "image/jpeg",
                    top_k=3,
                )
            else:
                matches = self.visual_embedding_index.match_base64_image(
                    image_base64,
                    image_mime_type=image_mime_type or "image/jpeg",
                    top_k=3,
                )
            if matches:
                return matches
        if image_bytes:
            return self.visual_index.match_image_bytes(image_bytes, top_k=3)
        return self.visual_index.match_base64_image(image_base64, top_k=3)

    def _understand_image(self, image_bytes: bytes, image_mime_type: str) -> dict:
        """调用 VLM 做语义理解，优先使用结构化 VLM（Phase 1），失败时回退到旧版。"""
        if not image_bytes:
            return {}

        # Phase 1: 尝试结构化 VLM（JSON 输出）
        if self.structured_vlm is not None:
            try:
                result = self.structured_vlm.understand_structured(
                    image_bytes, image_mime_type or "image/jpeg"
                )
                if result.confidence in ("high", "medium"):
                    return self._structured_result_to_dict(result)
            except Exception:
                pass  # 回退到旧版

        # 原有逻辑：旧版 VLM
        try:
            result = self.image_understanding.understand(image_bytes, image_mime_type or "image/jpeg")
            return {
                "raw": result.raw_description,
                "product_type": result.product_type_hint,
                "color": result.color_hint,
                "style": result.style_hint,
                "fabric": result.fabric_hint,
                "pattern": result.pattern_hint,
                "occasion": result.occasion_hint,
                "brand": result.brand_hint,
                "confidence": result.confidence,
            }
        except Exception:
            return {}

    def _structured_result_to_dict(self, result: StructuredImageUnderstandingResult) -> dict:
        """将结构化结果转换为旧版字典格式（兼容现有流程）。"""
        data = result.to_legacy_result()
        # 融合 visual_tags / style_tags 到描述中
        enriched = dict(data)
        tags = []
        if result.visual_tags:
            tags.extend(result.visual_tags)
        if result.style_tags:
            tags.extend(result.style_tags)
        if result.detail_mentions:
            tags.extend(result.detail_mentions)
        if tags:
            enriched["raw"] = f"{result.raw_description}\n视觉特征：{'，'.join(tags)}"
        return enriched

    def _build_fallback_input(self, text: str, vlm: dict) -> ProcessedInput:
        """无视觉匹配结果时的降级处理。"""
        fallback = text or "find visually similar products"

        # 如果有 VLM 理解结果，用它构建更有信息的摘要
        if vlm and vlm.get("raw"):
            hints = []
            if vlm.get("product_type"):
                hints.append(f"看起来是{vlm['product_type']}")
            if vlm.get("color"):
                hints.append(f"颜色是{vlm['color']}")
            if vlm.get("style"):
                hints.append(f"款式是{vlm['style']}")
            if vlm.get("fabric"):
                hints.append(f"面料是{vlm['fabric']}")

            summary = (
                "我看到了你上传的图片。"
                + "，".join(hints) + "。"
                if hints
                else "我看到了你上传的图片，但当前商品库里还没有足够可靠的视觉近邻。"
            )
            summary += "我会先结合你补充的文字需求继续找，避免硬说同款。"

            visual_query = f"{fallback}\nImage signal: {summary}"
            if vlm.get("product_type"):
                visual_query += f"\nPrioritize: {vlm['product_type']}"
            if vlm.get("color"):
                visual_query += f" {vlm['color']}"
            if vlm.get("style"):
                visual_query += f" {vlm['style']}"

            return ProcessedInput(
                text=visual_query,
                modality="image",
                image_summary=summary,
                visual_matches=[],
            )

        summary = "我看到了你上传的图片，但当前商品库里还没有足够可靠的视觉近邻。我会先结合你补充的文字需求继续找，避免硬说同款。"
        return ProcessedInput(
            text=f"{fallback}\nImage signal: {summary}",
            modality="image",
            image_summary=summary,
            visual_matches=[],
        )

    def _build_enriched_input(self, text: str, matches: list[dict], vlm: dict) -> ProcessedInput:
        """融合视觉匹配 + VLM 理解 → 生成 enriched image_summary。"""
        best = matches[0]
        product_types = ", ".join(best.get("product_type_names", []))
        match_source = best.get("visual_match_source", "signature")

        # 基础视觉匹配摘要
        summary_parts = [
            f"我先按图片做了视觉匹配，当前商品库里最接近的是「{best['name']}」，"
            f"品牌是 {best['brand']}，类目是 {best['category']}"
        ]
        if product_types:
            summary_parts[0] += f"，更像是 {product_types}"
        summary_parts[0] += (
            f"。相似度约 {best['similarity']:.2f}，"
            f"来源是 {format_visual_match_source(match_source)}。"
        )

        # 叠加 VLM 语义理解（如果有）
        vlm_enriched = False
        if vlm and vlm.get("raw") and vlm.get("confidence") in ("high", "medium"):
            vlm_parts = []
            if vlm.get("color"):
                vlm_parts.append(f"颜色是{vlm['color']}")
            if vlm.get("style"):
                vlm_parts.append(f"款式是{vlm['style']}")
            if vlm.get("fabric"):
                vlm_parts.append(f"面料是{vlm['fabric']}")
            if vlm.get("pattern"):
                vlm_parts.append(f"图案是{vlm['pattern']}")
            if vlm.get("occasion"):
                vlm_parts.append(f"适合{vlm['occasion']}")
            if vlm_parts:
                summary_parts.append("同时，AI 视觉分析显示：" + "，".join(vlm_parts) + "。")
                vlm_enriched = True

        summary_parts.append("我会优先按这个风格和品类给你推荐，同时不会把它说成百分百同款。")
        summary = "\n".join(summary_parts)

        # 构建检索 query：融合视觉匹配线索 + VLM 结构化特征
        user_text = text or "find the same or visually similar product from the image"
        visual_query_parts = [user_text, f"Image signal: {summary}"]

        # 优先推荐视觉匹配到的商品
        prioritize_parts = [
            f"Prioritize same or visually similar products: {best['name']} {best['brand']} {best['category']}"
        ]
        if product_types:
            prioritize_parts.append(" ".join(best.get("product_type_names", [])))

        # 追加 VLM 提取的结构化特征作为检索约束
        vlm_constraints = []
        if vlm and vlm.get("confidence") in ("high", "medium"):
            if vlm.get("product_type"):
                vlm_constraints.append(vlm["product_type"])
            if vlm.get("color"):
                vlm_constraints.append(vlm["color"])
            if vlm.get("style"):
                vlm_constraints.append(vlm["style"])
            if vlm.get("fabric"):
                vlm_constraints.append(vlm["fabric"])
            if vlm.get("pattern"):
                vlm_constraints.append(vlm["pattern"])
            if vlm.get("occasion"):
                vlm_constraints.append(vlm["occasion"])
            if vlm.get("brand"):
                vlm_constraints.append(vlm["brand"])

        if vlm_constraints:
            prioritize_parts.append(f"Additional visual cues: {' '.join(vlm_constraints)}")

        visual_query_parts.append(" ".join(prioritize_parts))
        visual_query = "\n".join(visual_query_parts)

        return ProcessedInput(
            text=visual_query,
            modality="image",
            image_summary=summary,
            visual_matches=matches,
        )


def format_visual_match_source(source: str) -> str:
    if source == "multimodal_embedding":
        return "多模态向量"
    return "图片视觉特征"
