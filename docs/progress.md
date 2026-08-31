# 项目进度

更新时间：2026-06-08

## 已完成

- [x] 建立 Git 仓库和 monorepo 目录结构
- [x] 配置 `.gitignore`，排除 `.env`、PDF、构建产物、日志和缓存
- [x] FastAPI 后端骨架
- [x] `/health` 健康检查
- [x] `/chat` SSE 流式接口
- [x] SSE 事件：`token` / `product_card` / `done`
- [x] 本地 JSON 商品检索 fallback
- [x] 商品预算过滤
- [x] 反选/排除过滤
- [x] SessionState 会话状态结构
- [x] ToolRegistry 工具注册结构
- [x] VectorStore / InputProcessor / PostProcessor 扩展接口
- [x] Android Kotlin + Compose 原生客户端骨架
- [x] Android SSE 网络客户端
- [x] Android 对话 UI、输入框、流式消息气泡
- [x] Android 商品卡片展示和点击跳转
- [x] Android 商品卡片主图展示
- [x] Android 商品详情弹窗
- [x] 本地商品详情页 `/products/{product_id}`
- [x] 后端 pytest 测试
- [x] 后端联调测试脚本
- [x] Android 环境检查脚本
- [x] README、架构文档、API 文档、Demo 脚本
- [x] Android Studio、Android SDK、Platform Tools、API 35 安装到 D 盘
- [x] 真机识别与 `adb reverse tcp:8000 tcp:8000`
- [x] 真机端到端 Demo 跑通：输入推荐需求后返回 PureLab 商品卡片
- [x] 接入参考数据集 zip，清洗生成 100 条正式商品数据
- [x] 后端默认数据源切换为 `data/products_ref.json`
- [x] 增加关键词后处理，减少跨类目误召回
- [x] ChromaStore 适配层
- [x] Chroma 灌库脚本 `python -m server.rag.ingest`
- [x] `USE_CHROMA=true` 可启用 Chroma，默认保留 JSON fallback
- [x] Ark/Doubao embedding 适配：`USE_ARK_EMBEDDING=true` 时 Chroma 使用 Ark `/embeddings`
- [x] embedding collection 隔离：真实 embedding 使用模型名后缀 collection，避免与本地 hashing 向量维度冲突
- [x] 已完成 Git 提交，当前最新提交覆盖：MVP、Chroma、反选约束、Doubao/Ark、商品主图、商品详情页
- [x] 严格关键词过滤：无严格匹配时降级说明，不返回无关商品
- [x] 一句话多重反选解析：如“不要含酒精的，也不要日系品牌”
- [x] Doubao/Ark LLM 客户端接入：`USE_LLM=true` 时基于 RAG 商品上下文生成回答
- [x] LLM 调用失败或未配置 API Key 时自动回退本地模板回答
- [x] 防幻觉 prompt：约束只基于候选商品回答，不编造价格、优惠、库存或功效
- [x] `GroundingGuard` 回答后校验：LLM 输出先缓冲，拦截未提供的优惠/库存/销量、候选外价格、缺少候选引用和绝对化功效后降级为 grounded 模板回答
- [x] 从参考集 zip 抽取 100 张商品主图到 `data/product_images`
- [x] FastAPI 通过 `/assets/products/...` 提供商品主图静态资源
- [x] 商品卡片返回完整 `detail_url`，支持跳转本地商品详情页
- [x] 主动澄清：如“推荐一款手机”会先追问拍照/续航/性能/性价比和预算
- [x] 多轮改写补全：澄清后的下一轮会把 `pending_subject` 补回查询
- [x] 上下文追问增强：支持“第二个怎么样”“这个适合吗”“再便宜点”等轻量跟进
- [x] 预算解析增强：支持“预算4000”这类预算前置表达，并继续由程序化价格过滤执行
- [x] 商品对比：识别“X 和 Y / X vs Y / X 与 Y”，分别检索双方候选并返回 `comparison_card`
- [x] 对比预算处理：对比时保留双方候选解释差异，推荐结论优先选择预算内商品
- [x] Android 端渲染 `comparison_card`：展示推荐结论、关注点、双方优势和取舍
- [x] 对话式购物车：支持加购、查看、删除、修改数量和模拟下单确认
- [x] Android 端渲染 `cart_update`：展示购物车总件数、总价和最近商品，商品行可点击打开详情
- [x] 后端智能体工程化拆分：Orchestrator 收窄为预处理和调度层，具体任务由 `AgentWorkflow` 和 handlers 承接
- [x] 外部 RAG/Agent 成熟化参考梳理：新增 `docs/rag_product_maturity.md`
- [x] 新增 `SemanticPlanner`：规则 fallback 默认启用，`USE_SEMANTIC_LLM=true` 时支持 LLM JSON plan + Pydantic 校验
- [x] 上下文泛化增强：支持品牌/名称引用、隐式加购数量、候选商品排除、参考价格继续检索
- [x] Agent trace：每轮记录 plan、handler、query、filters、事件计数、商品 ID、购物车数量和耗时
- [x] 调试接口：`GET /debug/traces` 和 `GET /debug/traces/{trace_id}`
- [x] 离线评估集和脚本：`data/eval_queries.jsonl`、`python scripts/evaluate_agent.py`
- [x] 商品 taxonomy/facet 过滤：`data/product_taxonomy.json` 维护标准 `product_type`，支持别名和组合触发词，检索文档加载时写入类型元数据，`运动鞋/跑鞋/跑步鞋/跑步的鞋` 不再混入运动裤、短裤等同大类商品
- [x] 同 facet 多选 OR：`运动鞋或运动裤` 会返回鞋或裤，不要求一个商品同时满足两个类型
- [x] 会话过滤生命周期：用户显式切换主商品类型时，会清理旧品类、旧关键词、旧预算和旧排除条件，避免“眼霜 -> 手机”这类长会话污染
- [x] 大库检索优化：`VectorStore.query` 支持 `VectorSearchFilters`，本地 JSON fallback 使用商品类型倒排索引、文档 token/title 特征缓存和 Top-K 堆选择，Chroma metadata 写入类型布尔标记并支持 `where` 下推
- [x] 性能压测脚本：`python scripts\benchmark_retrieval.py --store local|chroma --sizes ... --runs ... --output ...`
- [x] 性能压测报告：`docs/retrieval_benchmark_2026-06-07.md`
- [x] 轻量多模态图片找货：Android 可选择图片，`/chat` 接收 base64 图片，后端基于商品主图视觉签名做同款/相似商品线索转换
- [x] 场景化组合推荐：`ScenarioBundleHandler` 支持三亚度假、通勤、运动训练和通用组合方案，跨类目返回商品卡片
- [x] 场景组合配置化：`data/scenario_bundles.json` 维护场景触发词、优先级、槽位、检索模板和过滤条件；代码层通过 `ScenarioCatalog` 校验加载，不再把新增场景写成 Python 分支
- [x] 场景路由多信号化：`ScenarioCatalog` 综合 trigger terms、semantic terms、slot terms、product type overlap 和 plan intent 打分；product type/slot overlap 只能加分，不能单独把“推荐运动鞋”误判为整套运动方案
- [x] 场景策略治理雏形：每个 bundle 支持 `status`、`rollout_percentage`、`owner`、`reviewed_at`，trace 会记录 bundle id、catalog version、confidence、signals、预算和选品结果
- [x] 动态组合规划：slot 支持 `optional`、`min_budget`、`candidate_pool_size`、`budget_weight`、`match_terms`，可根据预算和用户表达裁剪槽位
- [x] 组合级优化：新增 `BundleOptimizer`，在总预算、商品去重和槽位完整度约束下从每槽位候选池中选择组合，不再每槽位固定取 Top1
- [x] 检索 pipeline 工程化：新增 `ProductRetrievalPipeline`，统一 store 预过滤召回、后处理过滤、去重、轻量 rerank 和 diagnostics；`ProductSearchTool` 收缩为卡片转换层
- [x] 商品卡 evidence：`product_card` 增加后端可用的结构化 `evidence` 字段，记录商品 ID、品牌、价格、类目、类型和检索分数，为后续 citation/fact-check 做准备
- [x] Citation/fact-check 雏形：`GroundingGuard` 返回商品级 citations，并优先使用 `product_card.evidence` 作为事实源；grounding 结果写入 trace metadata
- [x] ESCI small 外部公开数据集接入：新增下载、转换、检索评测脚本，在 5549 商品 / 300 查询上完成 Recall@K、MRR@K、NDCG@K 和延迟评测
- [x] 首 Token 优化与压测：推荐/组合链路先发即时 token，新增 `scripts/benchmark_first_token.py`
- [x] 首 Token 压测报告：`docs/first_token_benchmark_2026-06-07.md`
- [x] 生产配置隔离：`APP_ENV=production` 默认关闭 `/debug`，CORS 白名单、debug 开关、session/trace 容量通过环境变量配置
- [x] 会话容量治理：内存 `SessionStore` 增加 TTL 和最大会话数，避免本地服务长时间运行时无限增长
- [x] SQLite 会话持久化：`SESSION_BACKEND=sqlite` 时保存完整 `SessionState`，服务重启后可恢复购物车、候选商品和上下文
- [x] Redis 会话后端：`SESSION_BACKEND=redis` 时通过 `SESSION_REDIS_URL` 共享会话和购物车状态，支持 TTL 和最大 session 数淘汰
- [x] 后端模块瘦身：`handlers.py` 和 `inputs/processors.py` 收缩为兼容导出层，具体 handler、workflow、多模态和图片相似索引按职责拆分
- [x] 语义规划模块瘦身：`semantic.py` 收缩为兼容导出层，schema、规则 fallback、LLM prompt/解析和 plan 合并按职责拆分
- [x] Android UI 模块瘦身：`ChatScreen.kt` 收缩为页面容器，消息、输入栏、商品卡、购物车和对比面板拆成独立 Compose 文件

