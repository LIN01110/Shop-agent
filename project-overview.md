# RAG 多模态电商智能导购 Agent — 项目文件说明

> 本文档整理了 `rag-shopping-agent-main` 项目中所有代码文件、数据文件和配置文件的作用，方便快速定位和二次开发。

---

## 目录总览

| 目录 | 作用 |
|------|------|
| `client/` | Android 客户端（Kotlin + Jetpack Compose） |
| `server/` | FastAPI 后端与 RAG/Agent 核心模块 |
| `data/` | 示例商品数据、类目体系、评估集等 JSON |
| `docs/` | 架构文档、接口文档、压测报告、答辩材料 |
| `scripts/` | 启动脚本、压测脚本、数据预处理脚本 |

---

## 一、client/ — Android 客户端

基于 Kotlin + Jetpack Compose 构建的原生 Android 应用，负责用户交互、SSE 流式对话、商品卡片展示、对比面板和购物车。

### 1.1 项目配置

| 文件 | 作用 |
|------|------|
| `client/build.gradle.kts` | 根项目 Gradle 构建配置 |
| `client/settings.gradle.kts` | Gradle 项目设置，包含模块声明 |
| `client/gradle.properties` | Gradle 全局属性配置 |
| `client/gradlew` / `gradlew.bat` | Gradle Wrapper 启动脚本 |
| `client/local.properties` | 本地 SDK 路径配置（不提交 Git） |
| `client/app/build.gradle.kts` | App 模块构建配置，声明依赖和编译选项 |
| `client/app/src/main/AndroidManifest.xml` | Android 应用清单，声明权限、Activity、主题 |
| `client/app/src/main/res/values/strings.xml` | 应用字符串资源（如 App 名称） |
| `client/app/src/main/res/values/themes.xml` | Material 主题样式定义 |
| `client/README.md` | 客户端模块说明文档 |

### 1.2 数据模型（Model）

| 文件 | 作用 |
|------|------|
| `client/.../model/CartState.kt` | 购物车状态数据类：商品列表、总价、数量 |
| `client/.../model/ChatMessage.kt` | 聊天消息数据类：ID、角色、文本、是否思考中 |
| `client/.../model/ChatSession.kt` | 会话摘要和会话快照数据类 |
| `client/.../model/ComparisonCard.kt` | 商品对比卡片数据类：标题、对比产品列表、推荐建议 |
| `client/.../model/ImageAttachment.kt` | 图片附件数据类：URI、字节、MIME 类型、文件名 |
| `client/.../model/ProductCard.kt` | 商品卡片数据类：ID、名称、类目、品牌、价格、评分、图片等 |

### 1.3 网络层（Network）

| 文件 | 作用 |
|------|------|
| `client/.../network/BackendConfig.kt` | 后端配置：定义本地反向代理基地址 `http://127.0.0.1:8000` |
| `client/.../network/ChatSseClient.kt` | **SSE 客户端核心**：基于 OkHttp 建立 SSE 连接，逐行解析 `event` 和 `data`，按事件类型分发到回调；处理网络中断和解析失败 |

### 1.4 仓库层（Repository）

| 文件 | 作用 |
|------|------|
| `client/.../repository/ChatRepository.kt` | 聊天仓库：封装 SSE 请求构造、图片上传、会话管理、购物车/对比/商品数据解析，连接 ViewModel 与网络层 |

### 1.5 UI 层（Compose）

| 文件 | 作用 |
|------|------|
| `client/.../ui/ChatScreen.kt` | **主聊天界面**：包含消息列表、输入框、图片选择器、底部商品/对比/购物车面板的状态管理 |
| `client/.../ui/MessageComponents.kt` | 消息气泡组件：用户消息和机器人消息的 UI 展示 |
| `client/.../ui/MessageComposer.kt` | 消息输入栏组件：文本输入、发送按钮、图片附件预览 |
| `client/.../ui/ProductComponents.kt` | 商品卡片组件：商品图片、名称、价格、评分、加购按钮的 Compose UI |
| `client/.../ui/ComparisonPanel.kt` | 对比面板组件：展示多商品对比表格和推荐结论 |
| `client/.../ui/CartPanel.kt` | 购物车面板组件：展示购物车商品列表、数量修改、删除、结算 |
| `client/.../ui/Theme.kt` | 应用主题定义：主色、次色、Material 颜色方案 |

