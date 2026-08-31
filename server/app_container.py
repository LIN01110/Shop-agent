"""
Purpose: 依赖容器/工厂：create_app() 的依赖注入，创建和缓存 LLM 客户端、向量存储、Session 存储等核心组件。
"""

import logging
from functools import lru_cache
from pathlib import Path

from server.agent.default_handlers import build_default_workflow
from server.agent.orchestrator import Orchestrator
from server.agent.query_feedback import QueryFeedbackStore
from server.agent.scenario_classifier import EmbeddingScenarioClassifier, HybridScenarioClassifier
from server.agent.scenarios import load_scenario_catalog
from server.agent.semantic_llm import SemanticPlanner
from server.agent.tracing import InMemoryTraceStore
from server.commerce.facts import CommerceFactProvider, LocalMockCommerceProvider, configure_fact_provider
from server.config import Settings, get_settings
from server.inputs.chinese_clip_provider import CachedChineseClipProvider, ChineseClipProvider
from server.inputs.multimodal import MultimodalInputProcessor
from server.inputs.visual_embedding import ProductVisualEmbeddingIndex
from server.inputs.vlm_structured import VLMStructuredProvider
from server.inputs.vlm_understanding import (
    ImageUnderstandingProvider,
    NoOpImageUnderstandingProvider,
    VLMImageUnderstandingProvider,
)
from server.llm.ark_client import ArkChatClient, LLMClient
from server.llm.vlm_client import ArkVLMClient, NoOpVLMClient, VLMClient
from server.rag.bm25_engine import BM25Engine
from server.rag.cross_encoder import CrossEncoderReranker, SimpleCrossEncoderReranker
from server.rag.embedding_cache import EmbeddingCache
from server.rag.embeddings import ArkEmbeddingFunction
from server.rag.feedback_loop import FeedbackLoop
from server.rag.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
from server.rag.milvus_store import MilvusConfig, MilvusStore
from server.rag.vector_store import (
    ArkMultimodalEmbeddingFunction,
    ChromaStore,
    LocalJsonVectorStore,
    build_chroma_embedding_function,
    load_product_documents,
)
from server.session.state import PersistentSessionStore, RedisSessionStore, SQLiteSessionStore, SessionStore
from server.tools.cart import CartTool
from server.tools.product_compare import ProductCompareTool
from server.tools.product_search import ProductSearchTool
from server.tools.registry import ToolRegistry
from server.commerce.facts import get_commerce_gateway


logger = logging.getLogger(__name__)


@lru_cache
def get_orchestrator() -> Orchestrator:
    settings = get_settings()
    configure_fact_provider(create_commerce_fact_provider(settings))
    commerce_gateway = get_commerce_gateway()
    store = create_store(settings)
    llm_client = create_llm_client(settings)
    lite_llm_client = create_lite_llm_client(settings)
    registry = ToolRegistry()
    search_tool = ProductSearchTool(
        store,
        public_base_url=settings.public_base_url,
        commerce_gateway=commerce_gateway,
        candidate_multiplier=settings.retrieval_candidate_multiplier,
        min_candidate_pool=settings.retrieval_min_candidate_pool,
    )
    registry.register(search_tool)
    registry.register(ProductCompareTool(search_tool))
    registry.register(CartTool())
    semantic_client = llm_client if settings.use_semantic_llm else None
    scenario_catalog = create_scenario_catalog(settings)
    return Orchestrator(
        registry=registry,
        sessions=create_session_store(settings),
        llm_client=llm_client,
        lite_llm_client=lite_llm_client,
        workflow=build_default_workflow(scenario_catalog),
        semantic_planner=SemanticPlanner(
            semantic_client,
            timeout_seconds=settings.semantic_llm_budget_seconds,
        ),
        trace_store=InMemoryTraceStore(max_items=settings.trace_max_items),
        query_feedback_store=create_query_feedback_store(settings),
        input_processor=create_input_processor(settings),
        recommendation_llm_budget_seconds=settings.recommendation_llm_budget_seconds,
        retrieval_timeout_seconds=settings.retrieval_timeout_seconds,
    )


def create_query_feedback_store(settings: Settings) -> QueryFeedbackStore | None:
    if not settings.enable_query_feedback_log:
        return None
    return QueryFeedbackStore(settings.query_feedback_log_file, enabled=True)


