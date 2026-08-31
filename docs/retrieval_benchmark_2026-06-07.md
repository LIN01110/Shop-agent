# 检索性能压测记录

日期：2026-06-07

## 目标

验证商品库扩大后，导购检索链路是否仍能稳定返回相关商品，并识别本地 JSON fallback 与 Chroma 路径在查询延迟、建库耗时上的差异。

## 本次优化

- `VectorStore.query` 新增 `VectorSearchFilters`，把商品类型和价格作为检索前过滤条件传入向量库层。
- 本地 JSON fallback 新增商品类型倒排索引，避免每次查询都扫全库。
- 本地 JSON fallback 新增文档 token/title 特征缓存，避免每次查询重复解析商品文本。
- 本地 JSON fallback 将完整排序改为 `heapq.nlargest(top_k)`，减少大候选集上的排序开销。
- Chroma metadata 新增 `product_type` 布尔过滤标记和价格字段，查询时通过 `where` 下推硬过滤。
- Chroma collection 名包含索引 schema 版本，避免旧 metadata 结构与新过滤逻辑混用。
- 新增 `scripts/benchmark_retrieval.py`，支持本地/Chroma 两种 store，输出查询 P50/P95/平均延迟、卡片数量、合成数据耗时和建库耗时。

## 复跑命令

本地 fallback：

```powershell
cd D:\RAG
python scripts\benchmark_retrieval.py --store local --sizes 50000 --runs 1 --warmup 1 --top-k 5 --output docs\retrieval_benchmark_2026-06-07-local-50k-r1.json
```

Chroma：

```powershell
cd D:\RAG
python scripts\benchmark_retrieval.py --store chroma --sizes 1000 --runs 1 --warmup 0 --top-k 5 --output docs\retrieval_benchmark_2026-06-07-chroma-1k.json
```

## 结果摘要

本地 fallback，50k 合成商品库：

| 用例 | P50 | P95 | 平均 | 返回卡片 |
| --- | ---: | ---: | ---: | --- |
| 推荐一款运动鞋 | 64.15ms | 64.15ms | 64.15ms | 5 |
| 推荐一款适合跑步的鞋 | 105.29ms | 105.29ms | 105.29ms | 5 |
| 推荐运动鞋或运动裤 | 129.77ms | 129.77ms | 129.77ms | 5 |
| 推荐一款保湿眼霜，预算250以内 | 20.06ms | 20.06ms | 20.06ms | 5 |
| 推荐一款拍照手机，预算4000以内 | 16.71ms | 16.71ms | 16.71ms | 5 |

本地 fallback 建库：

| 规模 | 合成数据 | 建索引 |
| ---: | ---: | ---: |
| 50k | 345.65ms | 71878.17ms |

Chroma，1k 合成商品库：

| 用例 | P50 | P95 | 平均 | 返回卡片 |
| --- | ---: | ---: | ---: | --- |
| 推荐一款运动鞋 | 140.12ms | 140.12ms | 140.12ms | 5 |
| 推荐一款适合跑步的鞋 | 101.63ms | 101.63ms | 101.63ms | 5 |
| 推荐运动鞋或运动裤 | 178.34ms | 178.34ms | 178.34ms | 5 |
| 推荐一款保湿眼霜，预算250以内 | 36.12ms | 36.12ms | 36.12ms | 5 |
| 推荐一款拍照手机，预算4000以内 | 32.75ms | 32.75ms | 32.75ms | 5 |

Chroma 建库：

| 规模 | 合成数据 | 建库/写入 |
| ---: | ---: | ---: |
| 1k | 6.20ms | 12579.65ms |

## 工程结论

- 查询链路已从“全库扫描 + 后置过滤”推进到“metadata/facet 预过滤 + Top-K 截断”，能避免“推荐运动鞋返回运动裤”这类同大类污染，也能显著降低大库查询成本。
- 本地 JSON fallback 在 50k 合成库上查询仍可保持几十到百毫秒级，但启动建索引约 72 秒，不适合作为生产大库主路径。
- Chroma 查询稳定走 metadata `where`，更接近生产形态；当前 1k 写入约 12.6 秒，后续真实大库应使用持久化 Chroma，避免每次启动重建。
- 50k 多轮压测曾出现一次 753ms 级查询抖动，属于需要继续观察的尾延迟风险；后续应增加 runs、记录进程资源，并在真实 embedding + 持久化 Chroma 上复测。

## 下一步

1. 使用 `USE_CHROMA=true` 和 `USE_ARK_EMBEDDING=true` 做一次真实 embedding 灌库，并复跑 Chroma 10k/50k。
2. 为商品库 ingestion 增加 taxonomy/schema 版本记录，变更过滤 metadata 时提示重灌。
3. 增加 rerank 层，只对检索前过滤后的较小候选集做精排。
4. 将 benchmark 纳入每次检索层改动后的手动回归清单。
