# Shop-Agent 用户系统 + 真实数据 + 优惠券系统 — 总体设计方案 v2

> 版本：v2.0 | 日期：2026-08-07
> 状态：已根据 7 个生产级问题修正

---

## 一、数据源现状

| 字段 | 现状 | 是否真实 |
|------|------|----------|
| `id` | `p_beauty_001` 等 | ✅ 真实 |
| `name` | 雅诗兰黛小棕瓶... | ✅ 真实 |
| `brand` | 雅诗兰黛/兰蔻... | ✅ 真实 |
| `price` | 720.0 / 760.0... | ✅ 参考价（无 SKU 级） |
| `stock` | 固定 100 | ❌ Mock |
| `sku_options` | 容量: 30ml/50ml/75ml | ✅ 真实规格 |

**结论：** 商品目录真实，库存 mock，缺 SKU 级独立价格/库存。先自建表，后期对接 API。

---

## 二、MySQL + Redis 架构

```
Client
  │
  ▼
FastAPI（无状态）
  │
  ├── Redis Cache（热点数据，TTL 5min）
  ├── Redis Session（JWT 黑名单）
  ├── Redis Rate Limit（API 限流）
  └── Redis Lock（分布式锁，领券/下单）
  │
  ▼
MySQL Master（写）
  │
  ▼
MySQL Slave xN（读，预留）
```

---

## 三、核心表设计（10 张表，全部 BIGINT 物理主键）

### 3.1 物理主键 vs 业务键规则

- **所有表**：`id BIGINT PRIMARY KEY AUTO_INCREMENT`（物理主键，聚簇索引）
- **业务键**：`user_id VARCHAR(32) UNIQUE`、`sku_id VARCHAR(64) UNIQUE` 等（逻辑键，不用于外键）
- **外键引用**：全部用 `BIGINT id`，不用 VARCHAR
- **原因**：InnoDB 聚簇索引，VARCHAR 主键比 BIGINT 慢 3~5 倍，索引膨胀

### 3.2 用户表

