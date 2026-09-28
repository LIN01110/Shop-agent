# RAG 多模态电商智能导购 Agent

> 字节跳动全栈挑战赛个人项目。面向电商场景的导购 Agent：以图搜商品、多轮导购对话、商品对比、对话式购物车；从原型迭代为具备完整检索链路、反馈闭环、幻觉守卫与故障降级的生产级架构，评测脚本与数据开源可复现。

![Python](https://img.shields.io/badge/Python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-SSE-green) ![Milvus](https://img.shields.io/badge/Milvus-HNSW-orange) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

## 实测指标（全部可复现，脚本见 `scripts/`、`server/eval/`）

| 维度 | 结果 | 口径 |
|---|---|---|
| 检索质量 | **Recall@10 95.3% / NDCG@10 97.5%** | 82 条标注查询，较向量裸召回 +4.7%；Amazon ESCI 公开集（300 查询/2583 商品）端到端可复现 |
| 幻觉治理 | **输出违规率 54.2% → 8.3% → 0%** | 24 个导购问题三臂对比：纯 LLM → RAG → RAG+守卫 |
| 多模态属性抽取 | **解析成功率/字段完整率 97.5% → 100%** | VLM 结构化 JSON 输出（Schema 约束 + 失败回退），40 样本实测 |
| 性能 | 检索 **P50 0.26ms** / 首事件 **TTFB 5.29ms** | Milvus HNSW + FastAPI 异步 SSE |
| 成本 | LLM 调用 **-33.3%** / 总 token **-54.4%** | 规则路由将事实型查询分流至确定性 handler，24 问双臂对比 |
| 回归保障 | **310 项 pytest 用例全绿** | 含检索/守卫/购物车/MCP 工具端到端 |

### ESCI 库规模压力测试（2026-09-09）

同一管线、同一 seed，把评测规模扩大 10 倍（脚本 `scripts/prepare_esci_small.py` + `evaluate_esci_retrieval.py`，ESCI-S us 公开数据）：

| 评测集 | 商品池 | Recall@10 | MRR | NDCG@10 |
|---|---|---|---|---|
| esci_small | 2,583 | 55.3% | 65.2% | 55.5% |
| esci_large | **24,063（×9.3）** | 34.6% | 46.5% | 35.2% |

同一管线、同一 seed 下把商品池扩大约 10 倍，量化刻画了不同 embedding 方案对库规模的敏感性，为检索架构选型提供可复现的压测基线（扩容复现：`--max-queries 3000`）。

### ABO 真实商品库压测：hashing vs BGE（2026-09-17）

把商品池换成 **Amazon Berkeley Objects 真实电商数据**（145,615 去重商品，其中 11,642 条带中文标题；CC-BY-4.0，仅研究用途），用 200 条中文合成标题查询（title-to-product 基准）对比 embedding 方案。脚本 `scripts/prepare_abo.py` + `evaluate_esci_retrieval.py --embedding local-st`，全部可复现：

| 商品池规模 | hashing Recall@10 / MRR / NDCG@10 | **BGE-small-zh Recall@10 / MRR / NDCG@10** |
|---|---|---|
| 50,000 | 78.5% / 66.8% / 69.7% | **98.0%** / 86.6% / 89.4% |
| 80,000 | 63.0% / 54.9% / 56.9% | **97.5%** / 86.1% / 88.9% |

本地 BGE-small-zh（sentence-transformers，CPU 可跑）在 8 万真实商品规模下保持 Recall@10 97.5%，作为系统默认语义 embedding 方案；索引入库脚本支持分批写入与断点续跑。

```bash
# 数据准备（ABO parquet 经 hf-mirror 下载，见 data_external/abo/）
python scripts/prepare_abo.py --max-products 0 --max-queries 500 --output-dir data/benchmarks/abo_full
# BGE 索引（分批入库，支持断点续跑；hh_neuron 等含 sentence-transformers 的环境）
HF_ENDPOINT=https://hf-mirror.com python scripts/ingest_abo_st.py --products data/benchmarks/abo_full/products.json \
  --persist-dir data/benchmarks/abo/chroma --collection-name abo_products
# 评测（hashing / local-st 双臂对比）
python scripts/evaluate_esci_retrieval.py --products data/benchmarks/abo_full/products.json \
  --queries data/benchmarks/abo_full/queries.jsonl --store chroma --embedding local-st \
  --persist-dir data/benchmarks/abo/chroma --collection-name abo_products --limit 200
```

> 注：ABO 无价格字段，压测商品的价格为按类目价格带确定性合成（`attributes.price_synthesized=true`），仅用于过滤器压测，不进入推荐话术事实源。

### Embedding LoRA 微调实验（2026-09-28）

用 ESCI 人工标注对（2 万）+ ABO 中文标题-描述对（1.05 万）做对比学习微调（`MultipleNegativesRankingLoss`，in-batch negatives，LoRA rank=16 挂 q/k/v 投影，可训练参数 0.81%，CPU 可复现）。评测为 ABO 中文子集（11,642 商品）500 条合成查询直评（脚本 `scripts/build_st_training_data.py` / `finetune_st_lora.py` / `evaluate_st_models.py`）：

| 模型 | Recall@10 | MRR | NDCG@10 |
|---|---|---|---|
| BGE-small-zh（基线） | 98.0% | 77.7% | 82.7% |
| + LoRA 中文纯享 300 步（lr 5e-5） | 97.6% | **78.0%** | **82.8%** |

LoRA 领域微调在中文商品语义匹配上 MRR / NDCG 双指标超过基线；训练数据构建、LoRA 训练、双臂评测脚本全部开源，CPU 可复现完整流程。

### LLM-as-Judge 双层评测（2026-09-28）

对 24 个导购问题 × pure_llm/rag 双臂重新生成回答（DeepSeek），同时用轻量规则（对齐 GroundingGuard 核心规则）与 LLM 裁判（忠实度/相关性/自然度 1-5 分 + 二值违规）双层判定（脚本 `scripts/evaluate_llm_judge.py`，报告 `results/llm_judge.json`）：

| 臂 | LLM 违规率 | 规则违规率 | 说明 |
|---|---|---|---|
| pure_llm | 87.5% | 33.3% | LLM 能识别"编造证据外商品/价格"，规则看不到 |
| rag | 4.2% | 29.2% | 规则对拒答话术中的违禁词更敏感，LLM 会结合语境豁免 |

二者 Cohen's Kappa ≈ 0.10（一致性弱）——实测证明**规则与 LLM 裁判是互补而非替代**：规则管硬事实底线，LLM 管语义质量，生产上应双层并存。

### Bad Case 归因体系（2026-09-28）

检索评测 miss 自动归因 6 类（召回缺失/排序错误/近似重复/查询歧义/品牌碎片/其他），输出回归集（脚本 `scripts/attribute_bad_cases.py`，回归集 `server/eval/bad_case_regression_abo.json`）。ABO 80k 规模 20 条 bad case 分布：近似重复干扰 80%、召回缺失 20%——为检索链路的重排与去重优化提供量化归因依据。

## 架构

```text
用户（Android Compose / Web）
   │  SSE 流式
   ▼
FastAPI 后端 ── 规则路由（事实型 → 确定性 handler，省 token）
   │
   ├─ 语义规划层：LLM JSON plan + Pydantic 校验 + 规则 fallback（预算内超时回退）
   ├─ 检索链路：向量 + BM25 双路召回（RRF 融合）→ 7 级结构化后过滤 → 规则重排序
   │     └─ Milvus（HNSW + 标量过滤）/ ChromaDB / 本地 JSON fallback 可插拔
   ├─ 幻觉守卫：商品存在性校验 → 价格库存事实比对 → 数值错误自动修正 → 模板降级
   ├─ 多模态：VLM 属性抽取（Schema 约束 JSON）+ Chinese-CLIP 图文双模态
   └─ MCP Server：搜索/对比/购物车封装为 3 个标准工具（stdio，端到端验证通过）
```

## 核心能力

- **多轮导购**：主动澄清（宽泛需求先追问预算/偏好）、上下文追问（"第二个怎么样"）、品类切换状态清理
- **商品对比 / 对话式购物车**：结构化参数进、JSON 出，session 候选绑定（"把第一个加购物车"）
- **以图搜商品**：VLM 属性抽取 + 视觉签名相似匹配，复用同一 RAG 链路
- **故障降级**：LLM 未配置或调用失败自动回退模板回答，`/chat` 不中断
- **会话持久化**：memory / sqlite / redis 三种后端，多实例可共享购物车状态

## 快速开始

```bash
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r server/requirements.txt
copy .env.example .env                                  # 填入 ARK_API_KEY（可选，不填则模板降级）
.\scripts\run_server.ps1
```

```bash
# 回归测试 / 离线评估 / 压测
python -m pytest tests/ -q
python scripts/evaluate_agent.py
python scripts/benchmark_retrieval.py --store local --sizes 50000 --top-k 5
```

未配置 API Key 时全链路自动降级为本地模板与规则路径，评测脚本可离线复现。

## 目录结构

```text
.
├── client/                  # Android Kotlin + Jetpack Compose
├── server/                  # FastAPI 后端与 RAG/Agent 模块
│   ├── rag/                 # 检索链路（召回/融合/过滤/重排）
│   ├── agent/               # 规划层与各 handler
│   ├── eval/                # 评测用例
│   └── mcp_server.py        # MCP 工具服务
├── docs/                    # 架构、接口、压测报告
├── scripts/                 # 启动/评测/压测脚本
└── tests/                   # 310 项 pytest 回归
```

## 隐私与数据说明

- `.env`（API Key）通过 `.gitignore` 排除，仓库内仅含 `.env.example`
- 商品数据与图片资源未上传；`data/` 结构见代码内 schema，可自行灌入

详细设计见 [docs/architecture.md](docs/architecture.md)、[docs/api.md](docs/api.md)。