### 1.6 视图模型（ViewModel）

| 文件 | 作用 |
|------|------|
| `client/.../viewmodel/ChatViewModel.kt` | **Android 端状态管理中心**：管理 `ChatUiState`（消息、商品、对比、购物车、流式状态），处理 6 种 SSE 事件映射到 UI 状态更新 |

### 1.7 入口 Activity

| 文件 | 作用 |
|------|------|
| `client/.../MainActivity.kt` | 应用入口：继承 `ComponentActivity`，通过 `setContent` 加载 `ChatRoute` 和主题 |

---

## 二、server/ — FastAPI 后端

后端采用 FastAPI 框架，提供 REST API 和 SSE 流式接口，核心能力包括：对话编排（Orchestrator）、语义规划（Semantic Planner）、RAG 检索（Retrieval Pipeline）、Agent 工作流（Agent Workflow）、购物车管理、商品对比、多模态输入（图片找货）和场景化组合推荐。

### 2.1 入口与配置

| 文件 | 作用 |
|------|------|
| `server/main.py` | **FastAPI 应用入口**：`create_app()` 创建应用，注册 CORS、静态文件、路由、生命周期管理；`lifespan` 预热 Orchestrator |
| `server/config.py` | **配置中心**：`Settings` 类集中管理环境变量（`USE_CHROMA`、`USE_LLM`、`USE_ARK_EMBEDDING`、模型、超时阈值、端口等） |
| `server/app_container.py` | **依赖容器**：`create_app()` 的依赖注入工厂，负责创建和缓存：LLM 客户端、向量存储、Embedding 函数、Session 存储、Orchestrator 等核心组件 |
| `server/requirements.txt` | Python 依赖清单 |
| `server/__init__.py` | 空包初始化文件 |

### 2.2 API 路由（server/api/）

| 文件 | 作用 |
|------|------|
| `server/api/chat.py` | **核心对话接口**：`POST /chat` 返回 SSE `StreamingResponse`；`event_stream()` 调用 `orchestrator.stream_chat()`；同时提供 WebSocket 备用接口 `/ws/chat` |
| `server/api/cart.py` | 购物车 REST 接口：直接增删改查购物车，绕过对话流 |
| `server/api/products.py` | 商品详情接口：`/products/{id}` 返回本地商品详情页 HTML，`/assets/products/...` 提供商品主图静态资源 |
| `server/api/sessions.py` | 会话管理接口：获取/恢复会话状态，返回购物车摘要 |
| `server/api/uploads.py` | 图片上传接口：接收用户上传的图片，保存到本地并返回图片 URL |
| `server/api/admin.py` | 管理后台接口：提供 `/admin` 管理页面、trace 统计、健康度检查 |
| `server/api/debug.py` | 调试接口：`/debug/traces` 查看最近对话 trace，用于定位问题 |

### 2.3 Agent 核心（server/agent/）

