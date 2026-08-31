# Milvus SQ8 量化操作指南（Phase 4 模块 6）

> 手动操作 checklist。执行前请确保已有 Milvus 数据备份。

---

## 为什么做 SQ8 量化？

| 指标 | FP32 | SQ8 | 节省 |
|---|---|---|---|
| 向量存储/条 | 512 dim × 4B = 2KB | 512 dim × 1B = 512B | **75%** |
| 精度损失 | 0% | < 1% | 可接受 |
| 检索速度 | 基准 | 略快或持平 | — |

---

## 前置条件

- [ ] Milvus 已运行并可连接
- [ ] 当前集合已有数据
- [ ] 已确认 `server/config.py` 中 `use_milvus=true`

---

## 操作步骤

### 步骤 1：确认当前索引类型

```python
# 在 Python 中执行
from pymilvus import connections, Collection

connections.connect(uri="http://localhost:19530")
collection = Collection("products")
print(collection.indexes)  # 查看当前索引
```

记录当前索引类型：___

### 步骤 2：创建 SQ8 索引（推荐 IVF_SQ8）

```python
from pymilvus import Index

index_params = {
    "index_type": "IVF_SQ8",      # 或 "HNSW_SQ8"（Milvus 2.4+）
    "metric_type": "COSINE",
    "params": {"nlist": 128},     # IVF 聚类数，根据数据量调整
}

# 删除旧索引（如有）
collection.drop_index()

# 创建新索引
collection.create_index(field_name="embedding", index_params=index_params)
collection.load()
```

### 步骤 3：验证索引生效

```python
# 检查索引信息
print(collection.indexes)

# 执行测试查询
results = collection.search(
    data=[[0.1] * 512],  # 你的向量维度
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 10}},
    limit=5,
)
print(results)
```

### 步骤 4：修改配置

在 `.env` 或环境变量中：

```bash
# 如果是 IVF_SQ8
MILVUS_INDEX_TYPE=IVF_SQ8

# 如果是 HNSW_SQ8（Milvus 2.4+）
# MILVUS_INDEX_TYPE=HNSW_SQ8
```

### 步骤 5：重新运行 eval 验证准确率

```bash
cd server
python -m scripts.eval_retrieval_params
```

对比量化前后的 **Top-1 准确率**：
- 量化前：___%
- 量化后：___%
- **下降幅度**：___%（应 < 1%）

### 步骤 6：监控存储变化

```bash
# Milvus 存储目录大小（Docker 部署时）
docker exec milvus-standalone du -sh /var/lib/milvus
```

- 量化前：___ MB
- 量化后：___ MB
- **节省**：___%

---

## 回滚方案

如果准确率下降 > 2%：

```python
# 删除 SQ8 索引，重建原索引
collection.drop_index()
collection.create_index(
    field_name="embedding",
    index_params={
        "index_type": "HNSW",  # 或原来的 IVF_FLAT
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200},
    }
)
collection.load()
```

---

## 验收标准

| 检查项 | 标准 | 结果 |
|---|---|---|
| 索引创建成功 | `collection.indexes` 显示 IVF_SQ8 | [ ] |
| 查询正常 | `collection.search()` 返回结果 | [ ] |
| 准确率下降 < 1% | eval 报告 Top-1 准确率变化 | [ ] |
| 存储降低 > 50% | `du -sh` 对比 | [ ] |

---

> 完成后更新 `PHASE4_README.md`，标记模块 6 为已完成。
