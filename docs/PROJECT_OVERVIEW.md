# Shop_Agent 项目架构总览

> 给 DeepSeek / AI 助手的项目上下文文档。阅读此文件可快速理解项目结构、核心流程和关键设计决策。

---

## 项目定位

**Shop_Agent** 是一个 RAG（检索增强生成）多模态电商智能导购 Agent。

- **前端**：Android Kotlin + Jetpack Compose（用户手机 App）
- **后端**：Python + FastAPI（你正在看的代码）
- **核心能力**：理解用户自然语言需求 → 检索商品 → LLM 生成推荐理由 → 返回结构化回答
- **目标**：工程级落地，可作为简历项目展示

---

## 技术栈

| 层级 | 技术 | 说明 |
|-----|------|------|
| LLM | 火山引擎 Ark API | deepseek-v4-flash（主模型）+ doubao-lite（降级） |
| 向量存储 | Milvus（HNSW_SQ 索引）| 高性能向量检索，SQ8 压缩减少显存 |
| 关键词检索 | BM25 + jieba 分词 | 中文倒排索引 |
| 精排 | Cross-Encoder | 三阶段检索最后一环 |
| 数据库 | MySQL + Redis | 商品数据、用户会话、缓存 |
| 框架 | FastAPI + asyncio | 异步处理 |
| 部署 | Docker + Nginx | 生产环境 |

---

## 目录结构

```
server/
├── agent/           # Agent 核心（大脑）
│   ├── orchestrator.py      # 编排器：串联完整请求流程
│   ├── semantic_llm.py      # 语义规划：意图识别 + 过滤条件提取
│   ├── recommendation_handler.py  # 推荐 Handler：检索 + LLM 生成回答
│   ├── hallucination_guard.py     # 幻觉检测守卫（三层）
│   ├── query_rewriter.py    # 查询重写（模糊输入处理）
│   ├── grounding.py         # Grounding 校验
│   └── workflow.py          # Handler 调度工作流
├── rag/             # 检索层
│   ├── milvus_store.py      # Milvus 向量存储
│   ├── bm25_engine.py       # BM25 关键词检索
│   ├── cross_encoder.py     # 精排模型
│   ├── hybrid_retriever.py  # 混合检索管道（三阶段）
│   └── feedback_loop.py     # 反馈闭环
├── llm/             # LLM 客户端
│   ├── ark_client.py        # 火山引擎 Ark API 客户端
│   └── prompt.py            # Prompt 模板
├── inputs/          # 输入处理
│   ├── multimodal.py        # 多模态输入（图片 + 文本）
│   └── vlm_structured.py    # VLM 结构化 JSON 输出
├── session/         # 会话管理
│   └── state.py             # SessionState（历史、过滤条件、购物车）
├── memory/          # 记忆系统
│   ├── short_term_memory.py
│   ├── long_term_memory.py
│   └── memory_manager.py
├── api/             # REST API
│   ├── chat.py              # /chat 主接口
│   └── ...
├── commerce/        # 电商网关
│   └── mysql_bridge.py      # MySQL 商品数据桥接
├── monitoring/      # 监控
│   └── metrics.py           # 指标收集
└── config.py        # 配置中心（所有环境变量开关）
```

---

## 核心流程（一次用户请求）

```
用户输入: "推荐一款2000元左右的手机"
   ↓
[1] Input Processor — 分词、关键词提取
   ↓
[2] Semantic Planner — 双层规划
    ├─ 规则层: 识别"手机"→product_type, "2000元"→max_price
    └─ LLM层(0.8s超时): 输出 JSON Plan（intent, filters, query）
   ↓
[3] Query Rewriter — 模糊检测 + 补全
    "推荐一款2000元左右的手机" → "手机 2000元以下 推荐"
   ↓
[4] Product Search — 三阶段检索
    ├─ 阶段1: BM25 粗排（Top-50）
    ├─ 阶段2: 向量检索（Top-20）
    └─ 阶段3: Cross-Encoder 精排（Top-5）
   ↓
[5] LLM Answer Generation — deepseek 生成推荐理由
    "根据您的需求，推荐以下 3 款手机..."
   ↓
[6] Hallucination Guard — 三层幻觉检测
    ├─ Layer1: 商品存在性校验
    ├─ Layer2: 属性事实校验（价格/库存精确匹配）
    └─ Layer3: SQL 真实数据校验
   ↓
[7] 返回用户 — 文本 + 商品卡片
```

---

## 关键设计决策

### 1. 双层规划（SemanticPlanner）
- **规则层**：快速匹配常见模式（"便宜"→max_price），零 LLM 消耗
- **LLM层**：处理长尾表达（"适合熬夜党用的"），0.8s 超时自动回退
- **合并逻辑**：confidence < 0.5 全用规则；cart/compare/bundle 优先规则

### 2. 三阶段检索
- **BM25**: 关键词匹配，快但粗
- **向量**: 语义相似，召回相关但不一定精确
- **Cross-Encoder**: 精排，慢但准，只处理 Top-20

### 3. 幻觉检测三层
- **Layer 1**: 检测编造商品（ID 不存在）
- **Layer 2**: 价格/库存精确匹配（零阈值）
- **Layer 3**: SQL 数据库校验（可选）

### 4. 大小模型分流
- **简单查询**（recommend + ≤3 cards）→ doubao-lite（便宜）
- **复杂查询**（compare/bundle/>5 cards）→ deepseek（强）

---

## 配置文件

所有开关集中在 `server/config.py`（Pydantic Settings），通过 `.env` 覆盖：

```bash
# LLM 配置
ARK_API_KEY=your-key
ARK_MODEL=ep-20260514111645-lmgt2

# 功能开关
USE_STRUCTURED_VLM=true
USE_CHINESE_CLIP=true
USE_MILVUS=true
ENABLE_HALLUCINATION_GUARD=true
USE_INDEXED_GROUNDING=true
ENABLE_LITE_MODEL_FALLBACK=true
ARK_LITE_MODEL=your-lite-endpoint

# Token 控制
LLM_PLANNING_MAX_TOKENS=512
LLM_ANSWER_MAX_TOKENS=1024
```

---

## 调试常用入口

| 需求 | 文件 | 函数 |
|-----|------|------|
| 看一次完整请求流程 | `server/agent/orchestrator.py` | `stream_chat()` |
| 看意图怎么解析 | `server/agent/semantic_llm.py` | `plan()` |
| 看检索怎么执行 | `server/agent/recommendation_handler.py` | `execute_product_search_with_budget()` |
| 看幻觉检测 | `server/agent/hallucination_guard.py` | `guard_hallucination()` |
| 看 Prompt 模板 | `server/llm/prompt.py` | `SYSTEM_PROMPT` |
| 看配置项 | `server/config.py` | `Settings` 类 |
