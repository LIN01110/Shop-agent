"""
Milvus 向量库实现（Phase 1）。

替换 ChromaStore，提供更高性能、更大规模的向量检索能力。

设计要点：
- 实现 VectorStore 协议，保持接口兼容
- 支持多模态：文本嵌入 + 图片嵌入 + 混合检索
- 支持标量过滤：价格、品牌、类目等 metadata 过滤
- 自动索引创建：IVF_FLAT / HNSW 自适应选择
- 批量写入：支持高效的数据导入

依赖：
  pip install pymilvus>=2.4.0

使用方式（替换现有 create_store）：
  from server.rag.milvus_store import MilvusStore
  store = MilvusStore(
      uri="http://localhost:19530",
      collection_name="products",
      dim=512,
      text_embedder=text_embedder,      # 如 ArkEmbeddingFunction
      image_embedder=image_embedder,    # 如 ChineseClipProvider（可选）
  )
  store.add(documents)
  hits = store.query("红色连衣裙", top_k=10, filters=...)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from server.rag.documents import load_product_documents
from server.rag.types import VectorDocument, VectorSearchFilters, VectorStore

logger = logging.getLogger(__name__)

# 延迟导入 pymilvus，避免启动时强依赖
_pymilvus = None


def _ensure_pymilvus():
    global _pymilvus
    if _pymilvus is None:
        try:
            import pymilvus as _pm
            _pymilvus = _pm
        except ImportError as exc:
            raise RuntimeError(
                "Milvus store requires pymilvus. Install: pip install pymilvus>=2.4.0"
            ) from exc
    return _pymilvus


class TextEmbedder(Protocol):
    """文本嵌入协议。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_text(self, text: str) -> list[float]:
        ...


class ImageEmbedder(Protocol):
    """图片嵌入协议。"""

    def embed_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> np.ndarray:
        ...


@dataclass
class MilvusConfig:
    """Milvus 连接配置。"""

    uri: str = "http://localhost:19530"  # Milvus 服务地址
    token: str = ""                       # 认证 token（可选）
    db_name: str = "default"              # 数据库名
    collection_name: str = "products"     # 集合名
    dim: int = 512                        # 向量维度（需与 embedder 一致）
    # 索引参数
    index_type: str = "AUTOINDEX"         # AUTOINDEX / IVF_FLAT / HNSW / HNSW_SQ
    metric_type: str = "COSINE"           # COSINE / L2 / IP
    # SQ8 标量量化（显存优化）
    enable_sq8: bool = False              # 启用 SQ8
    sq_type: str = "SQ8"                  # SQ8 / SQ6 / SQ4U / BF16 / FP16
    sq_refine: bool = False               # 搜索时 refine 重排序
    sq_refine_type: str = "FP32"          # refine 精度
    # 写入参数
    batch_size: int = 100                 # 批量写入大小
    # 回退：如果 Milvus 不可用，是否回退到 LocalJsonVectorStore
    fallback_enabled: bool = True