| 文件 | 作用 |
|------|------|
| `server/agent/orchestrator.py` | **后端大脑/编排器**：串联一次完整请求的 10+ 步流程：trace → session → input_processor → semantic_planner → context_filters → scope_transition → merge_filters → AgentTurnContext → workflow.stream() → query_feedback → trace_store → save |
| `server/agent/workflow.py` | **Agent 工作流调度**：`AgentWorkflow` 按优先级遍历 handlers，第一个 `matches()` 返回 True 的 handler 执行；负责 Handler 分派机制 |
| `server/agent/default_handlers.py` | **Handler 装配器**：`build_default_workflow()` 把 6 个 Handler 按优先级组装成 `AgentWorkflow`（澄清 → 购物车 → 对比 → 场景组合 → 上下文追问 → 推荐兜底） |
| `server/agent/semantic_llm.py` | **语义规划器**：双层设计（规则 fallback + LLM plan），在短预算内（默认 0.8s）请求 LLM 输出结构化 JSON plan，超时自动 fallback |
| `server/agent/semantic_schema.py` | 语义规划的数据模型：`SemanticPlan`、`SemanticFilter`、`CartAction`、`ReferenceType` 等 Pydantic 模型定义 |
| `server/agent/semantic_rules.py` | 规则层语义规划：`build_rule_plan()` 基于规则快速生成 plan，作为 LLM 的 fallback |
| `server/agent/planning_policy.py` | 规划策略校验：`PlannerPolicy` 校验 LLM plan 的合理性，购物车写操作（加购/删除/改数量）缺少确定性证据时改为澄清 |
| `server/agent/planning_context.py` | 规划上下文压缩：将会话状态（候选商品、购物车、历史对话）压缩为适合 LLM 读取的上下文格式 |
| `server/agent/intent.py` | 用户意图枚举和检测：`COMPARE`、`CART`、`BUYING`、`BROWSING` 等 |
| `server/agent/recommendation_handler.py` | **推荐兜底 Handler**：调用 `ProductSearchTool` → 检索 → LLM 生成回答 → `GroundingGuard` 校验 → 若拦截则降级模板回答 → yield product_card + done |
| `server/agent/conversation_handlers.py` | 澄清和上下文追问 Handler：`ClarificationHandler`（宽泛需求生成追问问题）、`ContextFollowUpHandler`（解析“第二个怎么样”等引用） |
| `server/agent/commerce_handlers.py` | 电商业务 Handler：`CartHandler`（购物车操作）、`CompareHandler`（商品对比）、`ScenarioBundleHandler`（场景组合推荐） |
| `server/agent/context.py` | 上下文过滤提取：从消息中提取“别太贵”“同价位”等上下文过滤条件，绑定到已推荐的商品 |
| `server/agent/filters.py` | 消息过滤条件提取：从用户消息中提取 `product_type`、品牌、价格、排除条件等结构化过滤条件 |
| `server/agent/grounding.py` | **GroundingGuard 防幻觉**：LLM 回答先缓冲再校验，拦截 4 类问题：未提供的优惠/库存/销量、候选外价格、无引用商品、绝对化/医疗化承诺（“保证”“根治”“100%”） |
| `server/agent/product_discovery.py` | 商品发现策略：根据 `SemanticPlan` 决定展示模式（单件/列表）、候选池大小、是否允许 LLM 回答 |
| `server/agent/query_rewriter.py` | 查询重写：将会话中已确认的过滤条件、排除条件、pending_subject 拼入查询，增强检索相关性 |
| `server/agent/query_feedback.py` | 查询反馈记录：记录检索失败、规划失败等事件，用于离线分析和持续优化 |
| `server/agent/card_binding.py` | 商品卡片绑定：将 LLM 回答中提到的商品与候选商品卡片做绑定，未提及的商品会被标记为 dropped |
| `server/agent/responses.py` | 响应流式工具：`stream_text()` 将文本切分为 token 逐字流式输出，`build_done_payload()` 构造 SSE 结束事件 |
| `server/agent/tracing.py` | Agent Trace 追踪：`InMemoryTraceStore` 记录每次对话的完整链路，用于调试和离线评估 |
| `server/agent/scope_transition.py` | 商品范围切换策略：检测到品类切换（如从“眼霜”切换到“手机”）时，清理旧过滤条件，避免旧品类/预算污染新需求 |
| `server/agent/rule_signals.py` | 规则信号生成：`build_rule_signals()` 分析消息特征，决定走确定性规则、LLM planner 还是澄清路径 |
| `server/agent/scenario_catalog.py` | 场景目录管理：从 `data/scenario_bundles.json` 加载场景化组合配置，提供场景匹配和检索 |
| `server/agent/scenario_classifier.py` | 场景分类器：混合分类（规则 + 语义相似度），判断用户请求是否命中某个场景化组合（如“三亚度假”） |
| `server/agent/scenario_matching.py` | 场景匹配逻辑：计算场景与查询的匹配分数，识别显式组合请求（“搭配一套”“推荐一套”） |
| `server/agent/scenario_models.py` | 场景数据模型：`ScenarioBundle`、`ScenarioSlot`、`ScenarioBundleConfig` 等 Pydantic 定义 |
| `server/agent/scenario_response.py` | 场景组合回答构建：将多槽位（防晒、穿搭、出行、拍照）的商品组合格式化为自然语言回答 |
| `server/agent/scenario_utils.py` | 场景工具函数：模板渲染、槽位过滤合并、稳定分桶等辅助函数 |
| `server/agent/scenarios.py` | 场景组合入口：暴露场景目录的加载、匹配、回答构建等高阶 API |
| `server/agent/bundle_schema.py` | 组合方案数据结构：`BundlePlan`、`Slot`、`SlotCandidateSet` 等数据类定义 |
| `server/agent/bundle_planner.py` | 组合方案规划器：将场景化请求转换为可执行的多槽位购物计划（每个槽位对应一个商品类目） |
| `server/agent/bundle_retriever.py` | 组合方案检索器：为每个槽位独立检索候选商品 |
| `server/agent/bundle_ranker.py` | 组合方案排序器：按预算内、槽位覆盖度、总价等维度对组合方案排序 |
| `server/agent/bundle_optimizer.py` | 组合方案优化器：从各槽位候选中选出最优组合，满足预算和必需槽位约束 |
| `server/agent/bundle_grounding.py` | 组合方案事实校验：校验组合方案中的商品是否 grounded 在真实商品库中 |

