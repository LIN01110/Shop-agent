# Shop-Agent Phase 1 + Phase 2 更新包 — 合并说明

## 文件清单

### 新增文件（直接复制到项目对应位置）

```
server/inputs/vlm_structured.py          →  server/inputs/vlm_structured.py
server/inputs/chinese_clip_provider.py   →  server/inputs/chinese_clip_provider.py
server/rag/milvus_store.py               →  server/rag/milvus_store.py
server/rag/bm25_engine.py                →  server/rag/bm25_engine.py
server/rag/cross_encoder.py              →  server/rag/cross_encoder.py
server/rag/feedback_loop.py              →  server/rag/feedback_loop.py
server/rag/hybrid_retriever.py           →  server/rag/hybrid_retriever.py
```

### 修改文件（需要合并到现有文件）

| 文件 | 操作 | 说明 |
|------|------|------|
| `server/config.py` | 追加配置项 | 在 `app_env` 上方插入 Phase 1/2 配置 |
| `server/app_container.py` | 追加导入 + 替换 create_store + 新增工厂函数 | 见下方详细说明 |
| `server/inputs/multimodal.py` | 追加导入 + 修改 __init__ + 修改 _understand_image | 见下方详细说明 |
| `server/requirements.txt` | 追加依赖 | 取消注释 Phase 1/2 依赖行 |

---

## 详细合并步骤

### 1. config.py — 追加配置项

在 `app_env: str = "development"` 的**上方**插入以下配置块：

```python
    # ── Phase 1: 多模态核心升级 ──
    use_structured_vlm: bool = False
    structured_vlm_prompt_template: str = ""
    structured_vlm_fallback: bool = True

    use_chinese_clip: bool = False
    chinese_clip_model: str = "OFA-Sys/chinese-clip-vit-base-patch16"
    chinese_clip_device: str = "auto"
    chinese_clip_cache_path: str = "server/runtime/chinese_clip_cache.sqlite3"

    use_milvus: bool = False
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_db_name: str = "default"
    milvus_collection_name: str = "products"
    milvus_dim: int = 512
    milvus_index_type: str = "AUTOINDEX"
    milvus_metric_type: str = "COSINE"
    milvus_fallback_enabled: bool = True

    # ── Phase 2: 检索引擎升级 ──
    use_bm25: bool = False
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_top_k: int = 200
    bm25_index_path: str = "server/runtime/bm25_index.json"

    use_cross_encoder: bool = False
    cross_encoder_model: str = "BAAI/bge-reranker-base"
    cross_encoder_device: str = "auto"
    cross_encoder_batch_size: int = 32
    cross_encoder_ce_weight: float = 0.7
    cross_encoder_bm25_weight: float = 0.15
    cross_encoder_vector_weight: float = 0.15

    use_hybrid_retrieval: bool = False
    hybrid_vector_top_k: int = 100
    hybrid_rerank_top_k: int = 20
    hybrid_fusion_method: str = "rrf"
    hybrid_rrf_k: int = 60
    hybrid_cache_ttl_seconds: float = 300.0
    hybrid_timeout_ms: float = 5000.0

    use_feedback_loop: bool = False
    feedback_db_path: str = "server/runtime/feedback.sqlite3"
    feedback_batch_size: int = 50
    feedback_flush_interval: float = 5.0
```

### 2. app_container.py — 合并修改

**步骤 A：追加导入语句**

在文件开头的 import 区域，添加：

```python
from server.inputs.chinese_clip_provider import CachedChineseClipProvider, ChineseClipProvider
from server.inputs.vlm_structured import VLMStructuredProvider
from server.rag.bm25_engine import BM25Engine
from server.rag.cross_encoder import CrossEncoderReranker, SimpleCrossEncoderReranker
from server.rag.feedback_loop import FeedbackLoop
from server.rag.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from server.rag.milvus_store import MilvusConfig, MilvusStore
```

**步骤 B：替换 `create_store()` 函数**

将整个 `create_store()` 函数替换为 `update/server/app_container.py.new` 中的版本（已包含 Milvus 优先逻辑）。

**步骤 C：在 `create_vlm_client()` 之后追加新工厂函数**