def create_scenario_catalog(settings: Settings):
    classifier = create_scenario_classifier(settings)
    return load_scenario_catalog(str(settings.scenario_bundle_file), classifier=classifier)


def create_scenario_classifier(settings: Settings) -> HybridScenarioClassifier:
    if not settings.use_scenario_embedding:
        return HybridScenarioClassifier()
    if not settings.ark_api_key:
        logger.warning("USE_SCENARIO_EMBEDDING=true but ARK_API_KEY is empty; using rule scenario routing only.")
        return HybridScenarioClassifier()
    embedder = ArkEmbeddingFunction(
        api_key=settings.ark_api_key,
        base_url=settings.ark_base_url,
        model=settings.ark_embedding_model,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=min(max(1, settings.embedding_batch_size), 32),
    )
    return HybridScenarioClassifier(
        embedding_classifier=EmbeddingScenarioClassifier(
            embedder,
            min_similarity=settings.scenario_embedding_min_similarity,
            margin=settings.scenario_embedding_margin,
        )
    )


def create_commerce_fact_provider(settings: Settings) -> CommerceFactProvider:
    backend = settings.normalized_commerce_fact_backend
    if backend in {"mock", "local_mock", "local"}:
        return LocalMockCommerceProvider(settings.commerce_mock_data_file)
    if backend == "mysql":
        from server.commerce.mysql_bridge import create_mysql_backed_commerce_gateway
        gateway = create_mysql_backed_commerce_gateway(settings.commerce_mock_data_path)
        return GatewayFactProvider(gateway)
    raise ValueError(f"Unsupported COMMERCE_FACT_BACKEND: {settings.commerce_fact_backend}")


def create_session_store(settings: Settings) -> PersistentSessionStore:
    backend = settings.normalized_session_backend
    if backend in {"memory", "inmemory", "in-memory"}:
        return SessionStore(
            max_items=settings.session_max_items,
            ttl_seconds=settings.session_ttl_seconds,
        )
    if backend in {"sqlite", "sqlite3", "db"}:
        return SQLiteSessionStore(
            settings.session_db_file,
            max_items=settings.session_max_items,
            ttl_seconds=settings.session_ttl_seconds,
        )
    if backend == "redis":
        return RedisSessionStore(
            settings.session_redis_url,
            max_items=settings.session_max_items,
            ttl_seconds=settings.session_ttl_seconds,
        )
    raise ValueError(f"Unsupported SESSION_BACKEND: {settings.session_backend}")


# ═══════════════════════════════════════════════════════════════════════
# Phase 1 + Phase 2: 新增 / 升级的工厂函数
# ═══════════════════════════════════════════════════════════════════════

def create_store(settings: Settings):
    """创建向量存储（支持 Milvus / Chroma / LocalJson）。"""
    # ── Phase 1: 优先使用 Milvus ──
    if settings.use_milvus:
        try:
            text_embedder = None
            if settings.use_ark_embedding and settings.ark_api_key:
                text_embedder = ArkEmbeddingFunction(
                    api_key=settings.ark_api_key,
                    base_url=settings.ark_base_url,
                    model=settings.ark_embedding_model,
                    timeout_seconds=settings.embedding_timeout_seconds,
                    batch_size=settings.embedding_batch_size,
                )
            image_embedder = None
            if settings.use_chinese_clip:
                clip = create_chinese_clip_provider(settings)
                if clip and clip.available:
                    image_embedder = clip

            milvus_cfg = MilvusConfig(
                uri=settings.milvus_uri,
                token=settings.milvus_token,
                db_name=settings.milvus_db_name,
                collection_name=settings.milvus_collection_name,
                dim=settings.milvus_dim,
                index_type=settings.milvus_index_type,
                metric_type=settings.milvus_metric_type,
                enable_sq8=settings.milvus_enable_sq8,
                sq_type=settings.milvus_sq_type,
                sq_refine=settings.milvus_sq_refine,
                sq_refine_type=settings.milvus_sq_refine_type,
                fallback_enabled=settings.milvus_fallback_enabled,
            )
            store = MilvusStore(
                config=milvus_cfg,
                text_embedder=text_embedder,
                image_embedder=image_embedder,
            )
            if store.count() == 0:
                store.add(load_product_documents(settings.product_data_file))
            logger.info("Milvus store initialized: %s", settings.milvus_collection_name)
            return store
        except Exception as exc:
            logger.warning("Milvus initialization failed: %s", exc)
            if not settings.milvus_fallback_enabled:
                raise

    # ── 原有逻辑：Chroma / LocalJson ──
    if settings.use_chroma:
        try:
            embedding_function, collection_name = build_chroma_embedding_function(
                use_ark_embedding=settings.use_ark_embedding,
                embedding_api=settings.ark_embedding_api,
                api_key=settings.ark_api_key,
                base_url=settings.ark_base_url,
                model=settings.ark_embedding_model,
                timeout_seconds=settings.embedding_timeout_seconds,
                batch_size=settings.embedding_batch_size,
                collection_name=settings.chroma_collection_name,
            )
            if settings.use_ark_embedding and not settings.ark_api_key:
                logger.warning("USE_ARK_EMBEDDING=true but ARK_API_KEY is empty; using local hashing embedding.")
            store = ChromaStore(
                settings.chroma_path,
                collection_name=collection_name,
                embedding_function=embedding_function,
            )
            if store.count() == 0:
                store.add(load_product_documents(settings.product_data_file))
            return store
        except RuntimeError as exc:
            logger.warning("Chroma unavailable, falling back to local JSON search: %s", exc)
    return LocalJsonVectorStore(settings.product_data_file)


