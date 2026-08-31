# 如何在 VSCode + ClipChat + DeepSeek 中使用项目文档

> 让 DeepSeek 快速理解 Shop_Agent 项目，提升问答质量。

---

## 文档清单

已创建 3 个参考文档：

| 文档 | 用途 | 长度 |
|-----|------|------|
| `docs/PROJECT_OVERVIEW.md` | 项目架构总览、技术栈、核心设计决策 | ~4KB |
| `docs/CORE_FLOWS.md` | 一次请求的完整流转、时序、调试技巧 | ~8KB |
| `docs/CODE_GUIDE.md` | 按需求定位代码、常见修改模式 | ~5KB |

---

## 使用方法

### 方法 1: 直接粘贴到 ClipChat 上下文（推荐首次使用）

1. 打开 VSCode，打开 ClipChat 面板
2. 新建对话
3. 把 `PROJECT_OVERVIEW.md` 全文粘贴到用户输入框
4. 发送，然后问：
   ```
   请根据以上项目背景，帮我分析 server/agent/orchestrator.py 的 stream_chat() 函数
   ```

**优点**: DeepSeek 立即获得完整上下文，回答质量高
**缺点**: 每次新对话都要粘贴

---

### 方法 2: 使用 @file 引用（如果 ClipChat 支持）

部分 ClipChat 版本支持 `@file` 引用：

```
@file docs/PROJECT_OVERVIEW.md
@file docs/CODE_GUIDE.md

请帮我分析为什么 recommendation_handler.py 中的幻觉检测会拦截正常回答？
```

**优点**: 不用手动粘贴，自动加载文件内容
**缺点**: 依赖 ClipChat 版本是否支持

---

### 方法 3: 设置为系统提示（System Prompt）

如果 ClipChat 支持自定义 System Prompt：

1. 打开 ClipChat 设置
2. 找到 "System Prompt" 或 "系统提示" 配置
3. 粘贴以下内容：

```
你正在分析 Shop_Agent 项目，这是一个 RAG 多模态电商智能导购 Agent。

项目背景：
- 后端：Python + FastAPI
- LLM：火山引擎 Ark API（deepseek-v4-flash + doubao-lite）
- 向量存储：Milvus（HNSW_SQ 索引）
- 检索：BM25 + 向量 + Cross-Encoder 三阶段
- 幻觉检测：三层校验（商品存在性/属性事实/SQL 校验）

核心流程：
用户输入 → InputProcessor → SemanticPlanner（规则+LLM双层）→ QueryRewriter → 
ProductSearch（三阶段检索）→ LLM Answer → HallucinationGuard → 返回用户

关键文件映射：
- 编排器：server/agent/orchestrator.py
- 语义规划：server/agent/semantic_llm.py
- 推荐处理：server/agent/recommendation_handler.py
- 幻觉检测：server/agent/hallucination_guard.py
- 查询重写：server/agent/query_rewriter.py
- 配置中心：server/config.py

分析代码时：
1. 先理解代码在核心流程中的位置
2. 关注与 SessionState 的交互
3. 注意 LLM 调用点的超时和降级逻辑
```

**优点**: 每次对话自动携带上下文，无需重复粘贴
**缺点**: 有 token 长度限制，太长的 System Prompt 会占用输入空间

---

### 方法 4: 分阶段加载（深度分析时使用）

对于复杂问题，分多次加载文档：

**第一轮** - 加载项目总览：
```
[粘贴 PROJECT_OVERVIEW.md]

请总结这个项目的核心架构和关键技术决策。
```

**第二轮** - 加载流程详解：
```
[粘贴 CORE_FLOWS.md]

基于以上流程，请分析 orchestrator.py 中 Step 5-7 的实现细节。
```

**第三轮** - 加载代码导航：
```
[粘贴 CODE_GUIDE.md]

我想修改幻觉检测的 Layer 2，请告诉我具体要改哪些文件、哪些函数。
```

**优点**: 每次对话聚焦一个层面，不易混乱
**缺点**: 需要多轮对话

---

## 高效提问模板

加载文档后，使用以下模板提问，DeepSeek 回答质量更高：

### 模板 1: 代码分析

```
请分析文件 [文件路径] 中的 [函数/类名]：
1. 这个函数在核心流程中处于哪一步？
2. 输入输出是什么？
3. 关键逻辑是什么？
4. 有哪些降级/容错处理？
5. 如果我要修改 [具体需求]，应该改哪里？
```

### 模板 2: Bug 排查

```
我遇到了一个问题：[描述现象]
相关代码在 [文件路径]。
请帮我：
1. 分析可能的原因
2. 指出具体哪行代码可能有问题
3. 给出修复建议
```

### 模板 3: 功能扩展

```
我想添加一个功能：[功能描述]
请帮我：
1. 这个功能应该放在核心流程的哪个位置？
2. 需要修改哪些文件？
3. 给出实现思路和关键代码
```

---

## 注意事项

1. **Token 限制**: DeepSeek 有上下文长度限制（通常 8K-32K）。如果文档太长，先加载 PROJECT_OVERVIEW.md，需要时再加载其他文档。

2. **代码引用**: 提问时尽量给出具体文件路径和函数名，DeepSeek 能更精准定位。

3. **增量更新**: 如果项目代码有修改，同步更新文档中的信息，否则 DeepSeek 可能基于过时的上下文回答。

4. **多文件分析**: 如果要分析多个文件的交互，一次性把所有相关文件内容贴给 DeepSeek，比分开问效果更好。

---

## 快速开始（复制即用）

把下面这段话复制到 ClipChat：

```
我正在分析 Shop_Agent 项目，这是一个 RAG 多模态电商智能导购 Agent。

请阅读以下项目文档后回答我的问题：

---

[粘贴 docs/PROJECT_OVERVIEW.md 内容]

---

[粘贴 docs/CODE_GUIDE.md 内容]

---

我的问题是：[你的问题]
```
