# Future Technology Plan — Shop-Agent

> 本文件记录当前暂不需要、但未来上云/高并发场景下可能需要引入的技术栈。
> 按优先级排序，当前全部为 **搁置状态**。

---

## Tier 1: 监控与可观测性（上线后 1-2 月内）

### Prometheus + Grafana
- **为什么**: 手写 `/metrics` 只能看内存中的滑动窗口，重启后数据丢失；Prometheus 提供持久化 TSDB + 告警规则
- **何时引入**: 部署到云服务器、需要 24h 监控时
- **对接方式**: `prometheus-client` 库暴露 `/metrics`，Grafana 仪表盘展示 QPS / P99 / 错误率 / 缓存命中率
- **预估工作量**: 半天（已有埋点基础，只需换 exporter）

---

## Tier 2: 异步任务队列（日均 1w+ 查询时）

### Celery + Redis/RabbitMQ
- **为什么**: `ThreadPoolExecutor` 在单机上够快，但无法跨机器分布式执行；Celery 支持多 worker 横向扩容
- **何时引入**: 日均查询量 > 1 万，或需要离线任务（如夜间全量索引重建）
- **改造点**: 把 `TaskScheduler` 的 `concurrent.futures` 后端换成 Celery task，其他接口不变
- **预估工作量**: 1 天

---

## Tier 3: 流式数据处理（实时推荐场景）

### Kafka
- **为什么**: 反馈闭环当前用 SQLite + 内存队列，适合小数据量；Kafka 适合实时流处理（如"用户浏览即触发推荐更新"）
- **何时引入**: 需要实时个性化推荐、A/B 测试数据流、多服务事件总线
- **改造点**: `FeedbackLoop` 的 `_flush()` 改成 Kafka producer，消费者端做实时模型更新
- **预估工作量**: 2-3 天

---

## Tier 4: 向量数据库升级（百万级商品时）

### Milvus Cluster / Pinecone / Weaviate
- **为什么**: 单机 Milvus 支撑 10-100 万向量；超过后需要分布式 Milvus 或托管向量库
- **何时引入**: 商品量 > 50 万，或需要多 AZ 高可用
- **改造点**: `MilvusStore` 的 connection 参数改成集群配置，其余接口不变
- **预估工作量**: 半天

---

## Tier 5: 大模型推理加速（高并发 LLM 调用）

### vLLM / TGI / TensorRT-LLM
- **为什么**: Ark API 有 QPS 限制和成本；本地部署 Doubao/DeepSeek 模型可降低延迟和费用
- **何时引入**: LLM 调用成为瓶颈（> 50% 请求涉及 VLM/LLM），或需要离线微调
- **改造点**: `ArkClient` 增加本地 endpoint 支持，优先走本地 vLLM，失败 fallback 到 Ark
- **预估工作量**: 2 天（需 GPU 服务器 + 模型下载）

---

## 决策树

```
日均查询 < 1000 → 当前架构完全够用
日均查询 1k-10k → 引入 Prometheus + Grafana 监控
日均查询 10k-100k → 加 Celery + Redis 任务队列
日均查询 > 100k → 考虑 Kafka + Milvus Cluster + vLLM
商品量 < 10 万 → 单机 Milvus/Chroma 够用
商品量 > 50 万 → 分布式向量库
```

---

*最后更新: 2026-08-07*
