# Shop_Agent 代码导航指南

> 快速定位代码、理解模块边界、掌握常见修改模式。

---

## 按需求定位代码

### 我想改... → 去这个文件

| 想改什么 | 文件 | 关键函数/类 |
|---------|------|-----------|
| **意图识别逻辑** | `server/agent/semantic_llm.py` | `SemanticPlanner.plan()` |
| **规则匹配模式** | `server/agent/semantic_rules.py` | `build_rule_plan()` |
| **模糊输入处理** | `server/agent/query_rewriter.py` | `rewrite_query_async()` |
| **检索算法** | `server/rag/hybrid_retriever.py` | `retrieve()` |
| **BM25 实现** | `server/rag/bm25_engine.py` | `BM25Engine.search()` |
| **向量存储** | `server/rag/milvus_store.py` | `MilvusStore.search()` |
| **精排模型** | `server/rag/cross_encoder.py` | `CrossEncoderReranker.rerank()` |
| **LLM 客户端** | `server/llm/ark_client.py` | `ArkChatClient.stream_messages()` |
| **Prompt 模板** | `server/llm/prompt.py` | `SYSTEM_PROMPT`, `INDEXED_SYSTEM_PROMPT` |
| **幻觉检测** | `server/agent/hallucination_guard.py` | `guard_hallucination()` |
| **回答生成** | `server/agent/recommendation_handler.py` | `RecommendationHandler.handle()` |
| **Handler 调度** | `server/agent/workflow.py` | `AgentWorkflow.stream()` |
| **会话状态** | `server/session/state.py` | `SessionState` |
| **配置项** | `server/config.py` | `Settings` |
| **API 接口** | `server/api/chat.py` | `chat_endpoint()` |
| **多模态输入** | `server/inputs/multimodal.py` | `MultimodalInputProcessor.process()` |
| **VLM 输出** | `server/inputs/vlm_structured.py` | `StructuredVLMProcessor.extract()` |
| **商品搜索工具** | `server/tools/search.py` | `ProductSearchTool.execute()` |
| **反馈闭环** | `server/rag/feedback_loop.py` | `FeedbackLoop.log_event()` |

---

## 核心类关系图

```
Orchestrator (编排器)
    ├── SemanticPlanner (语义规划)
    │   ├── build_rule_plan()      # 规则层
    │   └── _try_llm_plan()        # LLM层
    ├── AgentWorkflow (工作流调度)
    │   ├── ClarificationHandler   # 澄清
    │   ├── CartHandler            # 购物车
    │   ├── CompareHandler         # 对比
    │   ├── ScenarioBundleHandler  # 场景搭配
    │   ├── ContextFollowUpHandler # 上下文追问
    │   └── RecommendationHandler  # 推荐（兜底）
    └── SessionState (会话状态)
        ├── history[]              # 对话历史
        ├── filters[]              # 已确认过滤条件
        ├── candidate_product_cards # 上一轮候选
        └── cart[]                 # 购物车

RecommendationHandler (推荐)
    ├── ProductSearchTool (检索工具)
    │   ├── HybridRetriever (混合检索)
    │   │   ├── BM25Engine         # 关键词
    │   │   ├── MilvusStore        # 向量
    │   │   └── CrossEncoder       # 精排
    │   └── SearchFilters (过滤条件)
    ├── LLM Answer Generation
    │   ├── ArkChatClient          # LLM客户端
    │   └── Prompt Templates       # 提示模板
    └── HallucinationGuard (幻觉检测)
        ├── check_product_existence()  # Layer 1
        ├── check_attribute_facts()    # Layer 2
        └── check_sql_grounding()      # Layer 3
```

---

## 常见修改模式

### 模式 1: 添加新的意图识别规则

**场景**: 用户说"送女朋友的"，系统要识别为 gift + female。

**文件**: `server/agent/semantic_rules.py`

```python
def build_rule_plan(message: str, session: SessionState) -> SemanticPlan:
    plan = SemanticPlan()
    
    # 新增规则
    if "送女朋友" in message or "送女生" in message:
        plan.filters.append(SemanticFilter(kind="facet", value="target_audience:female"))
        plan.filters.append(SemanticFilter(kind="facet", value="occasion:gift"))
    
    # ... 原有规则
    return plan
```

---

### 模式 2: 调整检索参数

**场景**: 觉得召回太少，想增加 BM25 的候选数量。

**文件**: `server/config.py`

```python
retrieval_candidate_multiplier: int = 3  # 3倍放大
retrieval_min_candidate_pool: int = 50   # 最少50个候选
```

---

### 模式 3: 修改 Prompt 模板

**场景**: 想让 LLM 回答更简洁。

**文件**: `server/llm/prompt.py`

```python
SYSTEM_PROMPT = (
    "你是电商导购助手..."
    "回答请控制在 200 字以内，只列关键信息。"  # 新增约束
)
```

---

### 模式 4: 添加新的 Handler

**场景**: 想添加一个"优惠券查询"Handler。

**文件**: `server/agent/default_handlers.py`

```python
class CouponHandler:
    def matches(self, context: AgentTurnContext) -> bool:
        return context.plan.intent == "coupon"
    
    async def handle(self, context: AgentTurnContext):
        # 查询优惠券
        coupons = await query_coupons(context.session.user_id)
        yield {"event": "coupons", "data": coupons}
```

然后在 `build_default_workflow()` 中注册：

```python
def build_default_workflow():
    return AgentWorkflow([
        ClarificationHandler(),
        CartHandler(),
        CouponHandler(),  # 新增
        CompareHandler(),
        # ...
    ])
```

---

## 调试技巧

### 1. 打印 Semantic Plan

```python
# 在 orchestrator.py 中
print(f"Intent: {plan.intent}")
print(f"Query: {plan.query}")
print(f"Filters: {[(f.kind, f.value) for f in plan.filters]}")
print(f"Confidence: {plan.confidence}")
```

### 2. 打印检索结果

```python
# 在 recommendation_handler.py 中
print(f"Found {len(cards)} cards")
for card in cards[:3]:
    print(f"  {card['id']}: {card['name']} ¥{card['price']}")
```

### 3. 打印 LLM 输出

```python
# 在 collect_llm_answer 中
print(f"LLM answer ({len(answer)} chars): {answer[:200]}...")
```

### 4. 查看 Session 状态

```python
# 任意地方
print(f"Session filters: {session.filters}")
print(f"Pending subject: {session.pending_subject}")
print(f"Cart: {len(session.cart)} items")
```