追加以下函数（详见 `update/server/app_container.py.new` 中的 Phase 1/2 新增部分）：

```python
def create_chinese_clip_provider(settings: Settings) -> CachedChineseClipProvider | None:
    ...

def create_structured_vlm_provider(settings: Settings, vlm_client: VLMClient) -> VLMStructuredProvider | None:
    ...

def create_bm25_engine(settings: Settings) -> BM25Engine | None:
    ...

def create_cross_encoder_reranker(settings: Settings) -> SimpleCrossEncoderReranker | CrossEncoderReranker | None:
    ...

def create_feedback_loop(settings: Settings) -> FeedbackLoop | None:
    ...

def create_hybrid_retriever(settings: Settings, store, bm25_engine, reranker, feedback_loop) -> HybridRetriever | None:
    ...
```

**步骤 D：修改 `create_input_processor()`**

在函数末尾，添加结构化 VLM 的创建和传入：

```python
    # Phase 1: 结构化 VLM（可选增强）
    structured_vlm = create_structured_vlm_provider(settings, vlm_client)
    if structured_vlm:
        logger.info("Structured VLM provider enabled for richer image understanding")

    return MultimodalInputProcessor(
        settings.product_data_file,
        settings.product_image_path,
        visual_embedding_index=visual_embedding_index,
        image_understanding=image_understanding,
        structured_vlm=structured_vlm,  # ← 新增参数
    )
```

### 3. multimodal.py — 合并修改

**步骤 A：追加导入**

```python
from server.inputs.vlm_structured import (
    StructuredImageUnderstandingResult,
    VLMStructuredProvider,
)
```

**步骤 B：修改 `__init__()`**

添加 `structured_vlm` 参数：

```python
def __init__(
    self,
    product_data_path: Path,
    product_image_dir: Path,
    visual_embedding_index: ProductVisualEmbeddingIndex | None = None,
    image_understanding: ImageUnderstandingProvider | None = None,
    structured_vlm: VLMStructuredProvider | None = None,  # ← 新增
) -> None:
    ...
    self.structured_vlm = structured_vlm  # ← 新增
```

**步骤 C：替换 `_understand_image()` 方法**

替换为 `update/server/inputs/multimodal.py.new` 中的版本（优先尝试结构化 VLM，失败回退旧版）。

### 4. requirements.txt — 取消注释

将 `update/server/requirements.txt.new` 中 Phase 1/2 的依赖行取消注释，然后追加到原文件末尾。

---

## 一键合并脚本（Windows PowerShell）

```powershell
# 在项目根目录执行
$updateDir = "update"

# 复制新增文件
Copy-Item "$updateDir\server\inputs\vlm_structured.py" "server\inputs\vlm_structured.py"
Copy-Item "$updateDir\server\inputs\chinese_clip_provider.py" "server\inputs\chinese_clip_provider.py"
Copy-Item "$updateDir\server\rag\milvus_store.py" "server\rag\milvus_store.py"
Copy-Item "$updateDir\server\rag\bm25_engine.py" "server\rag\bm25_engine.py"
Copy-Item "$updateDir\server\rag\cross_encoder.py" "server\rag\cross_encoder.py"
Copy-Item "$updateDir\server\rag\feedback_loop.py" "server\rag\feedback_loop.py"
Copy-Item "$updateDir\server\rag\hybrid_retriever.py" "server\rag\hybrid_retriever.py"

# 参考新文件手动合并 config.py / app_container.py / multimodal.py
Write-Host "新增文件已复制。请手动合并 config.py、app_container.py、multimodal.py 的修改。"
Write-Host "参考文件在 update/server/*.new 中。"
```

---

## 环境变量示例（.env 文件）

```env
# Phase 1
USE_STRUCTURED_VLM=false
USE_CHINESE_CLIP=false
USE_MILVUS=false

# Phase 2
USE_BM25=false
USE_CROSS_ENCODER=false
USE_HYBRID_RETRIEVAL=false
USE_FEEDBACK_LOOP=false
```

所有配置默认 `false`，不会破坏现有运行。按需逐个开启。
