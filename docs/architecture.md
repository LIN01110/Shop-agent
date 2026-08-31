# 架构设计

## 目标

保持可演示闭环稳定，同时把核心能力逐步迁到可配置、可测试、可替换的生产级链路：语义规划、工具执行、检索 pipeline、事实约束和状态持久化分层演进。

## 主链路

```text
Android Compose
  -> OkHttp SSE
  -> FastAPI /chat
  -> Orchestrator
  -> SemanticPlanner
  -> AgentWorkflow
  -> Clarification/Cart/Compare/Scenario/Context/Recommendation Handler
  -> ToolRegistry.search_products
  -> ProductRetrievalPipeline
  -> VectorStore metadata prefilter
  -> PostProcessor
  -> Dedupe/Rerank
  -> Doubao/Ark LLM 或模板 fallback
  -> GroundingGuard
  -> SSE token/guardrail/product_card/comparison_card/cart_update/done
  -> /assets/products 商品主图
  -> /products/{id} 商品详情页
```

## 后端扩展点

- `VectorStore`：当前支持 `LocalJsonVectorStore` 和 `ChromaStore`。未安装 Chroma 或未启用时使用 JSON fallback；设置 `USE_CHROMA=true` 后走 Chroma。检索接口支持 `VectorSearchFilters`，可把 `product_type` 和价格过滤下推到 store。
- `SemanticPlanner`：先把用户自然语言解析为结构化 `SemanticPlan`。LLM 可用时尝试 JSON plan，失败或不可用时走规则 fallback；后端再用 Pydantic 校验和工具执行，避免模型直接改状态。
- 语义规划模块边界：`semantic_schema.py` 定义 plan schema，`semantic_rules.py` 保存规则 fallback，`planning_context.py` 负责把会话历史、候选商品、购物车和 profile 压成固定大小上下文，`semantic_llm.py` 负责 LLM prompt、JSON 抽取和 plan 合并。
- `AgentWorkflow`：当前是轻量内部工作流，按澄清、购物车、对比、上下文追问、普通推荐的优先级分派；后续可替换为 LangChain/LangGraph 类 planner，而不影响 API 和 Android 端协议。
- Handler 模块边界：`workflow.py` 保存上下文和调度协议，`default_handlers.py` 装配默认工作流，`conversation_handlers.py` 处理澄清和上下文追问，`commerce_handlers.py` 处理购物车、对比和组合推荐，`recommendation_handler.py` 处理普通推荐与 GroundingGuard。
- `ToolRegistry`：当前注册 `search_products`、`compare_products` 和 `manage_cart`
- `SessionStore`：当前支持 `memory`、`sqlite` 和 `redis` 三种后端。`memory` 适合本地临时调试；`sqlite` 会把完整 `SessionState` 持久化到 DB，覆盖会话历史、候选商品、pending subject 和购物车；`redis` 适合多进程/多实例共享会话与购物车状态。
- `InputProcessor`：当前支持 `TextProcessor` 和 `MultimodalInputProcessor`；后续可增加 `ASRProcessor` 和真实 `VLMProcessor`。
- `MultimodalInputProcessor`：当前支持图片 base64 输入，通过商品主图视觉签名做相似匹配，把图片转成可检索的文字线索；后续可替换为真实 VLM。
- 输入模块边界：`inputs/base.py` 定义输入协议和文本处理，`inputs/image_similarity.py` 负责本地图片签名索引，`inputs/multimodal.py` 负责把图片线索并入文本查询。
- `ProductRetrievalPipeline`：把商品检索拆成 store 预过滤召回、后处理过滤、去重、轻量 rerank 和 diagnostics。`ProductSearchTool` 只负责把命中转换成商品卡，后续接 BM25/向量混合召回、外部 reranker 或 AB 实验时不需要改 handler。
- `PostProcessor`：当前有 `RangeFilter`、`ProductTypeFilter`、`KeywordFilter`、`ExclusionFilter`。即使 store 已经做 metadata 预过滤，后处理仍作为安全兜底，保证旧索引或 fallback 场景下结果正确。
- `LLMClient`：当前支持 Ark OpenAI-compatible `/chat/completions` 流式接口；`USE_LLM=true` 且 `ARK_API_KEY` 存在时启用。
- `StaticFiles`：当前通过 `/assets/products/{filename}` 服务参考集商品主图。
- `Products API`：当前通过 `/products/{product_id}` 提供本地商品详情 HTML 页。
- SSE 事件：当前支持 `token`、`image_analysis`、`guardrail`、`product_card`、`comparison_card`、`cart_update`、`done`
- `ScenarioCatalog`：场景组合由 `data/scenario_bundles.json` 维护，包括策略状态、灰度比例、负责人、审核日期、触发词、语义词、优先级、槽位、检索模板和结构化过滤条件。Catalog 会综合 trigger terms、semantic terms、slot terms、product type overlap 和 LLM/规则 plan intent 打分，避免只靠单个关键词命中。
- `image_analysis` 事件：当用户上传图片时，返回后端识别到的相似商品摘要和候选匹配。

