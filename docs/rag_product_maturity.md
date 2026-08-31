# RAG 产品成熟化设计

更新时间：2026-06-08

本项目不只按课题 PDF 的最小链路推进，而是参考成熟 RAG / Agent 系统的共性做法，逐步把 Demo 改造成可持续扩展的产品架构。

## 外部参考

- RAG 原始论文：RAG 的核心价值是把参数化模型和外部非参数化知识库结合，降低只靠模型参数回答带来的事实性问题。参考：https://arxiv.org/abs/2005.11401
- Self-RAG：成熟 RAG 不应无条件固定检索，而应判断是否需要检索、检索是否有用，并对生成结果做自我检查。参考：https://arxiv.org/abs/2310.11511
- LangGraph：成熟 Agent 应有明确的状态、节点、工具和可观测工作流，而不是把所有逻辑塞进一个函数。参考：https://docs.langchain.com/oss/python/langgraph/overview
- LlamaIndex Query Transformations：复杂用户问题需要查询改写、分解或转换，再进入检索。参考：https://developers.llamaindex.ai/python/framework/optimizing/advanced_retrieval/query_transformations/
- OpenAI Structured Outputs：让模型输出符合 JSON Schema 的结构化结果，比让模型直接自由决定业务动作更可靠。参考：https://platform.openai.com/docs/guides/structured-outputs
- RAGAS：成熟 RAG 需要可评估指标，例如 faithfulness、answer relevancy、context precision/recall 等。参考：https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- RAGFlow / Dify：成熟 RAG 产品普遍把 ingestion、retrieval、agent workflow、observability 拆成可独立演进的模块，而不是只靠一个 prompt。参考：https://github.com/infiniflow/ragflow 、https://github.com/langgenius/dify
- Algolia / Typesense 电商搜索：商品类目、品牌、价格等应建成 facet/filter 元数据，让用户查询先落到稳定属性，再做召回和排序。参考：https://www.algolia.com/doc/guides/managing-results/refine-results/faceting 、https://typesense.org/docs/28.0/api/search.html#facet-results

## 本项目采用的原则

1. **语义理解结构化**
   - 用户自然语言先进入 `SemanticPlanner`。
   - 默认走快速规则 fallback，保证演示延迟稳定。
   - 如果 `USE_SEMANTIC_LLM=true` 且 LLM 可用，再让模型输出结构化 JSON plan。
   - 后端用 Pydantic schema 校验 plan，失败时回退规则解析。
   - 模型只负责理解意图，不直接修改购物车、不直接编商品事实。

2. **工具执行可控**
   - `AgentWorkflow` 根据 plan 选择 handler。
   - `ToolRegistry` 执行检索、对比、购物车等真实动作。
   - 商品 ID、价格、品牌、详情页和购物车状态都来自后端数据，不由模型自由生成。

3. **上下文引用泛化**
   - 支持序号引用：`第二个怎么样`、`第2款`。
   - 支持品牌/名称引用：`AHC 那个`、`科颜氏先不要了`。
   - 支持自然指代：`那支给我来两件`、`便宜的那个`。
   - 支持参考商品继续检索：`有没有比第二款更适合敏感肌但别太贵的`。

4. **检索前查询增强**
   - 宽泛需求先澄清。
   - 澄清后的下一轮会补回 pending subject。
   - 多轮上下文会转换成明确过滤条件，如 max_price、keyword、exclude。
   - 商品类型走可配置 taxonomy：`data/product_taxonomy.json` 维护标准 `product_type`、别名和匹配字段，检索文档加载时会把类型写入元数据。
   - `product_type` 作为 facet/filter 使用，先约束“鞋/裤/手机”等硬类目，再让向量或关键词召回处理偏好词，避免同大类商品互相污染。
   - taxonomy 支持 `compound_aliases`，例如 `["跑步", "鞋"]` 可覆盖“适合跑步的鞋”这类自然表达。
   - 同一 facet 内多个 `product_type` 按 OR 处理，例如“运动鞋或运动裤”会返回鞋或裤；不同过滤维度之间仍按 AND 收紧。
   - 会话状态有 product scope 生命周期：当用户显式切换到新的 `product_type` 时，旧品类、旧关键词、旧预算和旧排除条件会清理；没有新 `product_type` 的追问继续沿用上下文。

5. **Grounded 生成**
   - LLM 只基于候选商品生成回答。
   - 检索为空时明确降级，不返回虚假商品卡。
   - 商品卡和对比卡始终以结构化 SSE 发送给 Android。
   - 推荐回答不再直接流出 LLM token；后端会先缓冲完整 LLM 输出，通过 `GroundingGuard` 校验后再流式发送。
   - `GroundingGuard` 会拦截未提供的优惠/库存/销量、候选外价格、缺少候选引用和绝对化功效，触发 `guardrail` 事件并降级为确定性 grounded 模板。

6. **可评估可回归**
   - 每次能力增强都加入 pytest 场景。
   - 当前测试覆盖澄清、对比、购物车、上下文追问、语义 planner、LLM fallback 和 API SSE。
   - 当前已加入 `data/eval_queries.jsonl` 和 `scripts/evaluate_agent.py`，用于回归 handler、事件、商品 ID、购物车数量和过滤条件。
   - 下一阶段可按 RAGAS 风格继续扩展 faithfulness、answer relevancy 和 context precision。

## 当前已落地

