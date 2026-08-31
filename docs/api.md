# API 说明

## `GET /health`

返回服务状态。

```json
{
  "status": "ok",
  "env": "development",
  "debug_api_enabled": true,
  "session_backend": "memory"
}
```

## `POST /chat`

请求：

```json
{
  "session_id": "demo",
  "message": "推荐一款适合油皮的洗面奶，预算100以内",
  "image_base64": "",
  "image_mime_type": "",
  "image_filename": ""
}
```

`image_*` 字段可选。上传图片时，后端会先做商品主图相似匹配，再把图片线索并入同一条 RAG 链路。

响应为 SSE：

```text
event: token
data: {"text":"根"}

event: product_card
data: {"id":"p001","name":"清爽控油氨基酸洗面奶","price":79,"product_types":["beauty.cleansing_oil"],"image_url":"http://127.0.0.1:8000/assets/products/p001_live.jpg","detail_url":"http://127.0.0.1:8000/products/p001"}

event: done
data: {"session_id":"demo","filters":[],"exclusions":[],"needs_clarification":false,"pending_subject":""}
```

## 事件类型

| 事件 | 用途 |
|---|---|
| `token` | 文本增量渲染 |
| `product_card` | 商品卡片 |
| `done` | 本轮结束 |
| `cart_update` | 购物车状态变化 |
| `comparison_card` | 多商品对比结构化结果 |
| `image_analysis` | 图片找货的视觉摘要和相似商品匹配 |
| `guardrail` | LLM 回答被后校验拦截或降级的审计事件 |

`done` 事件中的 `needs_clarification=true` 表示本轮是主动澄清，不会返回商品卡片；`pending_subject` 会保留待补全的商品主题，例如“手机”。

购物车请求会额外返回 `cart_update`，例如：

```text
event: cart_update
data: {
  "items": [
    {"product_id":"p_beauty_021","name":"科颜氏牛油果保湿眼霜","price":210,"quantity":1}
  ],
  "total_quantity": 1,
  "total_price": 210,
  "is_empty": false
}
```

对比请求会额外返回 `comparison_card`，例如：

```text
event: comparison_card
data: {
  "title": "商品对比",
  "query": "科颜氏和AHC哪个眼霜更适合干皮",
  "products": [
    {"id":"p_beauty_021","brand":"科颜氏","price":210,"strengths":["保湿"]},
    {"id":"p_beauty_016","brand":"AHC","price":139,"strengths":["保湿","修护"]}
  ],
  "recommendation": {"product_id":"p_beauty_021","focus":"保湿/干皮"}
}
```

图片请求会先返回 `image_analysis`，例如：

```text
event: image_analysis
data: {
  "summary": "上传图片最像商品“ Nike Air Zoom Pegasus 41 ...”，品牌 耐克，类目 服饰运动，商品类型 运动鞋，视觉相似度 1.00。",
  "matches": [
    {"id":"p_clothes_007","name":"Nike Air Zoom Pegasus 41 ...","similarity":1.0}
  ]
}
```

如果 LLM 回答触发防幻觉后校验，后端会返回 `guardrail`，随后流式输出安全降级后的 grounded 回答，例如：

```text
event: guardrail
data: {
  "action": "fallback",
  "violations": ["unsupported_promotion_terms:优惠券", "unsupported_prices:99"]
}
```

## 静态资源

### `GET /assets/products/{filename}`

返回参考集商品主图，例如：

```text
GET /assets/products/p_beauty_021_live.jpg
```

商品卡片中的 `image_url` 会直接指向该路径。真机调试时需要先执行：

```powershell
D:\Android\Sdk\platform-tools\adb.exe reverse tcp:8000 tcp:8000
```

## 商品详情页

### `GET /products/{product_id}`

返回本地商品详情 HTML 页面，例如：

```text
GET /products/p_beauty_021
```

商品卡片中的 `detail_url` 会指向该页面。当前页面展示商品主图、名称、品牌、类目、价格、库存、标签、规格和商品说明。

## 调试 Trace

`/debug` 接口仅用于开发和排查问题。`APP_ENV=production` 时默认不挂载；如需显式控制，可设置 `ENABLE_DEBUG_API=true|false`。生产环境不要对公网开放 trace，因为其中包含用户消息、语义计划、过滤条件和商品命中信息。

### `GET /debug/traces`

返回最近的 Agent trace，用于排查每轮对话的语义规划、handler 分派、过滤条件和结构化事件。

可选参数：

| 参数 | 说明 |
|---|---|
| `session_id` | 只查看指定会话 |
| `limit` | 返回条数，默认 20，最大 100 |

示例：

```text
GET /debug/traces?session_id=demo&limit=5
```

返回字段包括：

```json
{
  "trace_id": "8f1c...",
  "session_id": "demo",
  "message": "科颜氏和AHC哪个眼霜更适合干皮",
  "handler": "CompareHandler",
  "plan": {"intent": "compare", "filters": [{"kind": "keyword", "value": "保湿"}]},
  "query": "科颜氏和AHC哪个眼霜更适合干皮 保湿",
  "filters": {"max_price": null, "keywords": ["眼霜", "保湿"], "product_types": ["beauty.eye_cream"], "exclusions": []},
  "event_counts": {"token": 120, "product_card": 2, "comparison_card": 1, "done": 1},
  "product_ids": ["p_beauty_021", "p_beauty_016"],
  "comparison_product_ids": ["p_beauty_021", "p_beauty_016"],
  "cart_total_quantity": null,
  "duration_ms": 1300.5
}
```

### `GET /debug/traces/{trace_id}`

返回单条 trace。找不到时返回 404。