## 已验证

- [x] `python -m compileall server`
- [x] `python -m pytest server/tests -q`
- [x] `/chat` 能返回流式 token 和商品卡片
- [x] 空检索结果会降级说明，不返回虚假商品卡片
- [x] Android Studio Gradle Sync 成功
- [x] 小米真机安装成功
- [x] 手机 App 输入后能通过后端返回 PureLab 商品结果
- [x] 参考集查询验证：保湿眼霜可返回科颜氏/AHC 商品卡片
- [x] PDF 典型多轮场景回归：跑鞋 -> 轻量 -> 预算 500 以内，无严格匹配时不返回食品等无关商品
- [x] LLM mock 测试：正常流式生成、失败回退、无商品时跳过 LLM
- [x] Guardrail 回归：LLM 编造优惠券或候选外价格时，不向端侧流出不安全回答，改发 `guardrail` 并降级
- [x] Doubao-Seed 真实接口联调完成：`doubao-seed-2-0-lite-260215`
- [x] 静态图片接口验证：`/assets/products/p_beauty_021_live.jpg`
- [x] 商品详情页接口验证：`/products/p_beauty_021`
- [x] Android Debug 构建和真机安装成功
- [x] Ark embedding mock 测试：OpenAI-compatible `/embeddings` 请求、batching、index 顺序解析
- [x] 主动澄清回归：宽泛手机推荐不返回商品卡片，`done` 返回 `needs_clarification=true`
- [x] 多轮预算回归：手机 -> 拍照优先，预算4000，只返回 4000 元以内商品卡片
- [x] 上下文追问回归：推荐后问“第二个怎么样”会围绕上一轮第二个商品回答
- [x] 上下文价格回归：推荐后问“再便宜点”会追加低于上一轮候选的预算过滤
- [x] 商品对比回归：科颜氏 vs AHC 眼霜返回两侧商品卡和 `comparison_card`
- [x] 对比预算回归：小米 vs OPPO 拍照手机预算 4000 时，结论推荐预算内商品
- [x] 购物车回归：推荐商品后说“把刚才那款加到购物车”会返回 `cart_update`
- [x] Android Kotlin 编译验证：`:app:compileDebugKotlin`
- [x] AgentWorkflow 关键回归：澄清、LLM fallback、商品对比、上下文追问和购物车测试通过
- [x] SemanticPlanner 回归：`AHC 那个`、`那支来两件`、`科颜氏先不要了`、`比第二款更适合敏感肌但别太贵` 测试通过
- [x] Trace API 回归：`/debug/traces` 可返回最近一次 handler、plan 和结构化事件摘要
- [x] 运动鞋回归：`推荐一款运动鞋` 触发 `clothes.sports_shoes` 类型过滤，只返回鞋类商品，不返回运动裤
- [x] 跨品类状态回归：眼霜推荐后再问手机，会清理旧眼霜/预算过滤并重新触发手机澄清
- [x] 离线评估回归：当前 `data/eval_queries.jsonl` 12/12 turns 通过
- [x] 检索压测冒烟：本地 fallback 合成 50k 商品库查询约 16-130ms，Chroma 合成 1k 商品库查询约 33-178ms，报告可输出为 JSON
- [x] 多模态回归：上传参考商品主图可返回 `image_analysis` 和对应商品卡片
- [x] 场景组合回归：三亚度假方案返回防晒、穿搭、出行等组合槽位和商品卡片
- [x] 首 Token 回归：本地 8001 服务 4 个典型场景 P95 首 token 约 318-342ms，低于 1s 阈值
- [x] ESCI small 检索评测：Top10 Recall@K=0.4737、MRR@K=0.8714、NDCG@K=0.7406、P95 latency=51.89ms

