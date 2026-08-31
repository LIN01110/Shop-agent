# Shop-Agent Phase 5 修复 + 冗余清理方案

> 日期：2026-08-07 | 基于冲突报告 CONFLICT_REPORT.md

---

## 一、冗余文件清单

### 1. 🔴 server/api/cart.py — 删除

**现状**：旧的内存购物车 REST API，操作 `session.cart`（内存字典列表）。

**冗余原因**：新的 `server/api/cart_db.py` 已实现同样的 `/cart` 前缀路由，但基于 MySQL 持久化。

**冲突**：两个文件都注册 `/cart` 前缀路由，同时注册会导致路由覆盖（后注册的覆盖先注册的），行为不可预测。

**删除后影响**：无。新 API 完全覆盖旧功能，且新增了持久化、选中状态、失效标记等。

---

### 2. 🔴 CartItemORM 在 server/api/cart_db.py 中 — 迁移后删除内联定义

**现状**：`CartItemORM` 定义在 API 路由文件里。

**冗余原因**：ORM 模型应该统一放在 `server/models/` 目录下，与其他表（users, skus, orders, coupons）保持一致。

**处理**：新建 `server/models/cart.py`，把 `CartItemORM` 迁移过去，`cart_db.py` 改为从 models 导入。

---

### 3. 🟡 server/commerce/services.py 中的 mock 定价/库存 — 改造（非删除）

**现状**：`LocalMockPricingService` / `LocalMockInventoryService` / `LocalMockPromotionService` 基于 hash 伪随机生成价格/库存/优惠券。

**冗余原因**：新的 `server/services/pricing_service.py` 和 `inventory_service.py` 提供了真实数据查询。

**不直接删除的原因**：
- 当 `enable_user_system=False` 时，系统应该仍能运行（mock 数据兜底）
- `LocalMockPolicyService` / `LocalMockLogisticsService` 暂无替代实现

**改造方案**：修改 `create_local_mock_commerce_gateway()`，让它根据配置选择数据源：
```python
def create_commerce_gateway(settings):
    if settings.enable_user_system and mysql_available:
        return CommerceDataGateway(CommerceServices(
            catalog=LocalCatalogService(),
            pricing=MySQLPricingService(db_pool),      # 新增桥接
            inventory=MySQLInventoryService(db_pool),   # 新增桥接
            promotion=MySQLPromotionService(db_pool),   # 新增桥接
            ...
        ))
    else:
        return create_local_mock_commerce_gateway(...)  # 原有 mock
```

---

### 4. 🟢 server/tools/cart.py — 保留，追加 DB 同步

**现状**：Agent 的购物车工具，所有对话中加购/删购/改数量都走这里。

**不删除的原因**：这是 Agent 的核心工具，删除后对话流程无法加购。

**改造方案**：在执行完内存购物车操作后，追加一行同步到 DB（如果用户已登录）。

---

### 5. 🟢 server/session/state.py — 保留

**现状**：`SessionState` 定义内存购物车 `cart: list[dict]`。

**不删除的原因**：
- 匿名用户仍需内存购物车
- 登录后需要从 `session.cart` 合并到 DB
- 对话上下文需要 `session.cart` 展示给用户

---

## 二、修复任务清单（按依赖顺序）

| 顺序 | 任务 | 涉及文件 | 操作 |
|------|------|----------|------|
| 1 | 新建 `server/models/cart.py` 迁移 CartItemORM | 新建 `models/cart.py`，修改 `api/cart_db.py` | 迁移 |
| 2 | 删除冗余 `server/api/cart.py` | `api/cart.py` | 删除 |
| 3 | 在 `main.py` 注册新 API 路由 | `main.py` | 修改 |
| 4 | lifespan 管理 MySQL+Redis 连接 | `main.py` | 修改 |
| 5 | CartTool 同步写 DB | `tools/cart.py` | 修改 |
| 6 | CommerceGateway 接入 MySQL | `commerce/services.py` | 修改 |
| 7 | 登录合并匿名购物车 | `api/auth.py` | 修改 |
| 8 | 提交并更新 CONFLICT_REPORT | - | 提交 |

---

## 三、删除确认清单

请确认以下文件/代码是否同意删除/迁移：

| # | 文件/代码 | 操作 | 理由 |
|---|-----------|------|------|
| ✅ | `server/api/cart.py` | **删除** | 被 `cart_db.py` 完全取代，路由冲突 |
| ✅ | `server/api/cart_db.py` 中的 `CartItemORM` 内联定义 | **迁移到 `models/cart.py`** | ORM 统一归 models 层管理 |
| ❌ | `server/commerce/services.py` 中的 mock 服务 | **保留改造** | 作为兜底回退，enable_user_system=False 时仍需 |
| ❌ | `server/tools/cart.py` | **保留改造** | Agent 核心工具，需追加 DB 同步 |
| ❌ | `server/session/state.py` | **保留** | 匿名用户购物车 + 登录合并源 |

---

## 四、改造后架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Agent 对话流                          │
│  用户: "加入购物车"                                          │
│    → CartTool (tools/cart.py)                               │
│    → session.cart (内存，兼容旧逻辑)                         │
│    → 如果已登录 → sync_cart_to_db() → MySQL cart_items      │
├─────────────────────────────────────────────────────────────┤
│                        Web/App 流                           │
│  用户点击加购 → /cart/add (api/cart_db.py)                   │
│    → 直接写 MySQL cart_items                                 │
├─────────────────────────────────────────────────────────────┤
│                        价格查询                              │
│  Agent: "多少钱" → CommerceDataGateway                       │
│    → 配置=mysql: 查 sku_prices 表                            │
│    → 配置=mock: 回退 hash 伪随机                             │
└─────────────────────────────────────────────────────────────┘
```

---

确认后开始执行：先迁移 CartItemORM → 删除旧 cart.py → 修复 5 个文件 → 提交。
