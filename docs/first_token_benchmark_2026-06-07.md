# 首 Token 延迟压测记录

日期：2026-06-07

## 目标

验证 `/chat` SSE 在典型导购场景下能否在 1 秒内返回第一个 `token`，满足 PDF 加分项“首屏极速响应”的方向。

## 本次优化

- 在 FastAPI lifespan 阶段预热 Orchestrator、商品检索 store 和图片相似索引，避免把冷启动成本转嫁给第一个用户请求。
- 在 Orchestrator 收到请求后立即发送首个 `token`：`我先看一下，`。
- 首 token 后主动让出一次事件循环，确保网络层先 flush，再继续执行图片处理、语义规划、检索和 LLM。
- 新增 `scripts/benchmark_first_token.py`，默认拿到首个 token 即关闭流，避免完整 LLM 回复时长干扰首 token 指标。

## 复跑命令

```powershell
cd D:\RAG
python scripts\benchmark_first_token.py --url http://127.0.0.1:8001/chat --runs 3 --warmup 1 --threshold-ms 1000 --output docs\first_token_benchmark_2026-06-07.json
```

## 结果

| 用例 | P50 首 Token | P95 首 Token | 平均 | 最大值 | 是否通过 |
| --- | ---: | ---: | ---: | ---: | --- |
| 保湿眼霜推荐 | 316.91ms | 331.92ms | 321.92ms | 333.59ms | 是 |
| 运动鞋推荐 | 317.33ms | 342.04ms | 324.10ms | 344.79ms | 是 |
| 三亚度假组合方案 | 317.71ms | 334.85ms | 322.74ms | 336.76ms | 是 |
| 手机主动澄清 | 309.07ms | 318.29ms | 310.89ms | 319.32ms | 是 |

## 结论

当前本地联调环境下，典型请求的 P95 首 Token 均低于 1 秒。后续部署到服务器后，需要用同一脚本对公网/局域网服务再跑一次，避免网络、反向代理和真实 LLM 配置改变首 token 表现。