## Chroma 接入

`server.rag.ingest` 会读取 `data/products_ref.json`，把统一商品文档写入 `server/chroma_db`。默认 embedding 使用本地 hashing embedding，便于离线测试和无 API Key 启动。

如果 `.env` 或环境变量设置 `USE_ARK_EMBEDDING=true` 且存在 `ARK_API_KEY`，Chroma 会使用 `ArkEmbeddingFunction` 调用 Ark OpenAI-compatible `/embeddings` 接口。默认 `EMBEDDING_BATCH_SIZE=4`，贴近方舟文本向量化文档的性能建议。为避免旧 hashing 向量和真实 embedding 维度冲突，真实 embedding 会写入带模型名后缀的独立 collection，例如：

```text
products_ark_embedding_doubao_embedding_text_240515
```

切换 embedding 后需要重新执行：

```powershell
python -m server.rag.ingest
```

## 防幻觉策略

最小闭环阶段先采用程序化约束：

- 回答只引用检索返回的商品卡片
- 检索为空时返回降级话术
- 价格、品牌、落地页来自 metadata，不由模型自由生成

接入大模型后，系统提示词仍要求只能依据检索上下文回答。`RecommendationHandler` 不会直接把 LLM token 原样转发给客户端，而是先完整缓冲回答，再交给 `GroundingGuard` 校验：

- 出现未提供的优惠、折扣、满减、库存、销量、好评率等商业事实时降级。
- 出现候选商品和用户预算以外的价格时降级。
- 没有引用任何候选商品名称或品牌时降级。
- 出现绝对化或医疗化承诺，如“保证”“根治”“100%”时降级。

商品卡会携带结构化 `evidence`，包括商品 ID、名称、品牌、类目、类型、价格和检索分数。`GroundingGuard` 优先使用 `evidence` 作为事实源，缺失时再兼容旧商品卡字段，为后续逐句 citation/fact-check 留出接口。

降级时会发送 `guardrail` SSE 事件并改用确定性模板回答，商品卡片仍来自 metadata。

## 主动澄清与多轮改写

对于过宽泛且缺少预算/偏好的请求，`ClarificationHandler` 会先返回澄清问题，而不是直接检索并硬推商品。例如：

```text
用户：推荐一款手机
助手：你更看重拍照、续航、性能还是性价比？预算大概是多少？
```

澄清时 `done` 事件会带上 `needs_clarification=true` 和 `pending_subject`。用户下一轮回答“拍照优先，预算4000”时，`rewrite_query` 会把上一轮的“手机”补回查询，形成更完整的检索表达。

会话状态会保留上一轮候选商品卡片，`ContextFollowUpHandler` 用它处理轻量上下文追问：

- `第二个怎么样`：直接围绕上一轮第二个候选商品回答，并重新返回该商品卡片。
- `这个适合干皮吗`：把“这个/它/那款”解析为上一轮首个候选商品，基于商品 metadata 给出回答。
- `再便宜点`：根据上一轮候选中的最低价自动追加更低的预算过滤条件，再进入检索。
- `AHC 那个熬夜党能用吗`：通过品牌/名称引用上一轮候选商品，不再只依赖“第二个”这类固定句式。
- `那支给我来两件`：通过结构化 plan 识别隐式加购和数量。
- `有没有比第二款更适合敏感肌但别太贵的`：把“第二款”解析为参考商品，并转换成敏感肌关键词和参考价格上限后重新检索。

## 语义规划层

`server.agent.semantic_llm.SemanticPlanner` 是当前从 Demo 走向成熟 Agent 的关键层。默认使用快速规则 fallback；当 `.env` 或环境变量设置 `USE_SEMANTIC_LLM=true` 且 `USE_LLM=true` 时，会在 `SEMANTIC_LLM_BUDGET_SECONDS` 的短预算内尝试 LLM JSON plan，超时、解析失败或校验失败都会回退规则解析。它输出的 `SemanticPlan` 包括：