### 2.4 RAG 检索层（server/rag/）

| 文件 | 作用 |
|------|------|
| `server/rag/retrieval_pipeline.py` | **RAG 检索 Pipeline**：5 阶段流程（1. store_prefilter_recall 召回 50 条 → 2-7. PostProcessors 过滤 → 8. dedupe_hits → 9. rerank_hits 重排序） |
| `server/rag/vector_store.py` | **向量存储抽象**：`VectorStore` 协议定义；`ChromaStore`（Chroma 实现，metadata where 下推过滤）；`LocalJsonVectorStore`（本地 JSON 倒排索引 + 特征缓存 fallback） |
| `server/rag/stores.py` | ChromaStore 与 LocalJsonVectorStore 的具体实现细节，metadata 过滤、embedding 缓存等 |
| `server/rag/embeddings.py` | Embedding 实现：`HashingEmbeddingFunction`（本地哈希 embedding）、`ArkEmbeddingFunction`（Ark/Doubao API embedding） |
| `server/rag/embedding_cache.py` | Embedding 缓存：SQLite 持久化缓存，避免重复计算 embedding |
| `server/rag/ingest.py` | 数据灌入脚本：将 `products_ref.json` 灌入 Chroma 向量库，命令行入口 `python -m server.rag.ingest` |
| `server/rag/documents.py` | 商品文档加载：将 JSON 商品数据转换为 `VectorDocument`（文本 + metadata） |
| `server/rag/types.py` | RAG 类型定义：`VectorDocument`、`VectorSearchFilters` 等数据类 |
| `server/rag/scoring.py` | 检索评分：tokenize、哈希 embedding、多维度加权打分（product_type +20, category +12, keyword +6, brand +5, 预算内 +2） |
| `server/rag/post_process.py` | **检索后处理器**：7 个 `PostProcessor`（RangeFilter 价格、ProductTypeFilter、CategoryFilter、BrandFilter、StockFilter、KeywordFilter、ExclusionFilter 反选），程序化过滤保证反选稳定性 |
| `server/rag/taxonomy.py` | 商品类目体系：从 `data/product_taxonomy.json` 加载类目，提供 `extract_product_type_matches()`、`infer_product_type_ids()` 等别名匹配和推断能力 |
| `server/rag/category_taxonomy.py` | 商品分类体系：从 `data/category_taxonomy.json` 加载分类，提供分类匹配和推断 |
| `server/rag/taxonomy_governance.py` | 类目治理：对商品记录打 taxonomy 标签、生成审计 manifest，保证类目变更可追溯 |
| `server/rag/brand_aliases.py` | 品牌别名处理：从 `data/brand_aliases.json` 加载品牌别名，支持品牌提及识别和归一化（如 “adidas” → “阿迪达斯”） |
| `server/rag/identifiers.py` | 标识符安全处理：将字符串转换为 Chroma 集合安全的标识符 |
| `server/rag/chroma_metadata.py` | Chroma metadata 过滤：将 `SearchFilters` 转换为 Chroma `where` 表达式，实现 metadata 下推过滤 |