def create_llm_client(settings: Settings) -> LLMClient | None:
    if not settings.use_llm:
        return None
    if not settings.ark_api_key:
        logger.warning("USE_LLM=true but ARK_API_KEY is empty; falling back to template answers.")
        return None
    return ArkChatClient(
        api_key=settings.ark_api_key,
        base_url=settings.ark_base_url,
        model=settings.ark_model,
        timeout_seconds=settings.llm_timeout_seconds,
        retry_attempts=settings.llm_retry_attempts,
        circuit_breaker_failures=settings.llm_circuit_breaker_failures,
        circuit_breaker_reset_seconds=settings.llm_circuit_breaker_reset_seconds,
        max_tokens=settings.llm_max_tokens if settings.llm_max_tokens > 0 else 0,
    )


def create_lite_llm_client(settings: Settings) -> LLMClient | None:
    """创建小模型 LLM 客户端（用于简单查询降级）。"""
    if not settings.enable_lite_model_fallback:
        return None
    # 小模型配置：优先使用独立的 key/url/model，否则复用大模型配置
    api_key = settings.ark_lite_api_key or settings.ark_api_key
    base_url = settings.ark_lite_base_url or settings.ark_base_url
    model = settings.ark_lite_model
    if not api_key or not model:
        logger.warning("Lite model fallback enabled but ARK_LITE_MODEL not configured.")
        return None
    return ArkChatClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=min(settings.llm_timeout_seconds, 15.0),  # 小模型超时更短
        retry_attempts=settings.llm_retry_attempts,
        circuit_breaker_failures=settings.llm_circuit_breaker_failures,
        circuit_breaker_reset_seconds=settings.llm_circuit_breaker_reset_seconds,
        max_tokens=settings.llm_answer_max_tokens if settings.llm_answer_max_tokens > 0 else 512,
    )


def create_vlm_client(settings: Settings) -> VLMClient:
    """创建 VLM 客户端。未启用或配置缺失时返回 NoOp。"""
    if not settings.use_vlm:
        return NoOpVLMClient()
    if not settings.ark_api_key:
        logger.warning("USE_VLM=true but ARK_API_KEY is empty; VLM disabled.")
        return NoOpVLMClient()
    model = settings.vlm_model_name
    if not model:
        logger.warning("USE_VLM=true but VLM_MODEL is empty; VLM disabled.")
        return NoOpVLMClient()
    return ArkVLMClient(
        api_key=settings.ark_api_key,
        base_url=settings.ark_base_url,
        model=model,
        timeout_seconds=settings.vlm_timeout_seconds,
        retry_attempts=settings.vlm_retry_attempts,
        circuit_breaker_failures=settings.vlm_circuit_breaker_failures,
        circuit_breaker_reset_seconds=settings.vlm_circuit_breaker_reset_seconds,
        max_tokens=settings.vlm_max_tokens,
    )