- `intent`：recommend / compare / cart / ask_product_detail / clarify / browse
- `cart_action`：add / remove / update_quantity / view / checkout
- `reference_type`：last / ordinal / name / brand / cheapest
- `reference_text`、`reference_index`、`quantity`
- `filters`：keyword / max_price / exclude
- `query` 和 `needs_search`

LLM plan 只作为语义理解建议，所有字段都要经过 Pydantic 校验；业务动作仍由 handler 和 tool 执行。Planner prompt 只读取 `PlanningContext` 的 compact 快照，不直接拼完整 session，避免 prompt 膨胀和敏感运行时字段泄漏。这样比单纯堆正则更有泛化能力，也比让 LLM 直接调用业务动作更安全。

语义规划之后还有一层 `PlannerPolicy` 校验：LLM 可以把自然语言泛化成结构化 plan，但不能越过产品策略。`RuleSignals` 会把规则层抽到的高确定性信息统一成 route signal，例如是否有购物车动作、上下文引用、组合方案信号、复杂偏好、模糊预算、明确单品品类等。`PlannerPolicy` 不再散落地问“有没有某几个词”，而是根据这些信号决定 deterministic / planner / clarification 路径。典型规则包括：明确单品品类和硬预算可走确定性检索；上下文引用、否定偏好、模糊预算和购物车动作进入 planner；单品请求不能因为命中“防晒、通勤、训练”等场景词而升级成组合方案；LLM 不能在缺少规则层购物车证据时直接执行加购、删除、改数量或结算，这类 mutating cart action 会被降级为澄清。

当前仍保留 taxonomy、品牌 alias、数量单位、场景 catalog 等可控词表，这是电商系统必要的结构化基础；长尾表达通过短预算 LLM planner 和 query-level eval 持续补齐，不再靠在 Python 分支中无限堆词。

## 商品对比

`compare_products` 复用 `ProductSearchTool`，但会先解析“X 和 Y / X vs Y / X 与 Y”这类品牌或商品对，再分别检索两侧候选，避免普通 Top-K 只召回一侧品牌。对比场景会保留双方候选用于解释差异；如果用户给出预算，结论会优先推荐预算内商品，同时在 tradeoffs 中标记超预算候选。

对比链路返回三类 SSE：

- `token`：确定性对比回答，关闭 LLM 时也可稳定演示。
- `product_card`：两侧候选商品卡片。
- `comparison_card`：结构化对比数据，Android 端会渲染为对比面板。

## 对话式购物车

`manage_cart` 直接操作 `SessionState.cart`，并复用上一轮 `candidate_product_cards`。用户可以说“把刚才那款加到购物车”“把第二个加到购物车”“删除第一个”“把数量改成 2”“查看购物车”等自然语言指令。每次购物车状态变化后，后端会返回：

- `token`：对购物车动作的自然语言确认。
- `cart_update`：结构化购物车状态，包括商品列表、总件数和总价。
- `done`：本轮结束。

Android 端收到 `cart_update` 后会展示购物车面板；面板中的商品行可点击打开详情弹窗，满足 PDF 中“结构化数据 CRUD 并在客户端实时反馈状态变化”的加分项方向。

每轮对话结束后 Orchestrator 会保存完整 `SessionState`。当 `SESSION_BACKEND=sqlite` 时，同一单实例服务重启后可恢复购物车和上下文候选；当 `SESSION_BACKEND=redis` 时，多个后端进程可共享同一 `session_id` 的状态。当前 Redis 保存 session/cart，不保存 trace；trace 仍需单独迁移到 Redis Stream、数据库或结构化日志。

## Doubao/Ark 生成接入

`server.llm.ark_client.ArkChatClient` 负责调用 Ark OpenAI-compatible chat completions 接口。`server.llm.prompt` 会把检索得到的商品卡片整理为上下文，并在系统提示词中约束：

- 只能推荐候选商品中的商品
- 不编造优惠、库存、销量、功效或外部评价
- 价格、品牌、类目必须来自商品 metadata
- 候选商品不完全匹配时要如实说明

`RecommendationHandler` 会先完成 RAG 检索和后处理，再调用 LLM 生成自然语言回答。如果 LLM 未配置、调用失败或 `GroundingGuard` 判定输出不安全，则回退到本地模板回答，保证 Demo 可用性和事实边界。

## 商品主图链路

