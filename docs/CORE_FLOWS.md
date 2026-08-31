# Shop_Agent 核心流程详解

> 深度解析一次用户请求在系统中的完整流转。配合 `PROJECT_OVERVIEW.md` 阅读。

---

## 流程总图

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   用户输入   │────→│  Orchestrator │────→│ SemanticPlanner│
│ "推荐手机"   │     │  (编排大脑)   │     │  (意图解析)   │
└─────────────┘     └──────┬──────┘     └──────┬──────┘
                           │                    │
                           ↓                    ↓
                    ┌─────────────┐     ┌─────────────┐
                    │ InputProc   │     │ Rule Plan   │
                    │ (分词/关键词)│     │ (快速匹配)  │
                    └──────┬──────┘     └──────┬──────┘
                           │                    │
                           └────────┬───────────┘
                                    ↓
                           ┌─────────────┐
                           │  LLM Plan   │
                           │ (0.8s超时)  │
                           └──────┬──────┘
                                  ↓
                           ┌─────────────┐
                           │ Merge Plans │
                           │ (合并规则+LLM)│
                           └──────┬──────┘
                                  ↓
                    ┌─────────────────────────────┐
                    │      Query Rewriter         │
                    │  (模糊检测→规则补全→LLM重写)  │
                    └─────────────────────────────┘
                                  ↓
                    ┌─────────────────────────────┐
                    │     Product Search (RAG)    │
                    │  BM25 → Vector → CrossEncoder│
                    └─────────────────────────────┘
                                  ↓
                    ┌─────────────────────────────┐
                    │   LLM Answer Generation     │
                    │   (deepseek/doubao-lite)    │
                    └─────────────────────────────┘
                                  ↓
                    ┌─────────────────────────────┐
                    │     Hallucination Guard     │
                    │   (三层校验→自动修正/降级)   │
                    └─────────────────────────────┘
                                  ↓
                           ┌─────────────┐
                           │   返回用户   │
                           │  文本+卡片  │
                           └─────────────┘
```

---

## 步骤详解

### Step 1: Input Processor

**文件**: `server/inputs/base.py` → `TextProcessor.process()`

**做什么**:
- 分词（jieba）
- 提取关键词
- 检测商品名、品牌、价格、数量

**输出**:
```python
ProcessedInput(
    text="推荐一款2000元左右的手机",
    tokens=["推荐", "一款", "2000元", "左右", "手机"],
    entities=[Entity(type="price", value="2000", unit="元")],
)
```

---

### Step 2: Semantic Planner（双层）

**文件**: `server/agent/semantic_llm.py` → `SemanticPlanner.plan()`

#### 2a. 规则层（build_rule_plan）

**文件**: `server/agent/semantic_rules.py`

快速匹配，零 LLM 消耗：

```python
def build_rule_plan(message: str, session: SessionState) -> SemanticPlan:
    # 价格提取: "2000元左右" → max_price=2000
    # 品类提取: "手机" → product_type=phone
    # 品牌提取: "华为" → brand=华为
    # 数量提取: "两款" → quantity=2
```

**规则列表**（关键词 → 过滤条件）:
| 用户说 | 提取的 filter |
|--------|--------------|
| "2000元左右" | max_price=2000 |
| "便宜点" | 降低 max_price（当前预算的80%）|
| "华为的" | brand=华为 |
| "要轻量的" | keyword=轻量 |
| "只看有货的" | in_stock=true |

#### 2b. LLM 层（_try_llm_plan）

调用 deepseek，预算 0.8s：

```python
messages = [
    {"role": "system", "content": "你是电商导购 Agent 的语义解析器，只输出 JSON..."},
    {"role": "user", "content": "最近对话... 用户偏好... 当前输入: 推荐一款2000元左右的手机"},
]
```

**期望输出**:
```json
{
  "intent": "recommend",
  "query": "2000元以下手机",
  "filters": [
    {"kind": "max_price", "value": "2000"},
    {"kind": "product_type", "value": "phone"}
  ],
  "presentation_mode": "listing",
  "needs_search": true,
  "confidence": 0.95
}
```

#### 2c. 合并（merge_semantic_plans）

**逻辑**:
- confidence < 0.5 → 全用规则
- cart/compare/bundle → 优先规则（安全）
- filters 去重合并

---

### Step 3: Query Rewriter

**文件**: `server/agent/query_rewriter.py` → `rewrite_query_async()`

**三层处理**:

```
plan.query 明确？
  ├─ YES → 直接用
  ↓ NO
规则兜底（高确定性）
  ├─ pending_subject 存在 → "防晒霜 哪个好用"
  ├─ 已确认 product_type → "护肤品 便宜的"
  ├─ 最近候选 + 追问词 → "小棕瓶 怎么样"
  └─ user_profile 偏好 → "口红 推荐"
  ↓ 仍模糊