### 2.5 NLU 层（server/nlu/）

| 文件 | 作用 |
|------|------|
| `server/nlu/query_understanding.py` | 查询理解：`PresentationMode`（展示模式：单件/列表），识别列表类请求（“所有”“全部”） |
| `server/nlu/quantity.py` | 数量解析：从消息中提取数量、单位、序数词（“第二个”）、购买量表达（“来两件”） |
| `server/nlu/taxonomy_classifier.py` | 类目分类器：混合分类（规则 + embedding 相似度），判断查询属于哪个商品类目 |

### 2.6 LLM 层（server/llm/）

| 文件 | 作用 |
|------|------|
| `server/llm/ark_client.py` | **LLM 客户端**：封装 Doubao/Ark（OpenAI-compatible）流式调用，处理超时、重试、异常，返回异步迭代 token；调用失败时自动降级 |
| `server/llm/prompt.py` | Prompt 模板：`SYSTEM_PROMPT` 系统提示词，`build_product_context()` 构建商品上下文，`build_grounded_messages()` 构造带 grounding 约束的 messages |
| `server/prompts/system_prompt.md` | 系统 Prompt 文档版：定义 Agent 回答约束（不编造、不绝对化、移动端简洁表达） |

### 2.7 输入处理层（server/inputs/）

| 文件 | 作用 |
|------|------|
| `server/inputs/multimodal.py` | **多模态输入处理器**：处理上传图片，计算视觉签名 → 相似匹配 → 生成 `image_summary` → yield `image_analysis` 事件，图片线索拼入查询 |
| `server/inputs/image_similarity.py` | 图片相似度：基于视觉签名（轻量 12x12 RGB）做相似匹配，推断商品类目 |
| `server/inputs/visual_embedding.py` | 视觉 Embedding 索引：将商品图片转换为向量索引，支持多模态 embedding 检索 |
| `server/inputs/upload_store.py` | 图片上传存储：接收上传图片，保存到本地磁盘，生成访问 URL |
| `server/inputs/base.py` | 输入处理基础类型：`ProcessedInput` 数据类定义 |

### 2.8 会话与状态（server/session/）

| 文件 | 作用 |
|------|------|
| `server/session/state.py` | **会话状态核心**：`SessionState` 包含消息历史、过滤条件、候选商品、购物车、pending_subject、product_type_scope；`FilterCondition` 过滤条件；`SQLiteSessionStore` / `RedisSessionStore` / `MemorySessionStore` 三种持久化实现 |
| `server/session/memory.py` | 会话记忆刷新：维护用户画像摘要，压缩历史对话（保留最近 12 轮），更新会话内存 |

### 2.9 电商业务层（server/commerce/）

| 文件 | 作用 |
|------|------|
| `server/commerce/facts.py` | 商业事实提供者协议：`CommerceFactProvider` 定义价格、库存、促销等事实的获取接口；`LocalMockCommerceProvider` 本地 mock 实现 |
| `server/commerce/models.py` | 商业事实模型：`BusinessFact`、`ProductFacts`、`ProductIdentity` 等数据类 |
| `server/commerce/services.py` | 电商数据网关：`CommerceDataGateway` 商品数据查询服务，本地 JSON 数据源实现 |

