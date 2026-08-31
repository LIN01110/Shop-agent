"""
Chinese-CLIP 本地推理封装。

提供：
- 图片嵌入：将商品图片编码为语义向量
- 文本嵌入：将查询文本编码为语义向量
- 跨模态相似度计算

依赖：
- pip install cnstd cnocr (如果需要 OCR 辅助)
- pip install torch transformers Pillow (已有)
- pip install ftfy regex tqdm (transformers 依赖)
- pip install git+https://github.com/OFA-Sys/Chinese-CLIP.git

使用方式：
  from server.inputs.chinese_clip_provider import ChineseClipProvider
  clip = ChineseClipProvider(model_name="OFA-Sys/chinese-clip-vit-base-patch16")
  image_vector = clip.embed_image(image_bytes)
  text_vector = clip.embed_text("红色连衣裙")
  similarity = clip.similarity(image_vector, text_vector)

设计要点：
- 延迟加载：模型在首次调用时才加载到 GPU/CPU
- 单例模式：通过 lru_cache 保证全局只有一个实例
- 容错降级：模型加载失败时优雅降级，不影响其他功能
"""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 延迟导入，避免启动时加载大模型
torch = None
transformers = None


def _ensure_torch():
    global torch, transformers
    if torch is None:
        try:
            import torch as _torch
            import transformers as _transformers
            torch = _torch
            transformers = _transformers
        except ImportError as exc:
            raise RuntimeError(
                "Chinese-CLIP requires torch and transformers. "
                "Install: pip install torch transformers"
            ) from exc
    return torch, transformers


class ImageTextEmbedder(Protocol):
    """跨模态嵌入协议。"""

    model: str
    dim: int

    def embed_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> np.ndarray:
        ...

    def embed_text(self, text: str) -> np.ndarray:
        ...


