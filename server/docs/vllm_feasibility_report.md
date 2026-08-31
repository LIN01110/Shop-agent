# vLLM 本地部署可行性评估（Phase 4 模块 3）

> 本文件是调研模板，需要你填入实际数字后做出决策。

---

## 1. 现状分析

| 指标 | 当前值 | 来源 |
|---|---|---|
| 日均查询量 | ___ | Ark 控制台 / 日志统计 |
| 平均 LLM 调用占比 | ___% | 多少请求走到了 LLM 生成 |
| 平均 LLM 延迟 | ___ ms | /metrics 端点 |
| 月均 Ark API 费用 | ___ 元 | 火山引擎账单 |
| 峰值 QPS | ___ | 压测报告 |

---

## 2. vLLM 部署成本估算

### 2.1 模型选型

| 模型 | 大小 | 显存需求 | 上下文长度 | 适用场景 |
|---|---|---|---|---|
| Doubao-lite-4k | ~7B | 16 GB | 4k | 低成本、低延迟 |
| DeepSeek-V2 | ~236B (MoE) | 80 GB | 128k | 高质量、长上下文 |
| Qwen2.5-7B-Instruct | ~7B | 16 GB | 32k | 开源、社区活跃 |

**你的选择**：___

### 2.2 硬件成本

| 方案 | 配置 | 月租金 | 来源 |
|---|---|---|---|
| 云服务器 GPU | A10 / RTX 4090 / A100 | ___ 元/月 | 阿里云 / 腾讯云 |
| 自有机器 | 已有工作站 | 电费 ___ 元/月 | — |

### 2.3 吞吐量估算

参考数据（vLLM 官方 benchmark，batch=1）：

| 模型 | GPU | Throughput (tok/s) | 并发数 |
|---|---|---|---|
| Qwen2.5-7B | A10 | ~1200 | 10-20 |
| DeepSeek-V2 | A100 | ~800 | 5-10 |

**你的场景**：
- 峰值 QPS = ___
- 平均生成 token 数 = ___
- 所需并发 = ___
- **结论**：___ GPU 是否够用？

---

## 3. 成本对比

| 方案 | 月均成本 | 延迟 | 可控性 | 维护成本 |
|---|---|---|---|---|
| 继续 Ark API | ___ 元 | 中 | 低 | 低 |
| vLLM 本地部署 | ___ 元 | 低 | 高 | 高 |

**盈亏平衡点**：当日均查询 > ___ 时，vLLM 更划算。

---

## 4. 实施 checklist（如决定部署）

### 4.1 环境准备
- [ ] 申请 GPU 云服务器（___ 规格）
- [ ] 安装 CUDA 驱动（>= 12.1）
- [ ] 安装 vLLM：`pip install vllm`

### 4.2 模型下载
```bash
# 示例：下载 Qwen2.5-7B
huggingface-cli download Qwen/Qwen2.5-7B-Instruct --local-dir ./models/qwen2.5-7b
```

### 4.3 启动 vLLM 服务
```bash
python -m vllm.entrypoints.openai.api_server \
    --model ./models/qwen2.5-7b \
    --tensor-parallel-size 1 \
    --max-num-seqs 20 \
    --enable-prefix-caching  # 启用 Prefix Caching（共享系统提示词 KV Cache）
```

### 4.4 修改 ArkChatClient 支持 fallback
```python
# server/llm/ark_client.py 中增加本地 endpoint
class ArkChatClient:
    def __init__(self, ..., local_base_url: str | None = None):
        ...
        self.local_base_url = local_base_url

    async def stream_messages(self, messages):
        # 优先走本地 vLLM，失败 fallback 到 Ark
        if self.local_base_url:
            try:
                async for token in self._stream_via_local(messages):
                    yield token
                return
            except Exception:
                pass  # fallback to Ark
        ...
```

### 4.5 验证 Prefix Caching 生效
```bash
# 查看 vLLM 日志，确认 "Prefix cache hit" 统计
curl http://localhost:8000/metrics | grep vllm_prefix_cache
```

---

## 5. 决策建议

| 你的情况 | 建议 |
|---|---|
| 日均查询 < 1000，预算敏感 | **继续 Ark API**，搁置 vLLM |
| 日均查询 1k-10k，延迟敏感 | **评估 vLLM**，先做 PoC |
| 日均查询 > 10k，或已有 GPU | **必须上 vLLM**，成本优势明显 |

**你的决策**：___

---

> 填完这个表格后，把结论同步到项目 README 的 "架构决策" 章节。