### 2.10 工具层（server/tools/）

| 文件 | 作用 |
|------|------|
| `server/tools/registry.py` | **工具注册中心**：`ToolRegistry` 注册所有工具（商品搜索、对比、购物车），供 Orchestrator 和 Handler 调用 |
| `server/tools/product_search.py` | **商品搜索工具**：调用 `ProductRetrievalPipeline` 执行检索，将结果转换为商品卡片，附加 evidence（检索依据）用于 GroundingGuard 校验 |
| `server/tools/product_compare.py` | **商品对比工具**：对比两个或多个商品，生成对比回答和对比卡片 |
| `server/tools/product_compare_presenter.py` | 对比展示器：构建对比维度和推荐建议，格式化对比结果 |
| `server/tools/product_compare_selection.py` | 对比商品选择：从候选商品中选出适合对比的商品对 |
| `server/tools/product_compare_terms.py` | 对比维度提取：从用户查询中提取对比维度（如“适合干皮”“性价比”） |
| `server/tools/product_evidence.py` | 商品证据收集：为检索结果收集证据，用于 Grounding 校验 |
| `server/tools/product_urls.py` | 商品 URL 生成：生成商品详情页和主图 URL |
| `server/tools/cart.py` | **购物车工具**：解析购物车操作（加购、删除、改数量、查看、结算），执行后返回 `cart_update` 事件 |
| `server/tools/cart_operations.py` | 购物车操作解析：从自然语言中解析具体的购物车操作（“来两件”“删除”等） |

### 2.11 网关与中间件（server/gateway/）

| 文件 | 作用 |
|------|------|
| `server/gateway/middleware.py` | 请求治理中间件：`RequestGovernanceMiddleware` 限制并发数和请求超时，流式路径豁免超时 |
| `server/gateway/resilience.py` | 弹性设计：重试、降级、熔断等弹性原语 |

### 2.12 测试（server/tests/）

| 文件 | 作用 |
|------|------|
| `server/tests/conftest.py` | pytest 全局配置：测试环境禁用 LLM、禁用 Ark Embedding |
| `server/tests/test_*.py` | 各模块的单元测试，覆盖：Admin API、App 配置、品牌别名、组合优化、购物车 API、购物车工具、类目分类、聊天 API、电商网关、Grounding Guard、LLM 客户端、多模态和场景、Orchestrator 过滤、Orchestrator LLM、商品对比、商品发现、商品搜索、数量解析、查询反馈、查询重写、规则信号、场景目录、场景分类、范围切换策略、语义规划、会话记忆、会话状态、Sessions API、类目分类、类目治理、上传、向量存储、视觉 Embedding |

---

## 三、data/ — 数据文件

| 文件 | 作用 |
|------|------|
| `data/products_ref.json` | **核心商品库**：约 645KB，包含 100 个左右的商品完整数据（ID、名称、类目、品牌、价格、库存、属性、描述、图片等），是 RAG 检索和对话推荐的数据源 |
| `data/product_taxonomy.json` | **商品类目体系**：定义标准商品类型（如 `beauty.eye_cream` 眼霜、`clothes.sports_shoes` 运动鞋）及其别名、匹配字段，用于检索前的 facet 过滤和意图识别 |
| `data/category_taxonomy.json` | **商品分类体系**：定义一级分类（如 `beauty.skincare` 美妆护肤、`clothes.sports` 服饰运动）及其别名，用于分类匹配 |
| `data/brand_aliases.json` | **品牌别名表**：品牌标准名与别名映射（如 Nike → 耐克、adidas → 阿迪达斯），用于品牌识别和归一化 |
| `data/commerce_mock.json` | **商业事实 Mock**：模拟价格、库存、促销、发票等实时商业数据，用于本地 fallback 和测试 |
| `data/sample_products.json` | **示例商品数据**：精简版商品数据，用于早期原型和快速测试 |
| `data/scenario_bundles.json` | **场景化组合方案**：定义场景（如“三亚度假”“通勤”“运动训练”）的槽位、触发词、组合模板，用于场景化组合推荐 |
| `data/eval_queries.jsonl` | **评估查询集**：JSONL 格式，每行一个测试用例（如对比、澄清、购物车、品类切换、场景组合），用于离线评估 Agent 行为 |
| `data/product_images/` | **商品主图目录**：100 张商品图片（`p_beauty_001_live.jpg` 到 `p_food_025_live.jpg`），按类目（beauty、clothes、digital、food）各 25 张，用于多模态图片找货和商品卡片展示 |

