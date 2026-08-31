# Shop-Agent Phase 5 代码兼容性冲突报告

> 日期：2026-08-07 | 状态：需修复

---

## 冲突总览

| 等级 | 冲突项 | 影响 | 状态 |
|------|--------|------|------|
| 🔴 **严重** | 新 API 路由未注册到 main.py | 用户系统完全无法访问 | 未修复 |
| 🔴 **严重** | 购物车双重实现（session.cart vs DB） | 数据不同步，用户困惑 | 未修复 |
| 🔴 **严重** | 价格/库存双重服务（mock vs MySQL） | Agent 显示假价格，API 显示真价格 | 未修复 |
| 🟡 **中等** | lifespan 未初始化/关闭 MySQL+Redis | 连接泄漏，启动慢 | 未修复 |
| 🟡 **中等** | CartItemORM 定义位置错误 | 架构混乱，重复定义风险 | 未修复 |
| 🟡 **中等** | 匿名 session 与 JWT 用户无桥接 | 登录前后购物车不合并 | 设计如此，需文档说明 |
| 🟢 **轻微** | 图片处理链无冲突 | use_structured_vlm=False，不影响原有流程 | ✅ 安全 |

---

## 详细冲突分析

### 🔴 冲突 1：新 API 路由未注册

**问题**：写了 5 个新 API 文件（auth.py / cart_db.py / orders.py / coupons.py / user.py），但 `server/main.py` 中一个都没有注册。

```python
# server/main.py — 当前注册的路由（没有新路由）
app.include_router(chat_router)
app.include_router(cart_router)       # ← 这是旧的内存购物车 API
app.include_router(products_router)
app.include_router(sessions_router)
app.include_router(uploads_router)
```

**后果**：所有新 API（/auth/login、/cart、/orders）返回 404，用户系统完全不可用。

**修复方案**：在 `server/main.py` 中条件注册新路由

```python
# 追加导入
from server.api.auth import router as auth_router
from server.api.cart_db import router as cart_db_router
from server.api.orders import router as orders_router
from server.api.coupons import router as coupons_router
from server.api.user import router as user_router

# 在 create_app() 中注册
if settings.enable_user_system:
    app.include_router(auth_router)
    app.include_router(cart_db_router)
    app.include_router(orders_router)
    app.include_router(coupons_router)
    app.include_router(user_router)
    # 静态文件登录页
    app.mount("/auth/login", StaticFiles(directory="server/static", html=True), name="login")
```

---

### 🔴 冲突 2：购物车双重实现 — 数据不同步

**现有逻辑**：
```
用户对话 → Agent(CartTool) → session.cart (内存 list[dict]) → session过期丢失
```

**新逻辑**：
```
App/Web → FastAPI(/cart/add) → MySQL cart_items 表 → 持久化
```

**冲突**：
1. 用户通过**对话**说"加入购物车" → 存入 `session.cart`（内存）
2. 用户通过**App**点击加购 → 存入 `cart_items`（MySQL）
3. 两者是**完全不同的数据**，购物车永远对不上

**更严重的问题**：旧的 `server/api/cart.py` 已经存在（内存购物车 API），新的 `server/api/cart_db.py` 是数据库购物车。命名差异让用户（和开发者）困惑。

**修复方案（推荐 B）**：

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| A | 删除旧 CartTool，Agent 直接调 DB | 统一数据源 | 改动大，Agent 需异步查 DB |
| **B** | **CartTool 读写都走 DB，session.cart 只作缓存** | 最小改动，数据统一 | 需要给 CartTool 注入 DB session |
| C | 保留双轨，登录时合并 | 兼容旧逻辑 | 复杂，易出 bug |

**推荐 B**：修改 `server/tools/cart.py`，让 `execute_cart_operations` 在修改 `session.cart` 的同时同步写入 DB（如果用户已登录）。

```python
# server/tools/cart.py 修改点
def execute_cart_operations(operations, *, session: SessionState, plan=None, user_id=None):
    # ... 原有逻辑 ...
    session.cart = working_cart
    
    # 追加：同步到数据库
    if user_id and settings.enable_user_system:
        sync_cart_to_db(user_id, working_cart)
```

---

### 🔴 冲突 3：价格/库存双重服务

**现有逻辑（Agent 使用）**：
```
Agent → CommerceDataGateway → LocalMockPricingService → 基于 hash 的伪随机价格
```

**新逻辑（API 使用）**：
```
API → pricing_service.py → MySQL sku_prices 表 → 真实价格
```

**冲突**：
- Agent 推荐商品时显示的价格 = mock（随机波动）
- 用户点击商品查看详情（走新 API）= 真实价格
- 同一个商品，对话里说 ¥720，点进去看 ¥699

**修复方案**：让 `CommerceDataGateway` 优先查 MySQL，回退到 mock

