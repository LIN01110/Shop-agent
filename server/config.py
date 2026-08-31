from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # ── Phase 1: 多模态核心升级 ──
    # VLM 结构化输出（JSON）
    use_structured_vlm: bool = True
    structured_vlm_prompt_template: str = ""   # 空则使用默认 JSON prompt
    structured_vlm_fallback: bool = True        # JSON 解析失败时回退到旧版

    # Chinese-CLIP 本地推理
    use_chinese_clip: bool = False
    chinese_clip_model: str = "OFA-Sys/chinese-clip-vit-base-patch16"
    chinese_clip_device: str = "auto"
    chinese_clip_cache_path: str = "server/runtime/chinese_clip_cache.sqlite3"

    # Milvus 向量存储
    use_milvus: bool = False
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_db_name: str = "default"
    milvus_collection_name: str = "products"
    milvus_dim: int = 512
    milvus_index_type: str = "AUTOINDEX"        # AUTOINDEX / IVF_FLAT / HNSW
    milvus_metric_type: str = "COSINE"
    milvus_fallback_enabled: bool = True         # Milvus 不可用时回退到 Chroma/Local

    # Milvus SQ8 向量压缩（显存优化）
    milvus_enable_sq8: bool = False              # 启用 SQ8 标量量化
    milvus_sq_type: str = "SQ8"                   # SQ8 / SQ6 / SQ4U / BF16 / FP16
    milvus_sq_refine: bool = False               # 搜索时 refine 重排序（提升精度）
    milvus_sq_refine_type: str = "FP32"           # refine 精度: FP32 / SQ8 / BF16 / FP16

    # ── Phase 2: 检索引擎升级 ──
    # BM25 倒排索引
    use_bm25: bool = True
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_top_k: int = 200
    bm25_index_path: str = "server/runtime/bm25_index.json"

    # Cross-Encoder 精排
    use_cross_encoder: bool = True
    cross_encoder_model: str = "BAAI/bge-reranker-base"
    cross_encoder_device: str = "auto"
    cross_encoder_batch_size: int = 32
    cross_encoder_ce_weight: float = 0.7
    cross_encoder_bm25_weight: float = 0.15
    cross_encoder_vector_weight: float = 0.15

    # 三阶段混合检索
    use_hybrid_retrieval: bool = True
    hybrid_vector_top_k: int = 100
    hybrid_rerank_top_k: int = 20
    hybrid_fusion_method: str = "rrf"            # rrf / weighted / max
    hybrid_rrf_k: int = 60
    hybrid_cache_ttl_seconds: float = 300.0
    hybrid_timeout_ms: float = 5000.0

    # 反馈闭环
    use_feedback_loop: bool = True
    feedback_db_path: str = "server/runtime/feedback.sqlite3"
    feedback_batch_size: int = 50
    feedback_flush_interval: float = 5.0

    # ── Phase 3: 生产基线 ──
    # 查询缓存（L1 内存 + L2 Redis）
    query_cache_l1_ttl_seconds: float = 5.0
    query_cache_l2_ttl_seconds: float = 300.0
    query_cache_compress: bool = True

    # 输入安全
    enable_input_guard: bool = True
    input_guard_similarity_threshold: float = 0.85

    # 幻觉检测
    enable_hallucination_guard: bool = True
    hallucination_guard_strict_mode: bool = True  # True=拒绝输出，False=标记但不拦截
    hallucination_max_regenerate: int = 2          # LLM 最大重生成次数
    hallucination_log_path: str = "server/runtime/hallucination.jsonl"

    # 序号引用模式（防ID幻觉）
    use_indexed_grounding: bool = True

    # 检索参数调优
    retrieval_candidate_multiplier: int = 5
    retrieval_min_candidate_pool: int = 20

    # 监控开关
    enable_metrics: bool = True

    # 质量监控
    enable_quality_metrics: bool = True
    quality_metrics_window_size: int = 500


    # 记忆系统
    enable_memory_system: bool = False
    long_term_memory_db_path: str = "server/runtime/user_memory.sqlite3"
    perception_memory_turns: int = 5
    short_term_memory_ttl_seconds: float = 7200.0

    # 异步任务
    enable_async_tasks: bool = False
    task_scheduler_workers: int = 4
    task_scheduler_timeout: float = 10.0

    # 多级缓存
    enable_query_cache: bool = True
    query_cache_ttl_seconds: float = 300.0
    query_cache_max_size: int = 1000

    # 启动预热
    enable_warmup: bool = True
    warmup_queries: str = "连衣裙,口红,精华,运动鞋,T恤"

    # ── Phase 5: 用户系统 + 真实数据 + 优惠券 ──
    # MySQL 数据库
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "shop_agent"
    mysql_pool_size: int = 10
    mysql_max_overflow: int = 20
    mysql_echo: bool = False

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # JWT
    jwt_secret_key: str = Field(default="", description="JWT 签名密钥，生产环境必须通过环境变量设置")
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    # 用户系统开关
    enable_user_system: bool = False

    # ── Phase 6: Agent Harness 层 ──
    # LLM 后端选择
    llm_backend: str = "ark"  # "deepseek" | "llamacpp" | "ark" | "mock"
    agents_md_path: str = "AGENTS.md"  # Harness 外置提示词文件路径

    # DeepSeek API 配置
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"  # deepseek-chat / deepseek-reasoner
    deepseek_timeout_seconds: float = 45.0

    # llama.cpp 本地配置
    llamacpp_base_url: str = "http://localhost:8080/v1"
    llamacpp_model: str = ""  # 空则自动检测
    llamacpp_timeout_seconds: float = 60.0

    # Harness 压缩器配置
    harness_compactor_max_log_tokens: int = 6000
    harness_compactor_preserve_turns: int = 4

    # Harness 指标暴露
    harness_enable_metrics: bool = True
    harness_strict_prefix_guard: bool = False  # True = 指纹漂移时 throw

    # ── 原有配置 ──
    app_env: str = "development"
    server_host: str = "127.0.0.1"
    server_port: int = 8000
    enable_debug_api: bool | None = None
    enable_admin_console: bool | None = None
    cors_allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    cors_allow_credentials: bool = False
    session_backend: str = "sqlite"
    session_db_path: str = "server/runtime/sessions.sqlite3"
    session_redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 43200
    session_max_items: int = 500
    trace_max_items: int = 200
    enable_query_feedback_log: bool = True
    query_feedback_log_path: str = "server/runtime/query_failures.jsonl"
    max_concurrent_requests: int = 100
    request_timeout_seconds: float = 30.0
    retrieval_timeout_seconds: float = 5.0

    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_model: str = "ep-20260514111645-lmgt2"

    # 小模型降级配置
    ark_lite_api_key: str = ""           # 小模型 API Key（空则复用 ark_api_key）
    ark_lite_base_url: str = ""          # 小模型 Base URL（空则复用 ark_base_url）
    ark_lite_model: str = ""             # 小模型 endpoint ID，如 doubao-1.5-lite-32k
    enable_lite_model_fallback: bool = False  # 是否启用小模型降级

    # LLM 输出长度控制
    llm_max_tokens: int = 0              # 0=不限制，>0 限制 LLM 最大输出 token 数
    llm_planning_max_tokens: int = 512   # Planning 阶段最大输出 token
    llm_answer_max_tokens: int = 1024    # Answer Generation 阶段最大输出 token

    use_llm: bool = True
    use_semantic_llm: bool = True
    semantic_llm_budget_seconds: float = 0.25
    llm_timeout_seconds: float = 45.0
    llm_retry_attempts: int = 2
    llm_circuit_breaker_failures: int = 3
    llm_circuit_breaker_reset_seconds: float = 30.0
    recommendation_llm_budget_seconds: float = 0.0
    use_ark_embedding: bool = False
    ark_embedding_api: str = "text"
    ark_embedding_model: str = "doubao-embedding-text-240515"
    embedding_timeout_seconds: float = 60.0
    embedding_batch_size: int = 4
    use_scenario_embedding: bool = False
    scenario_embedding_min_similarity: float = 0.72
    scenario_embedding_margin: float = 0.06

    use_chroma: bool = False
    chroma_dir: str = "server/chroma_db"
    chroma_collection_name: str = "products"
    product_data_path: str = "data/products_ref.json"
    product_image_dir: str = "data/product_images"
    use_vlm: bool = False
    vlm_model: str = ""
    vlm_timeout_seconds: float = 10.0
    vlm_retry_attempts: int = 2
    vlm_circuit_breaker_failures: int = 3
    vlm_circuit_breaker_reset_seconds: float = 30.0
    vlm_max_tokens: int = 256
    vlm_prompt_template: str = ""
    use_visual_embedding: bool = False
    visual_embedding_model: str = ""
    visual_embedding_index_path: str = "server/runtime/product_image_vectors.json"
    embedding_cache_path: str = "server/runtime/embedding_cache.sqlite3"
    upload_image_dir: str = "server/runtime/uploads/images"
    upload_image_max_bytes: int = 4_500_000
    upload_image_ttl_seconds: int = 86_400
    scenario_bundle_path: str = "data/scenario_bundles.json"
    public_base_url: str = "http://127.0.0.1:8000"
    commerce_fact_backend: str = "mock"
    commerce_mock_data_path: str = "data/commerce_mock.json"
    taxonomy_eval_cases_path: str = "server/eval/taxonomy_query_cases.json"

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def product_data_file(self) -> Path:
        path = Path(self.product_data_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def chroma_path(self) -> Path:
        path = Path(self.chroma_dir)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def session_db_file(self) -> Path:
        path = Path(self.session_db_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def normalized_session_backend(self) -> str:
        return self.session_backend.strip().lower()

    @property
    def product_image_path(self) -> Path:
        path = Path(self.product_image_dir)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def vlm_model_name(self) -> str:
        return self.vlm_model.strip() or self.ark_model

    @property
    def visual_embedding_model_name(self) -> str:
        return self.visual_embedding_model.strip() or self.ark_embedding_model

    @property
    def visual_embedding_index_file(self) -> Path:
        path = Path(self.visual_embedding_index_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def embedding_cache_file(self) -> Path:
        path = Path(self.embedding_cache_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def upload_image_path(self) -> Path:
        path = Path(self.upload_image_dir)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def scenario_bundle_file(self) -> Path:
        path = Path(self.scenario_bundle_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def commerce_mock_data_file(self) -> Path:
        path = Path(self.commerce_mock_data_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def taxonomy_eval_cases_file(self) -> Path:
        path = Path(self.taxonomy_eval_cases_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def query_feedback_log_file(self) -> Path:
        path = Path(self.query_feedback_log_path)
        return path if path.is_absolute() else ROOT_DIR / path

    @property
    def normalized_commerce_fact_backend(self) -> str:
        return self.commerce_fact_backend.strip().lower()

    @property
    def debug_api_enabled(self) -> bool:
        if self.enable_debug_api is not None:
            return self.enable_debug_api
        return self.app_env.lower() not in {"prod", "production"}

    @property
    def admin_console_enabled(self) -> bool:
        if self.enable_admin_console is not None:
            return self.enable_admin_console
        return self.app_env.lower() not in {"prod", "production"}

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def cors_credentials_enabled(self) -> bool:
        return self.cors_allow_credentials and "*" not in self.cors_origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