def create_image_understanding_provider(
    settings: Settings,
    vlm_client: VLMClient,
) -> ImageUnderstandingProvider:
    """创建图像理解提供者。VLM 未启用时返回 NoOp。"""
    if not settings.use_vlm or isinstance(vlm_client, NoOpVLMClient):
        return NoOpImageUnderstandingProvider()
    return VLMImageUnderstandingProvider(
        vlm_client=vlm_client,
        prompt_template=settings.vlm_prompt_template,
    )


# ── Phase 1 新增 ──

def create_chinese_clip_provider(settings: Settings) -> CachedChineseClipProvider | None:
    """创建 Chinese-CLIP 提供者（Phase 1）。"""
    if not settings.use_chinese_clip:
        return None
    provider = ChineseClipProvider(
        model_name=settings.chinese_clip_model,
        device=settings.chinese_clip_device,
    )
    return CachedChineseClipProvider(
        provider=provider,
        cache_path=settings.chinese_clip_cache_path,
    )


def create_structured_vlm_provider(settings: Settings, vlm_client: VLMClient) -> VLMStructuredProvider | None:
    """创建结构化 VLM 提供者（Phase 1）。"""
    if not settings.use_structured_vlm:
        return None
    if isinstance(vlm_client, NoOpVLMClient):
        logger.warning("Structured VLM requires VLM client; using NoOp fallback.")
        return None
    return VLMStructuredProvider(
        vlm_client=vlm_client,
        prompt_template=settings.structured_vlm_prompt_template,
        fallback_to_legacy=settings.structured_vlm_fallback,
    )


# ── Phase 2 新增 ──

def create_bm25_engine(settings: Settings) -> BM25Engine | None:
    """创建 BM25 引擎（Phase 2）。"""
    if not settings.use_bm25:
        return None
    documents = load_product_documents(settings.product_data_file)
    from server.rag.bm25_engine import BM25Config
    engine = BM25Engine(
        documents=documents,
        config=BM25Config(k1=settings.bm25_k1, b=settings.bm25_b),
    )
    # 尝试加载已有索引
    bm25_path = settings.bm25_index_path
    if bm25_path and Path(bm25_path).exists():
        try:
            engine = BM25Engine.load(bm25_path)
            logger.info("BM25 index loaded from %s", bm25_path)
        except Exception as exc:
            logger.warning("Failed to load BM25 index: %s", exc)
    return engine


def create_cross_encoder_reranker(settings: Settings) -> SimpleCrossEncoderReranker | CrossEncoderReranker | None:
    """创建 Cross-Encoder 精排器（Phase 2）。"""
    if not settings.use_cross_encoder:
        return None
    try:
        # 优先使用 sentence-transformers 的 CrossEncoder（更快）
        reranker = SimpleCrossEncoderReranker(
            model_name=settings.cross_encoder_model,
            device=settings.cross_encoder_device,
            batch_size=settings.cross_encoder_batch_size,
        )
        if reranker.available:
            return reranker
    except Exception as exc:
        logger.debug("SimpleCrossEncoder not available: %s", exc)

    # 回退到基于 transformers 的实现
    return CrossEncoderReranker(
        model_name=settings.cross_encoder_model,
        device=settings.cross_encoder_device,
        batch_size=settings.cross_encoder_batch_size,
        ce_weight=settings.cross_encoder_ce_weight,
        bm25_weight=settings.cross_encoder_bm25_weight,
        vector_weight=settings.cross_encoder_vector_weight,
    )


def create_feedback_loop(settings: Settings) -> FeedbackLoop | None:
    """创建反馈闭环（Phase 2）。"""
    if not settings.use_feedback_loop:
        return None
    return FeedbackLoop(
        db_path=settings.feedback_db_path,
        batch_size=settings.feedback_batch_size,
        flush_interval_seconds=settings.feedback_flush_interval,
    )