---

## 四、docs/ — 文档

| 文件 | 作用 |
|------|------|
| `docs/README.md` | （根目录 README）项目总体说明、快速启动指南、环境变量配置 |
| `docs/architecture.md` | **架构设计文档**：主链路流程图、各模块职责、数据流、接口定义，是理解项目的核心文档 |
| `docs/api.md` | **API 接口文档**：`/health`、`/chat`、`/cart`、`/products` 等接口的详细说明、请求/响应格式 |
| `docs/android_setup.md` | **Android 构建与联调指南**：环境要求、Gradle 构建、adb 反向代理、真机调试步骤 |
| `docs/demo_script.md` | **演示脚本**：最小闭环演示步骤、各功能点演示话术、常见问题排查 |
| `docs/progress.md` | **项目进度**：已完成清单和待办清单，记录开发里程碑 |
| `docs/rag_product_maturity.md` | **RAG 产品成熟化设计**：参考 RAG/Self-RAG 论文，规划从 Demo 到生产级的架构演进路线 |
| `docs/retrieval_benchmark_2026-06-07.md` | 检索性能压测报告：本地 JSON fallback 与 Chroma 路径在查询延迟、建库耗时上的对比 |
| `docs/first_token_benchmark_2026-06-07.md` | 首 Token 延迟压测报告：验证 SSE 首屏响应是否在 1 秒内 |
| `docs/esci_small_benchmark_2026-06-08.md` | ESCI Small 数据集基准测试报告 |
| `docs/esci_embedding_eval_2026-06-08.md` | ESCI Small Embedding 评估报告：对比本地、Chroma+hashing、Chroma+Ark 的检索质量 |
| `docs/visual_embedding_pipeline_2026-06-08.md` | 视觉 Embedding Pipeline 设计文档：从 12x12 RGB 签名到多模态向量索引的演进 |
| `docs/engineering_governance_2026-06-09.md` | 工程治理升级记录：模块解耦、降低生产风险的具体改动 |
| `docs/production_upgrade_check_2026-06-08.md` | 生产级升级前检查清单：状态持久化、向量库压测、延迟指标、视觉检索等待补齐项 |
| `docs/first_screen_latency_2026-06-09.md` | 首屏延迟优化记录：emit `thinking` 状态、推荐 LLM 生成移出首屏关键路径 |
| `docs/演讲稿.md` | **技术答辩 20 分钟讲解稿**：逐行代码讲解，覆盖所有答辩高频问题 |
| `docs/retrieval_benchmark_2026-06-07.json` / `-chroma-1k.json` / `-local-50k-r1.json` | 压测原始数据（JSON） |
| `docs/first_token_benchmark_2026-06-07.json` / `first_token_check_current.json` | 首 Token 压测原始数据 |

---

## 五、scripts/ — 脚本工具

