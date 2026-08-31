# Shop_Agent Benchmark 评测报告

> 生成时间：2026-08-13（第二轮全量复测）
> 数据口径：商品库 100 SKU（4 大类，products_ref.json 由 100 个类目文档重新生成，76 条更新）；
> 检索 ground truth 为 82 条规则自动标注查询；LLM 相关评测使用 DeepSeek API（deepseek-v4-flash）；
> 幻觉判定为三层幻觉守卫（hallucination_guard.py）Layer1+Layer2 规则集自动判定（与 Guard 同规则集）。
> 本轮变更：生产配置开关全量打开（BM25/混合检索/CE/反馈闭环/输入守卫/幻觉守卫/序号引用/质量监控），
> /chat 已接入 DeepSeek 真实生成（RECOMMENDATION_LLM_BUDGET_SECONDS=30）。

## 1. 检索质量（82 条规则标注查询）

| 配置 | Recall@5 | Recall@10 | NDCG@10 | MRR |
|---|---|---|---|---|
| 向量库裸召回 | 85.7% | 93.6% | 93.1% | 93.7% |
| 管道+重排序 | 85.7% | 93.6% | 93.1% | 93.7% |
| 管道+NLU过滤（生产路径） | 89.3% | 95.3% | 97.5% | 98.2% |

生产路径相对裸召回：Recall@10 93.6%→95.3%，NDCG@10 93.1%→97.5%（相对提升 4.7%），MRR 93.7%→98.2%。

## 2. 混合检索消融（82 条规则标注查询，含 Cross-Encoder 臂）

| 配置 | Recall@10 | NDCG@10 | MRR |
|---|---|---|---|
| 仅向量召回 | 93.6% | 93.1% | 93.7% |
| 仅 BM25 召回 | 92.4% | 90.7% | 91.2% |
| 向量 + BM25（RRF 融合，k=60） | 94.3% | 93.8% | 95.0% |
| RRF + Cross-Encoder 精排（bge-reranker-base，Top-20 候选） | 93.2% | 92.5% | 94.7% |
| 混合召回 + 管道 + NLU 过滤（生产路径） | 95.3% | 97.5% | 98.2% |

结论：RRF 融合双路召回三项指标均优于任一单路；CE 精排在本目录（100 SKU 短文本、规则标注）
上未带来额外增益且引入百毫秒级推理开销，故生产中作为可选模块默认关闭（配置化插拔）。
注：本轮修复了 CrossEncoderReranker 的模型加载 bug（bge-reranker 必须用
AutoModelForSequenceClassification 加载才能拿到 classifier 打分头；原实现用 AutoModel +
批内 [CLS] 余弦相似度，分数无意义），消融使用生产优先的 SimpleCrossEncoderReranker
（sentence-transformers CrossEncoder，分类 logit 打分）。

## 3. 延迟（/chat 已走 DeepSeek 真实生成 + 三层幻觉守卫）

| 环节 | P50 | P90 | P99 |
|---|---|---|---|
| 检索管道（进程内，100 SKU） | 0.26ms | — | 4.4ms |
| /chat 首事件 TTFB（SSE） | 5.29ms | — | — |
| /chat 完整流（DeepSeek 生成路径） | 3650.4ms | — | — |
| DeepSeek 生成（非流式对照，~256 tokens） | 3569.2ms | — | — |

结论：检索与流式首事件均为毫秒级；完整流耗时由 LLM 推理主导（与 DeepSeek 非流式对照基本一致），
系统自身开销可忽略。实测 /chat 输出使用【候选N】序号引用真实商品与价格，幻觉守卫放行。

## 4. 幻觉抑制（24 个导购问题，DeepSeek 三臂对比，新三层守卫）

| 实验组 | 违规率 |
|---|---|
| 纯 LLM（无商品上下文） | 54.2% |
| RAG（检索卡片注入） | 8.3% |
| RAG + 三层幻觉守卫（最终输出） | 0.0% |

守卫处置分布：22 例直接放行，2 例数值错误自动修正（auto_correct），0 例需要重生成/降级。
RAG 组违规类型均为 price_mismatch（价格与候选卡片不一致），被 Layer 2 属性事实校验拦截并自动改回真实价格。
判定口径：Layer1 商品存在性 + Layer2 属性事实（价格/库存精确比对），与 Guard 同规则集。