```text
Android App
  -> /chat SSE
  -> Orchestrator
  -> SemanticPlanner
     -> LLM JSON plan 或规则 fallback
     -> Pydantic 校验
  -> AgentWorkflow
     -> ClarificationHandler
     -> CartHandler
     -> CompareHandler
     -> ContextFollowUpHandler
     -> RecommendationHandler
  -> ToolRegistry
  -> Taxonomy filters / Search / Compare / Cart
  -> Grounded response + structured SSE events
  -> Agent trace
  -> Offline eval cases
```

## 可观测与评估

成熟 RAG 产品不能只依赖“看起来回答不错”。当前新增了两层质量闭环：

1. **在线 trace**
   - 每轮 `/chat` 后端会记录一条 `AgentTrace`。
   - trace 包括 `SemanticPlan`、选中的 handler、query、filters、事件计数、商品 ID、购物车数量和耗时。
   - 可通过 `/debug/traces` 和 `/debug/traces/{trace_id}` 查看。

2. **离线评估**
   - `data/eval_queries.jsonl` 保存多轮测试用例和期望结果。
   - `python scripts/evaluate_agent.py` 会逐条执行本地 Agent，并检查 handler、事件、商品、文本和过滤条件是否命中。
   - 这相当于轻量版产品质量看板，后续每次改 planner、检索或购物车，都可以先跑评估，避免能力回退。

## 下一阶段产品化路线

1. **Retrieval Quality**
   - 真实 embedding 灌库回归。
   - `VectorStore.query` 接收结构化 `VectorSearchFilters`；本地 JSON fallback 先按商品类型倒排索引缩小候选，再用缓存的 token/title 特征打分。
   - Chroma metadata 写入 `product_type` 布尔标记和价格字段，查询时可用 `where` 下推硬过滤；collection 名包含索引 schema 版本，避免旧索引和新 metadata 规则混用。
   - `scripts/benchmark_retrieval.py` 可生成合成 1k/10k/50k/100k 商品库，支持 local/Chroma 两种 store，输出 P50/P95/平均延迟、卡片数量、合成数据耗时和建库耗时。
   - 当前基准记录见 `docs/retrieval_benchmark_2026-06-07.md`：本地 fallback 50k 查询约 16-130ms，但建索引约 72 秒；Chroma 1k 查询约 33-178ms，后续应在真实 embedding + 持久化 Chroma 上扩展到 10k/50k。
   - 加入 rerank 层，解决 Top-K 只靠初始召回的问题。
   - 针对商品类型、类目、品牌、价格、功效词做混合检索；商品类型继续按 taxonomy/facet 维护，不写进分散业务分支。
   - 为 taxonomy 增加版本号和索引重灌提醒，避免线上商品元数据与过滤规则漂移。

2. **Planner Quality**
   - 为 `SemanticPlanner` 增加 few-shot 示例。
   - 记录 plan、工具调用、检索结果和最终回答，方便定位错误。
   - 对低置信度 plan 主动澄清，而不是强行执行。

3. **Evaluation**
   - 扩充 `data/eval_queries.jsonl`，覆盖更多类目、失败场景和长链多轮。
   - 输出 query success rate、cart action accuracy、grounding consistency。
   - 增加 LLM-as-judge 或 RAGAS 指标作为人工回归之外的辅助信号。
   - 将跨品类切换、旧过滤条件清理和澄清恢复作为长期回归项。

4. **Product Experience**
   - 增加商品详情页更完整信息。
   - 购物车支持清空、继续推荐搭配商品。
   - Android 端展示“当前理解到的条件”，让用户可修改。

5. **Multimodal**
   - 已接入轻量图片输入：Android 选择图片后随 `/chat` 上传，后端用商品主图视觉签名做相似匹配。
   - 图片匹配只生成结构化视觉线索，例如“最像商品 X / 类目 Y / 类型 Z”，再进入同一个 `SemanticPlanner`。
   - 图片理解结果仍不能直接生成商品事实，必须通过商品库检索验证。
   - 后续可将视觉签名替换为 VLM/CLIP embedding，而不改变 AgentWorkflow 和端侧协议。

6. **Scenario Bundle**
   - 已加入 `ScenarioBundleHandler`，将“三亚度假/通勤/运动训练/搭配一套”等需求拆成多个商品槽位。
   - 每个槽位独立检索和过滤，再合并为组合方案，避免让 LLM 直接编一套不存在的商品组合。
   - 场景定义已迁到 `data/scenario_bundles.json`，由 `ScenarioCatalog` 加载和校验；新增场景应走配置、评估样例和回归测试，而不是继续堆 Python 分支。
   - 商品检索已收敛到 `ProductRetrievalPipeline`：store 预过滤召回、后处理过滤、去重、轻量 rerank 和 diagnostics 独立于 handler，便于后续替换为 hybrid retrieval 或外部 reranker。
   - 场景路由已从纯 trigger term 升级为多信号打分：trigger terms、semantic terms、slot terms、product type overlap 和 plan intent 共同决定是否进入组合方案。
   - 组合推荐已加入 `BundleOptimizer`，按总预算、去重和槽位完整度选择跨槽位商品，低预算下可以裁剪 optional slot。
   - 策略治理目前落在 JSON 字段和 trace metadata：`status`、`rollout_percentage`、`owner`、`reviewed_at`、bundle id、catalog version、confidence、signals 和预算结果都会被记录。

7. **Latency**
   - 推荐和组合链路先发即时 token，降低用户等待感。
   - `scripts/benchmark_first_token.py` 可对 `/chat` 做正式首 Token 压测，默认阈值 1000ms。