## 当前状态

最小闭环已经跑通：

```text
Android 真机 App
  -> OkHttp SSE
  -> adb reverse
  -> FastAPI /chat
  -> MultimodalInputProcessor
  -> SemanticPlanner
  -> AgentWorkflow
  -> ProductRetrievalPipeline
  -> JSON/Chroma 商品检索
  -> Doubao-Seed grounded answer
  -> GroundingGuard
  -> Agent trace / offline eval
  -> 流式 token + image_analysis + guardrail + product_card + comparison_card + cart_update
  -> 手机端展示商品主图卡片、对比面板、购物车面板、图片上传入口和详情弹窗
```

## 当前限制

- [ ] Ark/Doubao embedding 代码已接入，但默认关闭；需要 `USE_ARK_EMBEDDING=true` 并重新灌库后才走真实向量
- [ ] 真实 embedding 灌库尚未做端到端计费接口回归，当前自动化测试使用 mock，避免测试阶段触发外部调用
- [ ] 本地 fallback 50k 查询延迟可接受，但建索引约 72 秒；生产大库应使用持久化 Chroma，避免每次启动重建
- [ ] Chroma metadata where 已完成 1k 冒烟压测；真实生产规模仍需对 Chroma + 真实 embedding + metadata where 做 10k/50k 端到端压测
- [ ] 多轮对话已支持澄清主题补全、商品指代和轻量价格追问，但还不是完整长期记忆
- [ ] 购物车已支持本地模拟闭环，但尚未接真实支付、地址或订单系统
- [ ] 当前多模态是本地图片签名相似检索，适合 Demo；真实拍照找货仍需接 VLM/CLIP 类视觉语义模型
- [ ] 首 Token 已有脚本和即时 token 优化；真实 LLM 开启后的首 Token 仍需在目标服务器上持续压测
- [ ] `GroundingGuard` 已覆盖价格、优惠、库存、销量和绝对化表述；商品卡已携带结构化 `evidence` 并生成商品级 citations，后续仍需扩展为真正逐句 citation/fact-check
- [ ] 策略治理目前是 JSON 配置 + trace metadata，尚未接配置后台、审批流、线上 A/B 平台或自动失败样例回流
- [ ] 商品详情页仍是本地模拟页，尚未接真实电商落地页
- [ ] session/cart 已支持 SQLite 和 Redis 后端；trace 仍是单进程内存态，生产多实例部署前应迁移到 Redis Stream、外部数据库或结构化日志系统

## 下一步

1. 按需打开 `USE_CHROMA=true` 和 `USE_ARK_EMBEDDING=true` 做一次真实 embedding 灌库回归，并用 `benchmark_retrieval.py --store chroma` 对 10k/50k Chroma 路径做压测。
2. 继续完善 taxonomy 覆盖面，把更多商品类型、品牌和功效词沉淀为可配置元数据，并为 taxonomy 变更建立索引重灌/回归提醒。
3. 继续完善多轮查询改写，覆盖更多类目和偏好组合。
4. 扩充离线评估集，覆盖更多类目、失败样例、长链多轮和购物车边界条件。
5. 基于现有 `ProductRetrievalPipeline` 深入实现更成熟的 hybrid retrieval/rerank，或从真实 VLM/CLIP、真实 embedding 灌库回归、配置后台/灰度平台、Demo 录屏中选择 1 个方向深入实现。
6. 做 Demo 脚本和答辩截图/录屏材料。
7. 后续如有真实商品落地页，再替换当前本地模拟页 URL。