| 文件 | 作用 |
|------|------|
| `scripts/run_server.ps1` | **启动后端**：设置 `PYTHONPATH`，启动 `uvicorn` 开发服务器（带 `--reload`） |
| `scripts/test_backend.ps1` | **后端编译测试**：编译所有 server 代码，检查语法错误 |
| `scripts/test_chat.ps1` | **聊天接口测试**：发送测试消息到 `/chat`，验证 SSE 返回 |
| `scripts/check_android_env.ps1` | **Android 环境检查**：检查 JDK、Android SDK、Gradle 等环境是否就绪 |
| `scripts/download_esci_small.ps1` | 下载 ESCI Small 数据集：从 Amazon Science 仓库克隆数据 |
| `scripts/benchmark_retrieval.py` | **检索性能压测**：对比本地 JSON 和 Chroma 在不同商品库规模下的检索延迟和召回率 |
| `scripts/benchmark_first_token.py` | **首 Token 延迟压测**：测量 `/chat` 接口返回第一个 token 的延迟，验证是否满足 1 秒要求 |
| `scripts/evaluate_agent.py` | **Agent 离线评估**：读取 `eval_queries.jsonl`，自动运行测试用例，检查 handler 路由、事件类型、商品命中等 |
| `scripts/evaluate_query_plans.py` | **查询规划回归评测**：读取 `server/eval/query_plan_cases.json`，检查 intent、route、品类、预算、否定和引用解析 |
| `scripts/evaluate_taxonomy.py` | **类目回归评测**：读取 `server/eval/taxonomy_query_cases.json`，验证类目分类准确性 |
| `scripts/evaluate_esci_retrieval.py` | ESCI 检索评估：在 ESCI 数据集上评估检索质量 |
| `scripts/prepare_esci_small.py` | ESCI 数据预处理：将原始 Parquet 转换为项目可用的 JSON 格式 |
| `scripts/prepare_ref_dataset.py` | 参考数据集准备：从外部数据集抽取商品数据，构建 `products_ref.json` |
| `scripts/annotate_product_taxonomy.py` | 商品 Taxonomy 标注：为商品记录自动打 `product_type` 标签 |
| `scripts/taxonomy_version_report.py` | Taxonomy 版本报告：生成类目体系变更审计报告 |
| `scripts/build_visual_embedding_index.py` | 视觉 Embedding 索引构建：为商品图片批量构建向量索引 |
| `scripts/extract_ref_images.py` | 商品主图抽取：从 `ecommerce_agent_dataset_ref.zip` 解压商品图片到 `data/product_images/` |

---

## 六、根目录配置

| 文件 | 作用 |
|------|------|
| `README.md` | 项目主文档：项目定位、目录结构、快速启动、环境配置、测试命令 |
| `.env.example` | 环境变量示例：ARK_API_KEY、USE_CHROMA、USE_LLM、USE_ARK_EMBEDDING 等配置模板 |
| `.env` | （本地创建，不提交 Git）实际环境变量配置，包含真实 API Key |
| `.gitignore` | Git 忽略规则：排除 `.env`、`.venv`、构建产物、日志、SQLite 数据库等 |
| `启动指南.md` | 本地启动步骤汇总：后端、Android、adb 代理的完整启动流程 |
| `3天答辩冲刺计划.md` | 答辩前 3 天任务排期：每天重点阅读的文件和准备的问题 |
| `开发计划_RAG多模态电商导购Agent.md` | 项目开发计划：里程碑、任务分解、技术选型 |
| `答辩PPT大纲_26页.md` | 答辩 PPT 26 页大纲：每页标题和要点 |
| `答辩稿.md` | 完整答辩演讲稿：技术老师/专家答辩的逐字稿 |
| `demo_script.md` | 演示脚本（根目录版）：与 docs/demo_script.md 内容类似 |
| `generate_defense_ppt.py` | 答辩 PPT 生成脚本：根据 `答辩PPT大纲_26页.md` 自动生成 PPT 文件 |
| `开启服务器.txt` | 快速启动命令备忘 |
| `v1_text.txt` / `orig_text.txt` | 原始文本素材（可能是早期文档草稿） |
| `rag-shopping-agent-main.zip` | 项目打包备份 |

---

> 最后更新：基于项目当前代码结构整理。如有新增文件，请同步更新本文档。