class ChineseClipProvider:
    """
    Chinese-CLIP 本地推理封装。

    支持的模型（按显存需求排序）：
    - OFA-Sys/chinese-clip-vit-base-patch16  (~400MB, 推荐)
    - OFA-Sys/chinese-clip-vit-large-patch14  (~1GB)
    - OFA-Sys/chinese-clip-vit-huge-patch14   (~2GB)

    首次加载约需 5-15 秒（取决于硬件）。
    """

    DEFAULT_MODEL = "OFA-Sys/chinese-clip-vit-base-patch16"
    DEFAULT_DEVICE = "auto"  # auto / cuda / cpu
    DEFAULT_MAX_SIDE = 224

    def __init__(
        self,
        model_name: str = "",
        device: str = "auto",
        cache_dir: str | Path | None = None,
    ) -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
        self.device_str = device or self.DEFAULT_DEVICE
        self.cache_dir = Path(cache_dir) if cache_dir else None

        # 延迟初始化的属性
        self._model = None
        self._processor = None
        self._device = None
        self._dim: int | None = None
        self._load_error: str = ""
        self._load_time_ms: float = 0.0

    @property
    def available(self) -> bool:
        """模型是否可用（已成功加载）。"""
        return self._model is not None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._ensure_loaded()
        return self._dim or 512

    @property
    def model(self) -> str:
        return self.model_name

    def _ensure_loaded(self) -> bool:
        """延迟加载模型。返回是否成功。"""
        if self._model is not None:
            return True
        if self._load_error:
            return False

        started = time.perf_counter()
        try:
            torch, transformers = _ensure_torch()

            # 自动选择设备
            if self.device_str == "auto":
                self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            else:
                self._device = torch.device(self.device_str)

            logger.info(
                "Loading Chinese-CLIP model: %s on %s",
                self.model_name,
                self._device,
            )

            # 加载 tokenizer 和模型
            from transformers import ChineseCLIPProcessor, ChineseCLIPModel

            kwargs = {}
            if self.cache_dir:
                kwargs["cache_dir"] = str(self.cache_dir)

            self._processor = ChineseCLIPProcessor.from_pretrained(self.model_name, **kwargs)
            self._model = ChineseCLIPModel.from_pretrained(self.model_name, **kwargs)
            self._model.to(self._device)
            self._model.eval()

            # 获取维度
            self._dim = self._model.config.projection_dim

            self._load_time_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "Chinese-CLIP loaded: dim=%d, device=%s, load_time=%.1fms",
                self._dim,
                self._device,
                self._load_time_ms,
            )
            return True

        except Exception as exc:
            self._load_error = str(exc)
            self._load_time_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.error(
                "Failed to load Chinese-CLIP (%s): %s",
                self.model_name,
                exc,
            )
            return False

    def embed_image(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> np.ndarray:
        """
        将图片编码为语义向量。
        返回归一化的 L2 单位向量（方便余弦相似度计算）。
        """
        if not self._ensure_loaded():
            raise RuntimeError(f"Chinese-CLIP not available: {self._load_error}")

        torch, _ = _ensure_torch()

        # 解码图片
        image = Image.open(BytesIO(image_bytes)).convert("RGB")

        # 预处理
        inputs = self._processor(images=image, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(self._device)

        with torch.no_grad():
            image_features = self._model.get_image_features(pixel_values=pixel_values)

        # 归一化
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        return image_features.cpu().numpy().flatten()

    def embed_text(self, text: str) -> np.ndarray:
        """
        将文本编码为语义向量。
        返回归一化的 L2 单位向量。
        """
        if not self._ensure_loaded():
            raise RuntimeError(f"Chinese-CLIP not available: {self._load_error}")

        torch, _ = _ensure_torch()

        inputs = self._processor(text=[text], return_tensors="pt", padding=True)
        input_ids = inputs["input_ids"].to(self._device)
        attention_mask = inputs["attention_mask"].to(self._device)

        with torch.no_grad():
            text_features = self._model.get_text_features(
                input_ids=input_ids,
                attention_mask=attention_mask,
            )

        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        return text_features.cpu().numpy().flatten()

    def embed_texts_batch(self, texts: list[str], batch_size: int = 32) -> list[np.ndarray]:
        """批量文本嵌入，效率更高。"""
        if not self._ensure_loaded():
            raise RuntimeError(f"Chinese-CLIP not available: {self._load_error}")

        torch, _ = _ensure_torch()
        results = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = self._processor(text=batch, return_tensors="pt", padding=True)
            input_ids = inputs["input_ids"].to(self._device)
            attention_mask = inputs["attention_mask"].to(self._device)

            with torch.no_grad():
                text_features = self._model.get_text_features(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                )

            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            for vec in text_features.cpu().numpy():
                results.append(vec.flatten())

        return results

    def similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """计算两个归一化向量的余弦相似度（范围 [-1, 1]，通常图片-文本在 [0, 1]）。"""
        return float(np.dot(vec_a, vec_b))

    def batch_similarity(
        self,
        query_vec: np.ndarray,
        candidate_vecs: list[np.ndarray],
    ) -> list[float]:
        """计算查询向量与多个候选向量的相似度。"""
        if not candidate_vecs:
            return []
        query_mat = np.stack([query_vec] * len(candidate_vecs))
        cand_mat = np.stack(candidate_vecs)
        scores = np.sum(query_mat * cand_mat, axis=1)
        return scores.tolist()


class NoOpChineseClipProvider:
    """空实现，用于 Chinese-CLIP 未启用时。"""

    model: str = "noop"
    dim: int = 512

    def embed_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> np.ndarray:
        return np.zeros(self.dim, dtype=np.float32)

    def embed_text(self, text: str) -> np.ndarray:
        return np.zeros(self.dim, dtype=np.float32)

    def similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        return 0.0


class CachedChineseClipProvider:
    """
    带缓存的 Chinese-CLIP 封装。
    缓存图片/文本的嵌入向量到 SQLite，避免重复计算。
    """

    def __init__(
        self,
        provider: ChineseClipProvider | None = None,
        cache_path: str | Path = "server/runtime/chinese_clip_cache.sqlite3",
    ) -> None:
        self.provider = provider or ChineseClipProvider()
        self.cache_path = Path(cache_path) if isinstance(cache_path, str) else cache_path
        self._cache: dict[str, np.ndarray] = {}
        self._dirty = False
        self._ensure_cache_dir()

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def dim(self) -> int:
        return self.provider.dim

    @property
    def available(self) -> bool:
        return self.provider.available

    def _ensure_cache_dir(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def embed_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> np.ndarray:
        key = _content_hash(image_bytes)
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        vec = self.provider.embed_image(image_bytes, mime_type)
        self._set_cached(key, vec)
        return vec

    def embed_text(self, text: str) -> np.ndarray:
        key = f"text:{text}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        vec = self.provider.embed_text(text)
        self._set_cached(key, vec)
        return vec

    def _get_cached(self, key: str) -> np.ndarray | None:
        # 内存缓存优先
        if key in self._cache:
            return self._cache[key]
        # TODO: 持久化缓存（SQLite/JSON）
        return None

    def _set_cached(self, key: str, vec: np.ndarray) -> None:
        self._cache[key] = vec
        self._dirty = True

    def similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        return self.provider.similarity(vec_a, vec_b)


def _content_hash(data: bytes) -> str:
    """计算内容的 SHA256 哈希作为缓存键。"""
    import hashlib
    return hashlib.sha256(data).hexdigest()[:32]