class MilvusStore(VectorStore):
    """
    Milvus 向量存储实现。

    Schema 设计：
    - id: VARCHAR (主键)
    - text: VARCHAR (商品文本描述)
    - text_vector: FLOAT_VECTOR[dim] (文本嵌入)
    - image_vector: FLOAT_VECTOR[dim] (图片嵌入，可选)
    - category: VARCHAR (类目)
    - sub_category: VARCHAR (子类目)
    - brand: VARCHAR (品牌)
    - price: FLOAT (价格)
    - stock: INT (库存)
    - tags: ARRAY<VARCHAR> (标签)
    - metadata_json: VARCHAR (完整 metadata JSON)
    """

    def __init__(
        self,
        config: MilvusConfig | None = None,
        text_embedder: TextEmbedder | None = None,
        image_embedder: ImageEmbedder | None = None,
        fallback_store: VectorStore | None = None,
    ) -> None:
        self.config = config or MilvusConfig()
        self.text_embedder = text_embedder
        self.image_embedder = image_embedder
        self.fallback_store = fallback_store

        # 延迟初始化的属性
        self._client = None
        self._collection = None
        self._available = False
        self._last_error = ""
        self._ensure_connected()

    def _ensure_connected(self) -> bool:
        """建立 Milvus 连接。返回是否成功。"""
        if self._available:
            return True
        if self._last_error:
            return False

        try:
            pymilvus = _ensure_pymilvus()

            # 连接
            connections_kwargs = {"uri": self.config.uri}
            if self.config.token:
                connections_kwargs["token"] = self.config.token

            self._client = pymilvus.MilvusClient(**connections_kwargs)

            # 检查集合是否存在
            if self._client.has_collection(self.config.collection_name):
                self._client.load_collection(self.config.collection_name)
                self._available = True
                logger.info(
                    "Milvus connected: %s, collection=%s",
                    self.config.uri,
                    self.config.collection_name,
                )
            else:
                self._create_collection()

            return self._available

        except Exception as exc:
            self._last_error = str(exc)
            logger.error(
                "Milvus connection failed (%s): %s",
                self.config.uri,
                exc,
            )
            return False

    def _create_collection(self) -> None:
        """创建集合和索引。"""
        pymilvus = _ensure_pymilvus()
        cfg = self.config

        # Schema 定义
        schema = pymilvus.MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=True,
        )
        schema.add_field("id", datatype=pymilvus.DataType.VARCHAR, max_length=128, is_primary=True)
        schema.add_field("text_vector", datatype=pymilvus.DataType.FLOAT_VECTOR, dim=cfg.dim)
        schema.add_field("text", datatype=pymilvus.DataType.VARCHAR, max_length=8192)
        schema.add_field("category", datatype=pymilvus.DataType.VARCHAR, max_length=128)
        schema.add_field("sub_category", datatype=pymilvus.DataType.VARCHAR, max_length=128)
        schema.add_field("brand", datatype=pymilvus.DataType.VARCHAR, max_length=128)
        schema.add_field("price", datatype=pymilvus.DataType.FLOAT)
        schema.add_field("stock", datatype=pymilvus.DataType.INT64)
        schema.add_field("tags_json", datatype=pymilvus.DataType.VARCHAR, max_length=4096)
        schema.add_field("metadata_json", datatype=pymilvus.DataType.VARCHAR, max_length=16384)

        # 图片向量字段（可选，仅在启用图片嵌入时创建）
        if self.image_embedder is not None:
            schema.add_field(
                "image_vector",
                datatype=pymilvus.DataType.FLOAT_VECTOR,
                dim=cfg.dim,
            )

        # 索引参数
        index_params = self._client.prepare_index_params()

        # 根据配置选择索引类型：HNSW + SQ8 或普通 HNSW
        if cfg.index_type == "AUTOINDEX":
            index_params.add_index(
                field_name="text_vector",
                index_type="AUTOINDEX",
                metric_type=cfg.metric_type,
            )
            if self.image_embedder is not None:
                index_params.add_index(
                    field_name="image_vector",
                    index_type="AUTOINDEX",
                    metric_type=cfg.metric_type,
                )
        elif cfg.index_type in ("HNSW", "HNSW_SQ"):
            # HNSW 基础参数
            hnsw_params = {"M": 16, "efConstruction": 200}
            # 如果启用 SQ8，使用 HNSW_SQ 索引类型并添加量化参数
            if cfg.enable_sq8 or cfg.index_type == "HNSW_SQ":
                index_type = "HNSW_SQ"
                hnsw_params["sq_type"] = cfg.sq_type
                if cfg.sq_refine:
                    hnsw_params["refine"] = True
                    hnsw_params["refine_type"] = cfg.sq_refine_type
            else:
                index_type = "HNSW"

            index_params.add_index(
                field_name="text_vector",
                index_type=index_type,
                metric_type=cfg.metric_type,
                params=hnsw_params,
            )
            if self.image_embedder is not None:
                index_params.add_index(
                    field_name="image_vector",
                    index_type=index_type,
                    metric_type=cfg.metric_type,
                    params=dict(hnsw_params),  # 复制一份避免引用问题
                )
        else:  # IVF_FLAT
            index_params.add_index(
                field_name="text_vector",
                index_type="IVF_FLAT",
                metric_type=cfg.metric_type,
                params={"nlist": 128},
            )

        self._client.create_collection(
            collection_name=cfg.collection_name,
            schema=schema,
            index_params=index_params,
        )
        self._client.load_collection(cfg.collection_name)
        self._available = True
        logger.info(
            "Milvus collection created: %s (dim=%d, index=%s)",
            cfg.collection_name,
            cfg.dim,
            cfg.index_type,
        )

    # ------------------------------------------------------------------
    # VectorStore 协议实现
    # ------------------------------------------------------------------

    def add(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return

        if not self._ensure_connected():
            if self.fallback_store:
                logger.warning("Milvus unavailable, falling back to %s", type(self.fallback_store).__name__)
                self.fallback_store.add(documents)
            return

        # 分批处理
        for i in range(0, len(documents), self.config.batch_size):
            batch = documents[i : i + self.config.batch_size]
            self._add_batch(batch)

    def _add_batch(self, documents: list[VectorDocument]) -> None:
        """批量写入。"""
        texts = [doc.text for doc in documents]

        # 文本嵌入
        if self.text_embedder:
            text_vectors = self.text_embedder.embed_texts(texts)
        else:
            text_vectors = [[] for _ in texts]

        # 图片嵌入（如果启用）
        image_vectors = None
        if self.image_embedder:
            image_vectors = self._embed_product_images(documents)

        # 构建插入数据
        entities = []
        for idx, doc in enumerate(documents):
            meta = doc.metadata
            entity = {
                "id": doc.id,
                "text_vector": text_vectors[idx],
                "text": doc.text[:8000],  # 截断避免超限
                "category": str(meta.get("category", ""))[:128],
                "sub_category": str(meta.get("sub_category", ""))[:128],
                "brand": str(meta.get("brand", ""))[:128],
                "price": float(meta.get("price", 0) or 0),
                "stock": int(meta.get("stock", 0) or 0),
                "tags_json": self._serialize_tags(meta.get("tags", [])),
                "metadata_json": self._serialize_metadata(meta),
            }
            if image_vectors is not None:
                entity["image_vector"] = image_vectors[idx].tolist() if image_vectors[idx] is not None else [0.0] * self.config.dim

            entities.append(entity)

        self._client.insert(
            collection_name=self.config.collection_name,
            data=entities,
        )
        logger.debug("Inserted %d documents into Milvus", len(entities))

    def _embed_product_images(self, documents: list[VectorDocument]) -> list[np.ndarray | None]:
        """为商品文档嵌入图片。"""
        if not self.image_embedder:
            return [None] * len(documents)

        results = []
        for doc in documents:
            image_url = doc.metadata.get("image_url", "")
            if not image_url:
                results.append(None)
                continue

            # 从本地加载图片
            image_path = self._resolve_image_path(image_url)
            if not image_path or not image_path.exists():
                results.append(None)
                continue

            try:
                vec = self.image_embedder.embed_image(image_path.read_bytes())
                results.append(vec)
            except Exception as exc:
                logger.warning("Failed to embed image for %s: %s", doc.id, exc)
                results.append(None)

        return results

    def _resolve_image_path(self, image_url: str) -> Path | None:
        """解析图片路径。"""
        # 相对路径：如 "1_美妆护肤/images/p_beauty_001_live.jpg"
        # 提取文件名
        from pathlib import Path as _Path
        parts = image_url.replace("\\", "/").split("/")
        if parts:
            return _Path("data/product_images") / parts[-1]
        return None

    @staticmethod
    def _serialize_tags(tags) -> str:
        if isinstance(tags, list):
            return ",".join(str(t) for t in tags)[:4000]
        return str(tags)[:4000]

    @staticmethod
    def _serialize_metadata(meta: dict) -> str:
        import json
        try:
            return json.dumps(meta, ensure_ascii=False, separators=(",", ":"))[:16000]
        except (TypeError, ValueError):
            return "{}"

    def query(
        self,
        query: str,
        top_k: int = 5,
        filters: VectorSearchFilters | None = None,
        query_image_bytes: bytes | None = None,
        image_weight: float = 0.5,
    ) -> list[dict]:
        """
        检索接口。

        支持三种模式：
        1. 纯文本检索：query_image_bytes=None
        2. 纯图片检索：query="", query_image_bytes=图片
        3. 混合检索：query + query_image_bytes（加权融合）
        """
        if not self._ensure_connected():
            if self.fallback_store:
                return self.fallback_store.query(query, top_k, filters)
            return []

        # 构建过滤表达式
        expr = self._build_filter_expr(filters)

        # 文本向量
        text_vector = None
        if query and self.text_embedder:
            text_vector = self.text_embedder.embed_text(query)

        # 图片向量
        image_vector = None
        if query_image_bytes and self.image_embedder:
            try:
                image_vector = self.image_embedder.embed_image(query_image_bytes).tolist()
            except Exception as exc:
                logger.warning("Failed to embed query image: %s", exc)

        # 执行检索
        if text_vector and image_vector:
            # 混合检索：加权融合
            return self._hybrid_search(text_vector, image_vector, top_k, expr, image_weight)
        elif image_vector:
            # 纯图片检索
            return self._image_search(image_vector, top_k, expr)
        else:
            # 纯文本检索
            return self._text_search(text_vector or [], top_k, expr)

    def _text_search(self, vector: list[float], top_k: int, expr: str) -> list[dict]:
        """文本向量检索。"""
        search_params = {"metric_type": self.config.metric_type, "params": {}}
        if self.config.index_type == "HNSW":
            search_params["params"]["ef"] = max(top_k * 2, 64)

        results = self._client.search(
            collection_name=self.config.collection_name,
            data=[vector],
            anns_field="text_vector",
            param=search_params,
            limit=top_k,
            expr=expr or None,
            output_fields=["id", "text", "category", "sub_category", "brand", "price", "stock", "metadata_json"],
        )
        return self._parse_search_results(results)

    def _image_search(self, vector: list[float], top_k: int, expr: str) -> list[dict]:
        """图片向量检索。"""
        search_params = {"metric_type": self.config.metric_type, "params": {}}

        results = self._client.search(
            collection_name=self.config.collection_name,
            data=[vector],
            anns_field="image_vector",
            param=search_params,
            limit=top_k,
            expr=expr or None,
            output_fields=["id", "text", "category", "sub_category", "brand", "price", "stock", "metadata_json"],
        )
        return self._parse_search_results(results)

    def _hybrid_search(
        self,
        text_vector: list[float],
        image_vector: list[float],
        top_k: int,
        expr: str,
        image_weight: float,
    ) -> list[dict]:
        """混合检索：加权融合文本和图片相似度。"""
        # 扩大候选池
        candidate_k = max(top_k * 5, 50)

        # 分别检索
        text_results = self._text_search(text_vector, candidate_k, expr)
        image_results = self._image_search(image_vector, candidate_k, expr)

        # 加权融合
        merged = {}
        for hit in text_results:
            hit_id = hit["id"]
            merged[hit_id] = {
                "hit": hit,
                "text_score": hit.get("score", 0.0),
                "image_score": 0.0,
            }

        for hit in image_results:
            hit_id = hit["id"]
            if hit_id in merged:
                merged[hit_id]["image_score"] = hit.get("score", 0.0)
            else:
                merged[hit_id] = {
                    "hit": hit,
                    "text_score": 0.0,
                    "image_score": hit.get("score", 0.0),
                }

        # 加权排序
        text_weight = 1.0 - image_weight
        scored = []
        for item in merged.values():
            fused_score = text_weight * item["text_score"] + image_weight * item["image_score"]
            hit = dict(item["hit"])
            hit["score"] = fused_score
            hit["text_score"] = item["text_score"]
            hit["image_score"] = item["image_score"]
            scored.append(hit)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def _parse_search_results(self, results) -> list[dict]:
        """解析 Milvus 搜索结果。"""
        hits = []
        import json
        for result_group in results:
            for hit in result_group:
                entity = hit.get("entity", {})
                metadata = {}
                meta_json = entity.get("metadata_json", "{}")
                try:
                    metadata = json.loads(meta_json)
                except json.JSONDecodeError:
                    pass

                hits.append({
                    "id": entity.get("id", ""),
                    "text": entity.get("text", ""),
                    "metadata": metadata,
                    "score": hit.get("distance", 0.0),
                })
        return hits

    def _build_filter_expr(self, filters: VectorSearchFilters | None) -> str:
        """构建 Milvus 标量过滤表达式。"""
        if not filters:
            return ""

        conditions = []
        if filters.categories:
            cats = [f'"{c}"' for c in filters.categories]
            conditions.append(f"category in [{','.join(cats)}]")
        if filters.product_types:
            # product_type 映射到 sub_category
            pts = [f'"{p}"' for p in filters.product_types]
            conditions.append(f"sub_category in [{','.join(pts)}]")

        return " and ".join(conditions) if conditions else ""

    def delete(self, ids: list[str]) -> None:
        if not self._ensure_connected():
            if self.fallback_store:
                self.fallback_store.delete(ids)
            return

        self._client.delete(
            collection_name=self.config.collection_name,
            ids=ids,
        )

    def count(self) -> int:
        if not self._ensure_connected():
            if self.fallback_store:
                return self.fallback_store.count()
            return 0
        return self._client.get_collection_stats(self.config.collection_name)["row_count"]

    # ------------------------------------------------------------------
    # 额外工具方法
    # ------------------------------------------------------------------

    def rebuild_index(self) -> None:
        """重建索引（数据量变化大后调用）。"""
        if not self._ensure_connected():
            return
        self._client.release_collection(self.config.collection_name)
        # Milvus 2.4+ 自动索引不需要手动重建
        self._client.load_collection(self.config.collection_name)
        logger.info("Milvus index reloaded: %s", self.config.collection_name)

    def get_stats(self) -> dict:
        """获取集合统计信息。"""
        if not self._ensure_connected():
            return {"available": False, "error": self._last_error}

        stats = self._client.get_collection_stats(self.config.collection_name)
        return {
            "available": True,
            "collection": self.config.collection_name,
            "row_count": stats.get("row_count", 0),
            "dim": self.config.dim,
            "index_type": self.config.index_type,
        }