## 5. LLM 结构化 JSON 输出（40 个商品样本，DeepSeek）

| 策略 | 解析成功率 | 字段完整率 | 类型合规率 |
|---|---|---|---|
| 普通提示词 | 97.5% | 97.5% | 97.5% |
| Schema 约束 + JSON mode | 100.0% | 100.0% | 100.0% |

注：DeepSeek API 无视觉模型，本项评测文本 LLM 的结构化输出稳定性，对应结构化 JSON 输出方案的工程价值。

## 6. Agent 路由评估（evaluate_agent.py，9 个多轮用例）

- 用例通过：8/9
- 轮次通过：11/13（84.6%）
- 失败用例：product_scope_switch_clears_old_filters（切换商品范围时旧过滤词未按预期清理）

## 7. 检索参数官方评测（eval_retrieval_params.py，14 个边界用例）

| 参数组 | 覆盖率 | Top1 准确率 | P50 | P95 | P99 |
|---|---|---|---|---|---|
| 旧默认 10/50 | 100.0% | 57.1% | 2.4ms | 10.4ms | 20.2ms |
| 新默认 5/20（当前） | 100.0% | 57.1% | 1.7ms | 2.0ms | 2.1ms |
| 保守 5/50 | 100.0% | 57.1% | 2.2ms | 2.6ms | 2.6ms |

## 8. ESCI 公开数据集检索评测（Amazon Shopping Queries Dataset）

数据口径：tasksource/esci（Hugging Face 镜像，Amazon ESCI 官方 joined 预处理版）test 分片，
us locale + small_version=1（ESCI-S），采样 300 条测试查询 / 2,583 个商品（seed 42，含人工标注的困难负样本）。

| 检索链路 | Recall@10 | NDCG@10 | MRR | P50 延迟 |
|---|---|---|---|---|
| 本地词法检索（LocalJsonVectorStore） | 55.3% | 55.5% | 65.2% | 19.0ms |
| Chroma + 哈希嵌入 | 53.4% | 54.8% | 66.4% | 202.1ms |

分标签召回（词法链路）：Exact 48.1%、Substitute 42.4%（E/S 为相关标签；Irrelevant 误召率 20.2%）。
注：哈希嵌入为无外部依赖的占位实现，非语义向量模型，绝对值不代表语义检索上限；
本项意义在于检索链路在公开人工标注数据集上端到端可复现（scripts/prepare_esci_small.py +
scripts/evaluate_esci_retrieval.py，适配脚本 scripts/prepare_esci_from_tasksource.py）。

---

## 简历可用表述（基于以上实测数据）

- 设计并实现「混合召回（向量+BM25，RRF 融合）→ 7 级结构化后过滤 → 规则重排序」检索管道，在 82 条规则标注查询上 Recall@10 达 95.3%、NDCG@10 达 97.5%，相对向量裸召回 NDCG@10 提升 4.7%（100 SKU 商品库）；消融实测 RRF 融合双路三项指标均优于任一单路
- 检索管道单次查询 P50 延迟 0.26ms、P99 4.4ms；/chat SSE 首事件 TTFB P50 5.29ms，完整流耗时由 LLM 推理主导（DeepSeek 实测约 3.6s），系统自身开销毫秒级
- 在 Amazon ESCI 公开数据集（300 查询 / 2,583 商品采样）上完成端到端检索评测，词法链路 Recall@10 55.3%、NDCG@10 55.5%（含人工标注困难负样本，结果可复现）
- 设计三层幻觉守卫（商品存在性校验 / 价格库存属性事实校验 / SQL 数据校验，数值错误自动修正 + 纠错重生成 + 确定性模板降级），24 个导购问题三臂对比中，输出违规率从纯 LLM 的 54.2% 降至 RAG 的 8.3%，守卫后降至 0.0%（DeepSeek 实测）
- 通过 Schema 约束 Prompt + JSON mode 将 LLM 结构化输出解析成功率从 97.5% 提升至 100.0%，字段完整率 100.0%（40 样本实测）
- Agent 多轮路由评估 9 个用例通过 8 个（11/13 轮次），覆盖意图路由、上下文继承、反选过滤