def create_hybrid_retriever(
    settings: Settings,
    store,
    bm25_engine: BM25Engine | None,
    reranker,
    feedback_loop: FeedbackLoop | None,
) -> HybridRetriever | None:
    """创建三阶段混合检索器（Phase 2）。"""
    if not settings.use_hybrid_retrieval:
        return None

    config = HybridRetrieverConfig(
        enable_bm25=settings.use_bm25,
        bm25_top_k=settings.bm25_top_k,
        enable_vector=True,
        vector_top_k=settings.hybrid_vector_top_k,
        enable_rerank=settings.use_cross_encoder,
        rerank_top_k=settings.hybrid_rerank_top_k,
        ce_weight=settings.cross_encoder_ce_weight,
        bm25_weight=settings.cross_encoder_bm25_weight,
        vector_weight=settings.cross_encoder_vector_weight,
        enable_hybrid=True,
        hybrid_fusion_method=settings.hybrid_fusion_method,
        rrf_k=settings.hybrid_rrf_k,
        cache_enabled=True,
        cache_ttl_seconds=settings.hybrid_cache_ttl_seconds,
        enable_feedback=settings.use_feedback_loop,
        timeout_ms=settings.hybrid_timeout_ms,
    )

    return HybridRetriever(
        config=config,
        bm25_engine=bm25_engine,
        vector_store=store,
        reranker=reranker,
        feedback_loop=feedback_loop,
    )


def create_input_processor(settings: Settings) -> MultimodalInputProcessor:
    visual_embedding_index = None
    if settings.use_visual_embedding:
        if not settings.ark_api_key:
            logger.warning("USE_VISUAL_EMBEDDING=true but ARK_API_KEY is empty; using signature image fallback.")
        else:
            model = settings.visual_embedding_model_name
            embedder = ArkMultimodalEmbeddingFunction(
                api_key=settings.ark_api_key,
                base_url=settings.ark_base_url,
                model=model,
                timeout_seconds=settings.embedding_timeout_seconds,
                batch_size=1,
            )
            visual_embedding_index = ProductVisualEmbeddingIndex(
                settings.visual_embedding_index_file,
                embedder=embedder,
                cache=EmbeddingCache(settings.embedding_cache_file),
                model=model,
            )
            if not visual_embedding_index.entries:
                logger.warning(
                    "Visual embedding index is empty; run scripts/build_visual_embedding_index.py "
                    "or use signature image fallback."
                )

    vlm_client = create_vlm_client(settings)
    image_understanding = create_image_understanding_provider(settings, vlm_client)

    # Phase 1: 结构化 VLM（可选增强）
    structured_vlm = create_structured_vlm_provider(settings, vlm_client)
    if structured_vlm:
        logger.info("Structured VLM provider enabled for richer image understanding")

    return MultimodalInputProcessor(
        settings.product_data_file,
        settings.product_image_path,
        visual_embedding_index=visual_embedding_index,
        image_understanding=image_understanding,
        structured_vlm=structured_vlm,
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: 工程化改造 — 记忆系统 + 任务调度
# ═══════════════════════════════════════════════════════════════════════

from server.memory.memory_manager import MemoryManager
from server.task.intent_decomposer import IntentDecomposer
from server.task.task_scheduler import TaskScheduler


def create_memory_manager(settings: Settings) -> MemoryManager | None:
    """创建记忆管理器（Phase 3）。"""
    if not settings.enable_memory_system:
        return None
    return MemoryManager(
        long_term_db_path=settings.long_term_memory_db_path,
    )


def create_task_scheduler(settings: Settings) -> TaskScheduler | None:
    """创建任务调度器（Phase 3）。"""
    if not settings.enable_async_tasks:
        return None
    return TaskScheduler(
        max_workers=settings.task_scheduler_workers,
        default_timeout=settings.task_scheduler_timeout,
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 5: 用户系统 — 数据库 + 认证
# ═══════════════════════════════════════════════════════════════════════

def init_user_system(settings: Settings):
    """
    初始化用户系统（MySQL + Redis）
    在 create_app() 中调用，仅在 enable_user_system=True 时执行
    """
    if not settings.enable_user_system:
        return
    
    # 初始化数据库连接
    from server.database.mysql_client import get_engine, init_db
    import asyncio
    try:
        asyncio.get_event_loop().run_until_complete(init_db())
    except Exception as exc:
        logger.warning("User system DB init failed (tables may already exist): %s", exc)


def get_db_session_dep():
    """FastAPI Depends 用的数据库会话生成器"""
    from server.database.mysql_client import get_db
    return get_db
