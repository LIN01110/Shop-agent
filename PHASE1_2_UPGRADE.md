# Shop-Agent Phase 1 + Phase 2 升级指南

## 新增模块一览

| 模块 | 路径 | 说明 |
|------|------|------|
| VLM 结构化 JSON | `server/inputs/vlm_structured.py` | VLM 输出严格 JSON，替代正则匹配 |
| Chinese-CLIP | `server/inputs/chinese_clip_provider.py` | 本地中文跨模态嵌入 |
| Milvus 存储 | `server/rag/milvus_store.py` | 高性能向量存储，支持混合检索 |
| BM25 引擎 | `server/rag/bm25_engine.py` | 中文分词 + 倒排索引 |
| Cross-Encoder | `server/rag/cross_encoder.py` | 精排模型 |
| 反馈闭环 | `server/rag/feedback_loop.py` | 用户行为收集 + LTR 训练数据导出 |
| 混合检索 | `server/rag/hybrid_retriever.py` | 三阶段检索管道（BM25 → 向量 → CE） |

## 快速启用

### 1. 安装依赖

```bash
# Phase 1
pip install torch transformers ftfy regex tqdm
pip install git+https://github.com/OFA-Sys/Chinese-CLIP.git
pip install pymilvus>=2.4.0

# Phase 2
pip install jieba rank-bm25
pip install sentence-transformers>=2.2.0
```

### 2. 环境变量配置

在 `.env` 文件中添加：

```env
# ── Phase 1 ──
USE_STRUCTURED_VLM=true
USE_CHINESE_CLIP=true
USE_MILVUS=true
MILVUS_URI=http://localhost:19530

# ── Phase 2 ──
USE_BM25=true
USE_CROSS_ENCODER=true
USE_HYBRID_RETRIEVAL=true
USE_FEEDBACK_LOOP=true
```

### 3. 启动 Milvus（如启用）

```bash
docker run -d \
  --name milvus-standalone \
  -p 19530:19530 \
  -p 9091:9091 \
  milvusdb/milvus:latest
```

### 4. 启动服务

```bash
python -m uvicorn server.main:app --reload
```

## 配置项参考

详见 `server/config.py`，新增配置均已设置默认值，不配置也能启动（功能关闭）。

## 架构对比

### 升级前
```
用户查询 → Chroma 向量检索 → 7 PostProcessor → 去重 → 规则重排序 → 结果
```

### 升级后（Phase 1 + 2）
```
用户查询
  ├── 图片 → Chinese-CLIP 嵌入 / VLM 结构化 JSON
  └── 文本 → BM25 粗排（jieba 分词）
           → 向量精排（Milvus / Chroma）
           → Cross-Encoder 精排
           → 反馈闭环记录
           → 结果
```

## 性能预期

| 指标 | 升级前 | 升级后 |
|------|--------|--------|
| 召回率 | 依赖 Chroma | BM25 + 向量双路召回 ↑ |
| 准确率 | 规则重排 | Cross-Encoder 语义重排 ↑ |
| 图片理解 | 关键词正则 | JSON 结构化输出 ↑ |
| 向量质量 | Ark 多模态 | Chinese-CLIP 本地语义 ↑ |
| 规模 | Chroma 单机 | Milvus 支持分布式 ↑ |

## 回退策略

所有新功能通过配置开关控制：
- `USE_MILVUS=false` → 回退到 Chroma / LocalJson
- `USE_BM25=false` → 跳过 BM25 阶段
- `USE_CROSS_ENCODER=false` → 跳过精排阶段
- `USE_STRUCTURED_VLM=false` → 使用旧版 VLM

即使 Milvus / Cross-Encoder 初始化失败，也会自动回退到原有方案，不影响服务可用性。