LLM 重写（doubao-lite，128 tokens）
  → "美白防晒霜 学生党 平价"
```

**模糊检测规则**:
- 长度 < 4 且无品类/品牌词
- 纯模糊词（"推荐一个""随便"）
- 仅含数字/量词（"300块"）

---

### Step 4: Product Search（三阶段检索）

**文件**: `server/agent/recommendation_handler.py` → `execute_product_search_with_budget()`

#### 阶段 1: BM25 粗排

**文件**: `server/rag/bm25_engine.py`

```python
# jieba 分词 → 倒排索引 → BM25 打分
candidates = bm25_search(query, top_k=50)
```

#### 阶段 2: 向量检索

**文件**: `server/rag/milvus_store.py`

```python
# 文本 Embedding → Milvus HNSW_SQ 检索
vector_results = milvus.search(
    embedding=embed(query),
    top_k=50,
    filter="price <= 2000 AND stock > 0"
)
```

#### 阶段 3: Cross-Encoder 精排

**文件**: `server/rag/cross_encoder.py`

```python
# 对 Top-20 候选重新打分
scores = cross_encoder.predict([
    (query, doc.name + " " + doc.description)
    for doc in candidates[:20]
])
final_results = sorted(candidates, key=lambda x: scores[x.id], reverse=True)[:5]
```

---

### Step 5: LLM Answer Generation

**文件**: `server/agent/recommendation_handler.py` → `collect_llm_answer()`

**模型选择**:
```python
if _should_use_lite_model(context, cards):
    client = context.lite_llm_client  # doubao-lite，便宜
else:
    client = context.llm_client       # deepseek，强
```

**Prompt 构建**:
```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": build_user_prompt(query, cards)},
]
```

**SYSTEM_PROMPT 核心约束**:
- 只推荐候选列表中的商品
- 不得编造价格、库存、优惠
- 不确定的信息说"具体以商品详情页为准"

---

### Step 6: Hallucination Guard

**文件**: `server/agent/hallucination_guard.py` → `guard_hallucination()`

#### Layer 1: 商品存在性

```python
# 检查回答中提到的商品是否都在 cards 中
for mention in extract_product_mentions(answer):
    if mention.id not in card_ids:
        violation = HallucinationViolation(
            layer=1, type="product_not_found",
            product=mention.name,
            claimed=mention.id, actual=None
        )
```

#### Layer 2: 属性事实

```python
# 价格精确匹配（零阈值）
if claimed_price != actual_price:
    auto_correct(answer, claimed_price, actual_price)

# 库存精确匹配
if claimed_stock != actual_stock:
    auto_correct(answer, claimed_stock, actual_stock)
```

#### Layer 3: SQL 校验（可选）

```python
# 从 MySQL 查询真实数据
real_data = mysql_bridge.query(product_id)
if answer_claim != real_data:
    flag_hallucination()
```

**处理闭环**:
```
检测通过 → 直接返回
价格/库存写错 → 自动替换
严重幻觉 → 纠错 Prompt → LLM 重生成（最多2次）
重生成失败 → 确定性降级模板
```

---

## 关键时序

```
用户输入
  │
  ├─→ Input Proc (本地，<10ms)
  │
  ├─→ Semantic Planner
  │   ├─ Rule Plan (<1ms)
  │   └─ LLM Plan (0.8s 超时)
  │
  ├─→ Query Rewriter
  │   ├─ Rule Rewrite (<1ms)
  │   └─ LLM Rewrite (小模型，~0.5s)
  │
  ├─→ Product Search
  │   ├─ BM25 (<50ms)
  │   ├─ Vector (<100ms)
  │   └─ Cross-Encoder (<200ms)
  │
  ├─→ LLM Answer (deepseek，1-3s)
  │
  └─→ Hallucination Guard (<10ms)
      └─→ 返回用户
```

**总耗时**: 简单查询 1-2s，复杂查询 3-5s

---

## 调试技巧

### 1. 看一次完整请求

```python
# 在 orchestrator.py stream_chat() 中加日志
logger.info("Step %d: %s -> %s", step, input, output)
```

### 2. 看 Semantic Plan

```python
# plan 对象结构
print(plan.intent)      # "recommend"
print(plan.query)       # "2000元以下手机"
print(plan.filters)     # [SemanticFilter(kind="max_price", value="2000")]
print(plan.confidence)  # 0.95
```

### 3. 看检索结果

```python
# context.metadata 中有完整信息
print(context.metadata["card_binding"])      # 卡片绑定信息
print(context.metadata["indexed_candidates"]) # 序号引用映射
print(context.metadata["llm_model"])          # "lite" 或 "pro"
```

### 4. 看幻觉检测结果

```python
print(context.metadata["hallucination"])
# {
#   "safe": false,
#   "action": "auto_correct",
#   "violations": [...]
# }
```
