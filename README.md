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