```sql
CREATE TABLE users (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,     -- 物理主键（所有外键引用它）
    user_id         VARCHAR(32) NOT NULL UNIQUE,            -- 业务 UUID（对外暴露）
    username        VARCHAR(50) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,                  -- bcrypt
    phone           VARCHAR(20),
    email           VARCHAR(100),
    avatar_url      VARCHAR(500),
    status          TINYINT DEFAULT 1,                      -- 0禁用 1正常
    is_deleted      BOOLEAN DEFAULT FALSE,                  -- 软删除
    deleted_at      DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login_at   DATETIME,
    
    -- 联合唯一：手机号 + 未删除（允许注销后重新注册同手机号）
    UNIQUE KEY uk_phone_active (phone, is_deleted),
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.3 用户画像

```sql
CREATE TABLE user_profiles (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT NOT NULL UNIQUE,                 -- 引用 users.id
    preferred_brands JSON,
    price_range_min DECIMAL(10,2),
    price_range_max DECIMAL(10,2),
    skin_type       VARCHAR(20),
    age_group       VARCHAR(20),
    search_history  JSON,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

### 3.4 SKU 主表

```sql
CREATE TABLE skus (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    sku_id          VARCHAR(64) NOT NULL UNIQUE,            -- 业务编码：sku_{product_id}_{spec_hash}
    product_id      VARCHAR(32) NOT NULL,
    product_name    VARCHAR(255) NOT NULL,
    brand           VARCHAR(50) NOT NULL,
    category        VARCHAR(50) NOT NULL,
    spec_json       JSON NOT NULL,                          -- {"容量":"30ML"}
    spec_display    VARCHAR(100) NOT NULL,                  -- "30ML 经典装"
    image_url       VARCHAR(500),
    status          TINYINT DEFAULT 1,                      -- 0下架 1上架
    is_deleted      BOOLEAN DEFAULT FALSE,
    deleted_at      DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_product (product_id),
    INDEX idx_brand (brand),
    INDEX idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**SKU 编码规范：**
```python
# 规格排序：颜色 → 尺码 → 容量 → 其他（按字母序）
# 值处理：转大写、去空格、/ → -、& → AND
# 示例：{"容量": "30ml", "颜色": "黑色"} → "颜色:黑色_容量:30ML"
# sku_id = f"sku_{product_id}_{normalize_spec(specs)}"
```

### 3.5 SKU 价格表

```sql
CREATE TABLE sku_prices (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    price_id        VARCHAR(64) NOT NULL UNIQUE,            -- 业务ID（保留可读性）
    sku_id          BIGINT NOT NULL,                        -- 引用 skus.id
    price           DECIMAL(10,2) NOT NULL,
    original_price  DECIMAL(10,2),
    currency        VARCHAR(3) DEFAULT 'CNY',
    effective_from  DATETIME NOT NULL,
    effective_to    DATETIME,                               -- NULL=永久生效
    promotion_tag   VARCHAR(100),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    -- 一个 SKU 在一个时间点只能有一个价格
    UNIQUE KEY uk_sku_time (sku_id, effective_from),
    -- 快速查当前价格
    INDEX idx_sku_active (sku_id, is_active, effective_from DESC),
    FOREIGN KEY (sku_id) REFERENCES skus(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.6 SKU 库存表

```sql
CREATE TABLE sku_inventory (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    sku_id          BIGINT NOT NULL UNIQUE,                 -- 引用 skus.id
    available_qty   INT NOT NULL DEFAULT 0,
    reserved_qty    INT NOT NULL DEFAULT 0,                 -- 已下单未付款
    sold_qty        INT NOT NULL DEFAULT 0,
    warning_level   INT DEFAULT 10,
    last_updated    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (sku_id) REFERENCES skus(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.7 购物车

```sql
CREATE TABLE cart_items (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT NOT NULL,                        -- 引用 users.id
    sku_id          BIGINT NOT NULL,                        -- 引用 skus.id
    quantity        INT NOT NULL DEFAULT 1,
    selected        BOOLEAN DEFAULT TRUE,                   -- 是否勾选结算
    invalid_reason  VARCHAR(100),                          -- NULL=正常, 'out_of_stock'/'off_shelf'
    added_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_user_sku (user_id, sku_id),
    INDEX idx_user_selected (user_id, selected),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (sku_id) REFERENCES skus(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.8 订单

```sql
CREATE TABLE orders (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id        VARCHAR(32) NOT NULL UNIQUE,            -- 业务单号 O{timestamp}{random}
    user_id         BIGINT NOT NULL,
    total_amount    DECIMAL(12,2) NOT NULL,                 -- 商品总金额
    discount_amount DECIMAL(12,2) DEFAULT 0,
    pay_amount      DECIMAL(12,2) NOT NULL,
    status          TINYINT DEFAULT 1,                      -- 1待付 2已付 3发货 4完成 5取消
    coupon_code     VARCHAR(32),                            -- 使用的券
    idempotency_key VARCHAR(64),                            -- 幂等键
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    paid_at         DATETIME,
    cancelled_at    DATETIME,
    is_deleted      BOOLEAN DEFAULT FALSE,
    
    INDEX idx_user (user_id),
    INDEX idx_status (status),
    INDEX idx_idempotency (idempotency_key),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_items (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id        BIGINT NOT NULL,                        -- 引用 orders.id
    sku_id          BIGINT NOT NULL,
    product_name    VARCHAR(255),
    spec_display    VARCHAR(100),
    quantity        INT NOT NULL,
    unit_price      DECIMAL(10,2) NOT NULL,                 -- 下单时快照
    total_price     DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    INDEX idx_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.9 优惠券模板

```sql
CREATE TABLE coupon_templates (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    template_id     VARCHAR(32) NOT NULL UNIQUE,
    name            VARCHAR(100) NOT NULL,
    description     VARCHAR(255),
    type            TINYINT NOT NULL,                      -- 1满减 2折扣 3立减
    scope           TINYINT DEFAULT 1,                     -- 1全平台 2品类 3单品
    threshold       DECIMAL(10,2) DEFAULT 0,
    discount_amount DECIMAL(10,2),                         -- 减多少
    discount_rate   DECIMAL(3,2),                          -- 折扣率
    max_discount    DECIMAL(10,2),
    total_count     INT NOT NULL,                          -- 总发行量
    remaining_count INT NOT NULL,                          -- 剩余库存
    user_limit      INT DEFAULT 1,
    applicable_skus JSON,
    applicable_categories JSON,
    stackable       BOOLEAN DEFAULT FALSE,
    status          TINYINT DEFAULT 1,
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_type (type),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.10 用户优惠券

```sql
CREATE TABLE user_coupons (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    coupon_code     VARCHAR(32) NOT NULL UNIQUE,
    user_id         BIGINT NOT NULL,
    template_id     VARCHAR(32) NOT NULL,
    status          TINYINT DEFAULT 1,                     -- 1未用 2已冻结 3已使用
    freeze_time     DATETIME,                              -- 冻结时间（30分钟倒计时起点）
    order_id        VARCHAR(32),                           -- 关联订单
    use_time        DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_deleted      BOOLEAN DEFAULT FALSE,
    
    INDEX idx_user_status (user_id, status),
    INDEX idx_order (order_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE coupon_usage_logs (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    coupon_code     VARCHAR(32) NOT NULL,
    user_id         BIGINT NOT NULL,
    order_id        VARCHAR(32) NOT NULL,
    order_amount    DECIMAL(12,2) NOT NULL,
    discount_amount DECIMAL(12,2) NOT NULL,
    use_time        DATETIME NOT NULL,
    INDEX idx_coupon (coupon_code),
    INDEX idx_order (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.11 幂等键表

```sql
CREATE TABLE idempotency_keys (
    key_hash        VARCHAR(64) PRIMARY KEY,                -- SHA256(客户端key)
    user_id         BIGINT NOT NULL,
    action          VARCHAR(50) NOT NULL,                   -- 'create_order', 'claim_coupon'
    request_body    JSON,
    response_body   JSON,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at      DATETIME                                -- 24小时后过期
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.12 商家表

```sql
CREATE TABLE merchants (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    merchant_id     VARCHAR(32) NOT NULL UNIQUE,
    name            VARCHAR(100) NOT NULL,
    logo_url        VARCHAR(500),
    rating          DECIMAL(2,1) DEFAULT 5.0,
    total_sales     INT DEFAULT 0,
    status          TINYINT DEFAULT 1,
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE sku_merchants (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    sku_id          BIGINT NOT NULL,
    merchant_id     BIGINT NOT NULL,
    merchant_price  DECIMAL(10,2) NOT NULL,
    commission_rate DECIMAL(4,3) DEFAULT 0,
    PRIMARY KEY (sku_id, merchant_id),
    FOREIGN KEY (sku_id) REFERENCES skus(id),
    FOREIGN KEY (merchant_id) REFERENCES merchants(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

## 四、事务 + 幂等 + 分布式锁

### 4.1 下单事务

```python
async def create_order(user_id: int, sku_items: list, coupon_code: str = None, idempotency_key: str = None):
    # 1. 幂等检查
    if idempotency_key:
        cached = await db.fetchone("SELECT response_body FROM idempotency_keys WHERE key_hash = ?", idempotency_key)
        if cached:
            return json.loads(cached.response_body)
    
    async with db.transaction():  # BEGIN
        # 2. 校验库存（加锁）
        for sku_id, qty in sku_items:
            row = await db.fetchone(
                "SELECT available_qty FROM sku_inventory WHERE sku_id = ? FOR UPDATE", sku_id
            )
            if not row or row.available_qty < qty:
                raise InsufficientStock(sku_id)
        
        # 3. 扣减库存
        for sku_id, qty in sku_items:
            await db.execute(
                "UPDATE sku_inventory SET available_qty = available_qty - ?, reserved_qty = reserved_qty + ? WHERE sku_id = ?",
                qty, qty, sku_id
            )
        
        # 4. 创建订单
        order_id = generate_order_id()
        await db.execute("INSERT INTO orders (...) VALUES (...)", ...)
        await db.execute("INSERT INTO order_items (...) VALUES (...)", ...)
        
        # 5. 冻结券
        if coupon_code:
            await db.execute(
                "UPDATE user_coupons SET status = 2, freeze_time = NOW(), order_id = ? WHERE coupon_code = ? AND status = 1",
                order_id, coupon_code
            )
            await redis.setex(f"coupon_freeze:{order_id}", 1800, coupon_code)
    
    # 6. 缓存幂等结果
    if idempotency_key:
        await db.execute(
            "INSERT INTO idempotency_keys (...) VALUES (...)",
            idempotency_key, user_id, 'create_order', ..., ..., DATE_ADD(NOW(), INTERVAL 1 DAY)
        )
```

### 4.2 并发领券（Redis 分布式锁）

```python
async def claim_coupon(user_id: int, template_id: str):
    lock_key = f"lock:coupon:{template_id}:{user_id}"
    
    acquired = await redis.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        raise TooManyRequests("操作太快，请稍后再试")
    
    try:
        # 查已领数量
        claimed = await db.fetchval(
            "SELECT COUNT(*) FROM user_coupons WHERE user_id = ? AND template_id = ? AND is_deleted = FALSE",
            user_id, template_id
        )
        template = await db.fetchone("SELECT user_limit, remaining_count FROM coupon_templates WHERE template_id = ?", template_id)
        
        if claimed >= template.user_limit:
            raise LimitExceeded("已达领取上限")
        
        # Redis 原子扣减
        remaining = await redis.decr(f"coupon_stock:{template_id}")
        if remaining < 0:
            await redis.incr(f"coupon_stock:{template_id}")
            raise OutOfStock("券已领完")
        
        # 写数据库
        await db.execute("INSERT INTO user_coupons (...)", ...)
        
    finally:
        await redis.delete(lock_key)
```

---

## 五、优惠券冻结与释放（Redis 倒计时）

### 5.1 设计

- 券**无限期使用**，不设置 `valid_end`
- 下单时冻结券，同时 Redis 设置 **30 分钟过期键**
- 30 分钟内支付 → 券正式使用
- 30 分钟未支付 / 用户取消 → 券自动释放

### 5.2 实现

```python
# 下单冻结
await redis.setex(f"coupon_freeze:{order_id}", 1800, coupon_code)

# 支付成功（取消倒计时）
await redis.delete(f"coupon_freeze:{order_id}")

# 用户取消（立即释放）
await redis.delete(f"coupon_freeze:{order_id}")
await db.execute("UPDATE user_coupons SET status = 1, freeze_time = NULL WHERE order_id = ?", order_id)

# 兜底：订单查询时检查冻结是否已过期
async def check_expired_freeze(order_id: str):
    exists = await redis.exists(f"coupon_freeze:{order_id}")
    if not exists:
        # Redis key 已过期，释放券
        order = await db.fetchone("SELECT status, coupon_code FROM orders WHERE order_id = ?", order_id)
        if order and order.status == 1:  # 待付款
            await db.execute(
                "UPDATE user_coupons SET status = 1, freeze_time = NULL, order_id = NULL WHERE coupon_code = ?",
                order.coupon_code
            )
```

---

## 六、文件清单（20 个新文件 + 3 个修改）

| # | 文件 | 说明 |
|---|------|------|
| 1 | `server/database/mysql_client.py` | MySQL 连接池 + SQLAlchemy |
| 2 | `server/database/redis_client.py` | Redis 连接 |
| 3 | `server/models/user.py` | Pydantic 用户模型 |
| 4 | `server/models/sku.py` | SKU / 价格 / 库存模型 |
| 5 | `server/models/order.py` | 订单模型 |
| 6 | `server/models/coupon.py` | 优惠券模型 |
| 7 | `server/auth/jwt_handler.py` | JWT 签发/验证/黑名单 |
| 8 | `server/auth/password.py` | bcrypt 密码哈希 |
| 9 | `server/api/auth.py` | 登录/注册/登出 API |
| 10 | `server/api/user.py` | 用户信息/画像 API |
| 11 | `server/api/cart_db.py` | 持久化购物车 API |
| 12 | `server/api/orders.py` | 订单创建/查询/取消 API |
| 13 | `server/api/coupons.py` | 领券/用券/查券 API |
| 14 | `server/services/pricing_service.py` | 真实价格查询 |
| 15 | `server/services/inventory_service.py` | 真实库存查询 |
| 16 | `server/services/coupon_engine.py` | 优惠券计算引擎 |
| 17 | `server/services/promotion_engine.py` | 满减推荐引擎 |
| 18 | `server/static/login.html` | 登录页面 |
| 19 | `server/scripts/init_db.sql` | 数据库初始化 |
| 20 | `server/scripts/migrate_products.py` | JSON 数据导入 |
| 改1 | `server/config.py` | 追加 MySQL/Redis/JWT 配置 |
| 改2 | `server/app_container.py` | 追加数据库/认证服务 |
| 改3 | `server/requirements.txt` | 追加依赖 |

---

## 七、确认问题速答

| # | 问题 | 答案 |
|---|------|------|
| 1 | VARCHAR 主键 | 全表改 `BIGINT AUTO_INCREMENT`，VARCHAR 做 `UNIQUE` |
| 2 | price_id | 自增主键 + `UNIQUE price_id` + `idx_sku_active` 索引 |
| 3 | SKU 编码 | 规格按固定顺序、大写、去空格、`/` 改 `-` |
| 4 | 事务/幂等/锁 | `BEGIN...COMMIT` + `idempotency_keys` 表 + Redis SETNX |
| 5 | 券冻结释放 | Redis `SETEX 1800`，无限期使用，支付成功/取消时释放 |
| 6 | 购物车 | 加 `selected`/`invalid_reason`/`added_at` |
| 7 | 软删除 | `is_deleted` + `deleted_at` + 联合唯一 `UNIQUE(phone, is_deleted)` |
