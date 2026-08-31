# ESCI Small Benchmark

日期：2026-06-08

## 数据来源

- 官方仓库：`https://github.com/amazon-science/esci-data`
- 本地原始目录：`D:\RAG\data_external\esci\esci-data\shopping_queries_dataset`
- 原始文件：
  - `shopping_queries_dataset_examples.parquet`：48.91 MB
  - `shopping_queries_dataset_products.parquet`：1057.49 MB
  - `shopping_queries_dataset_sources.csv`：1.73 MB

## 本地 small 子集

生成命令：

```powershell
python scripts\prepare_esci_small.py --max-queries 300 --max-products 12000 --locale us
```

输出目录：`D:\RAG\data\benchmarks\esci_small`

- 商品数：5549
- 查询数：300
- `products.json`：约 9.04 MB
- `queries.jsonl`：约 0.42 MB

该目录已被 `.gitignore` 忽略，避免把外部数据和派生 benchmark 数据提交进仓库。

## 检索评测

评测脚本：

```powershell
python scripts\evaluate_esci_retrieval.py --top-k 5  --output data\benchmarks\esci_small\retrieval_report_top5.json
python scripts\evaluate_esci_retrieval.py --top-k 10 --output data\benchmarks\esci_small\retrieval_report.json
python scripts\evaluate_esci_retrieval.py --top-k 20 --output data\benchmarks\esci_small\retrieval_report_top20.json
```

| K | Recall@K | MRR@K | NDCG@K | P50 latency | P95 latency |
|---:|---:|---:|---:|---:|---:|
| 5 | 0.2618 | 0.8694 | 0.7588 | 31.14 ms | 54.77 ms |
| 10 | 0.4737 | 0.8714 | 0.7406 | 30.22 ms | 51.89 ms |
| 20 | 0.6902 | 0.8720 | 0.7381 | 33.33 ms | 57.21 ms |

## 解读

- 这是第一次在公开外部数据集上的真实 query-product relevance 评测，不再只依赖手写 demo query。
- 当前 local JSON 检索延迟表现稳定，P95 在 60 ms 内。
- MRR 较高，说明很多查询的首个相关商品能较早出现。
- Recall@K 随 K 明显增长，说明候选召回仍可通过 hybrid retrieval 或 rerank 提升。
- ESCI 商品没有真实价格、库存、图片，当前转换器把价格设为 0，只用于检索评测，不用于真实交易演示。

## 下一步

1. 增加 ESCI 10k/30k/100k 商品分层评测。
2. 在 `ProductRetrievalPipeline` 中加入 hybrid lexical/vector scoring。
3. 对 ESCI labels 分别报告 Exact/Substitute/Complement 的召回。
4. 用同一 small 子集对 Chroma + hashing embedding、Chroma + Ark embedding 做横向比较。