`scripts.extract_ref_images` 会从参考数据集 zip 中抽取商品图片到 `data/product_images`。FastAPI 将该目录挂载到 `/assets/products`，`ProductSearchTool` 会把商品 metadata 中的相对图片路径转换为完整 URL，例如：

```text
http://127.0.0.1:8000/assets/products/p_beauty_021_live.jpg
```

真机调试时该 URL 通过 `adb reverse tcp:8000 tcp:8000` 被手机访问。Android 端使用 Coil 加载商品图片，点击商品卡片时打开本地详情弹窗；收到 `comparison_card` 时会展示对比面板。

Android UI 已按职责拆分：`ChatScreen.kt` 保留路由、图片选择器、页面主布局和 preview；`MessageComponents.kt` 负责消息气泡，`MessageComposer.kt` 负责输入栏和图片附件提示，`ProductComponents.kt` 负责商品卡、图片和详情弹窗，`CartPanel.kt` 负责购物车面板，`ComparisonPanel.kt` 负责对比面板。

商品卡片中的 `detail_url` 指向本地商品详情页：

```text
http://127.0.0.1:8000/products/p_beauty_021
```

Android 详情弹窗中的“打开链接”会跳转到该页面，用于满足 Demo 中可点击商品落地页的基础要求。

## NLU 输出契约

`SemanticPlan` 不只输出 `intent`、`filters` 和 `constraints`，还会携带字段级 `confidence_by_field` 与 `evidence`。例如用户输入“推荐一款1000元以上的运动鞋”时，plan 会记录：

- `confidence_by_field.product_type`：来自 taxonomy / profile / embedding classifier 的置信度。
- `evidence.product_type`：标准 `product_type`、展示名、来源和命中的原始 span。
- `confidence_by_field.price` 与 `evidence.price`：预算上下限的结构化来源。
- `confidence_by_field.quantity` 与 `evidence.quantity`：数量、单位、span、解析来源和是否为购物车数量。
- `confidence_by_field.reference`：第一款/第二款/品牌/商品名等上下文引用的确定性。

`Orchestrator` 会把这些内容写入 trace metadata 的 `nlu` 字段。`PlannerPolicy` 对购物车写操作采用更严格策略：加购、删除、改数量、结算必须有明确动作、可解析商品引用和足够置信度；缺少证据时会降级为澄清，而不是直接修改购物车状态。

## 多模态图片找货

`POST /chat` 可携带 `image_base64`、`image_mime_type` 和 `image_filename`。后端会：

1. 解码图片并计算 12x12 RGB 视觉签名。
2. 与 `data/product_images` 中的商品主图签名做相似度比较。
3. 生成“图片线索”，例如“上传图片最像商品 X，品牌 Y，类目 Z”。
4. 把图片线索拼入查询，再进入同一个 `SemanticPlanner -> AgentWorkflow -> ProductSearchTool`。

这不是完整 VLM，但是真实可运行的轻量多模态链路，适合当前 100 条参考数据集的 Demo。后续接入视觉模型时，只需替换 `MultimodalInputProcessor` 的图片摘要生成部分。

## 场景化组合推荐

`ScenarioBundleHandler` 不再把场景写死在 Python 里，而是加载 `data/scenario_bundles.json`。当前配置覆盖：

- 三亚/海边/度假/旅行：防晒保护、轻便上衣、舒适出行、拍照记录。
- 通勤/上班/办公室：通勤背包、降噪耳机、轻办公设备。
- 健身/训练/跑步装备：训练跑鞋、运动裤、速干上衣、运动耳机。
- 通用“搭配一套/组合推荐/全套”：护理、穿搭和数码辅助。

每个槽位都复用结构化 `SearchFilters`，商品卡仍来自检索结果，不由模型编造。

配置中的每个槽位包含 `query_template`、`filters` 和 `max_items`。例如通用组合方案会用 `{message}` 作为模板变量，把用户原话并入槽位检索；新增“露营/开学/新家”等场景时，只需要扩展 JSON 并补评估样例。

组合执行时不会再机械地“每个槽位取第一个商品”。`ScenarioBundleHandler` 会为每个槽位取候选池，交给 `BundleOptimizer` 在总预算、商品去重和槽位完整度之间做选择；低预算下可裁剪 optional slot，例如三亚方案中的“拍照记录”会在预算不足或用户未提及时被跳过。策略命中的 bundle id、catalog version、confidence、signals、预算与选品结果会写入 trace metadata，用于后续失败样例回流、灰度分析和策略复盘。