```python
# server/commerce/services.py 修改点
class MySQLPricingService:
    """新增：优先查 MySQL，找不到再回退 mock"""
    def __init__(self, db_session_factory, fallback: PricingService):
        self.db = db_session_factory
        self.fallback = fallback
    
    def price(self, product, sku_id=None):
        # 1. 查 MySQL
        sku_id_str = sku_id or product.get("sku_id")
        if sku_id_str:
            # 异步查 sku_prices 表...
            pass
        # 2. 回退 mock
        return self.fallback.price(product, sku_id)
```

**更简单的过渡方案**：配置开关控制
```python
# config.py
commerce_fact_backend: str = "mock"  # mock / mysql / hybrid
```

当 `commerce_fact_backend = "mysql"` 时，Agent 也走真实数据。

---

### 🟡 冲突 4： lifespan 未管理数据库连接

**问题**：`server/main.py` 的 lifespan 只有 `get_orchestrator()`，没有初始化/关闭 MySQL 和 Redis。

```python
# 当前 lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_orchestrator()  # ← 只初始化了 orchestrator
    yield               # ← 没有 init_db()，没有关闭连接
```

**后果**：
- 第一次请求时才创建连接池，首次请求慢
- 应用关闭时 MySQL/Redis 连接不释放，可能泄漏

**修复**：
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_orchestrator()
    
    if settings.enable_user_system:
        from server.database.mysql_client import init_db, close_db
        from server.database.redis_client import close_redis
        await init_db()  # 创建表
    
    yield
    
    if settings.enable_user_system:
        await close_db()
        await close_redis()
```

---

### 🟡 冲突 5：CartItemORM 定义位置错误

**问题**：`CartItemORM` 定义在 `server/api/cart_db.py` 中，而不是 `server/models/` 下。

**后果**：
- 架构混乱：ORM 应该统一在 models 层
- 其他模块（如 orders.py）如果需要引用购物车，会循环导入

**修复**：将 `CartItemORM` 迁移到 `server/models/cart.py`

---

### 🟡 冲突 6：匿名 session 与 JWT 无桥接

**问题**：原有系统完全基于匿名 `session_id`，新系统要求 JWT 登录。

**场景**：
1. 用户匿名浏览，Agent 对话中加购 → 存入 `session.cart`
2. 用户登录 → JWT 签发
3. 但 `session.cart` 中的商品没有合并到 `user_id` 的 DB 购物车

**这不是代码冲突，是设计缺口。** 需要在登录接口中增加合并逻辑。

**修复**：在 `auth.py` 登录成功后合并匿名购物车
```python
async def login(req, db):
    # ... 验证密码 ...
    
    # 合并匿名购物车（如果前端传了 session_id）
    if req.session_id:
        anon_cart = session_store.get(req.session_id).cart
        await merge_cart_to_db(user.id, anon_cart)
    
    return TokenResponse(...)
```

---

### 🟢 冲突 7：图片处理链 — 安全

**状态**：✅ 无冲突

原有图片处理链：
```
上传图片 → visual signature 匹配 (12×12 RGB) → 可选 VLM → 结果
```

Phase 1 新增的 `VLMStructuredProvider`：
- 通过 `config.use_structured_vlm` 控制，默认 `False`
- 即使文件存在，只要不开启配置，不会影响原有流程

**但有一个潜在问题**：`multimodal.py` 中导入了 `VLMStructuredProvider`，如果该文件有语法错误，会导致整个 multimodal 模块无法加载。需要验证 `vlm_structured.py` 能否独立导入。

---

## 修复任务清单

| # | 任务 | 优先级 | 文件 |
|---|------|--------|------|
| 1 | 新 API 路由注册到 main.py | 🔴 P0 | `server/main.py` |
| 2 | CartItemORM 迁移到 models/cart.py | 🔴 P0 | `server/models/cart.py` 新建 |
| 3 | lifespan 初始化/关闭 MySQL+Redis | 🟡 P1 | `server/main.py` |
| 4 | CartTool 同步写入 DB | 🟡 P1 | `server/tools/cart.py` |
| 5 | CommerceDataGateway 接入 MySQL | 🟡 P1 | `server/commerce/services.py` |
| 6 | 登录时合并匿名购物车 | 🟡 P1 | `server/api/auth.py` |
| 7 | 验证 vlm_structured.py 导入安全 | 🟢 P2 | `server/inputs/vlm_structured.py` |

---

## 修复后数据流

```
用户对话（Agent）:
  用户: "加入购物车" → CartTool → 写 session.cart → 如果已登录 → 同步写 DB cart_items
  用户: "多少钱" → Agent → CommerceDataGateway → 优先查 MySQL → 回退 mock

App/Web 操作:
  用户: POST /cart/add → cart_db API → 写 DB cart_items
  用户: GET /cart → 读 DB cart_items
  用户: POST /orders/create → 读 DB 价格/库存 → 事务创建订单
```

---

## 决策点

1. **是否立即修复冲突 2-6？** 还是保持 "用户系统默认关闭，开启时才修复"？
2. **CommerceDataGateway 改造方式**：是新增 MySQLPricingService 注入，还是直接改 LocalMockPricingService 让它先查 DB？
3. **匿名购物车合并**：是否在本次修复中实现，还是作为后续迭代？
