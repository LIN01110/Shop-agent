-- ============================================
-- Shop-Agent 数据库初始化脚本
-- MySQL 8.0+
-- ============================================

CREATE DATABASE IF NOT EXISTS shop_agent 
    DEFAULT CHARACTER SET utf8mb4 
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE shop_agent;

-- ============================================
-- 1. 用户表
-- ============================================
CREATE TABLE users (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         VARCHAR(32) NOT NULL UNIQUE,
    username        VARCHAR(50) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    phone           VARCHAR(20),
    email           VARCHAR(100),
    avatar_url      VARCHAR(500),
    status          TINYINT DEFAULT 1 COMMENT '0-禁用 1-正常',
    is_deleted      BOOLEAN DEFAULT FALSE,
    deleted_at      DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login_at   DATETIME,
    INDEX idx_username (username),
    UNIQUE KEY uk_phone_active (phone, is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- ============================================
-- 2. 用户画像
-- ============================================
CREATE TABLE user_profiles (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT NOT NULL UNIQUE,
    preferred_brands JSON,
    price_range_min BIGINT COMMENT '单位：分',
    price_range_max BIGINT COMMENT '单位：分',
    skin_type       VARCHAR(20),
    age_group       VARCHAR(20),
    search_history  JSON,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 3. SKU 主表
-- ============================================
CREATE TABLE skus (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    sku_id          VARCHAR(64) NOT NULL UNIQUE,
    product_id      VARCHAR(32) NOT NULL,
    product_name    VARCHAR(255) NOT NULL,
    brand           VARCHAR(50) NOT NULL,
    category        VARCHAR(50) NOT NULL,
    spec_json       JSON NOT NULL,
    spec_display    VARCHAR(100) NOT NULL,
    image_url       VARCHAR(500),
    status          TINYINT DEFAULT 1 COMMENT '0-下架 1-上架',
    is_deleted      BOOLEAN DEFAULT FALSE,
    deleted_at      DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_product (product_id),
    INDEX idx_brand (brand),
    INDEX idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU表';

-- ============================================
-- 4. SKU 价格表
-- ============================================
CREATE TABLE sku_prices (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    price_id        VARCHAR(64) NOT NULL UNIQUE,
    sku_id          BIGINT NOT NULL,
    price           DECIMAL(10,2) NOT NULL,
    original_price  DECIMAL(10,2),
    currency        VARCHAR(3) DEFAULT 'CNY',
    effective_from  DATETIME NOT NULL,
    effective_to    DATETIME,
    promotion_tag   VARCHAR(100),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_sku_time (sku_id, effective_from),
    INDEX idx_sku_active (sku_id, is_active, effective_from DESC),
    FOREIGN KEY (sku_id) REFERENCES skus(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU价格表';

-- ============================================
-- 5. SKU 库存表
-- ============================================
CREATE TABLE sku_inventory (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    sku_id          BIGINT NOT NULL UNIQUE,
    available_qty   INT NOT NULL DEFAULT 0,
    reserved_qty    INT NOT NULL DEFAULT 0,
    sold_qty        INT NOT NULL DEFAULT 0,
    warning_level   INT DEFAULT 10,
    last_updated    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (sku_id) REFERENCES skus(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SKU库存表';

-- ============================================
-- 6. 购物车
-- ============================================
CREATE TABLE cart_items (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT NOT NULL,
    sku_id          BIGINT NOT NULL,
    quantity        INT NOT NULL DEFAULT 1,
    selected        BOOLEAN DEFAULT TRUE,
    invalid_reason  VARCHAR(100),
    added_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_user_sku (user_id, sku_id),
    INDEX idx_user_selected (user_id, selected),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (sku_id) REFERENCES skus(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 7. 订单
-- ============================================
CREATE TABLE orders (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id        VARCHAR(32) NOT NULL UNIQUE,
    user_id         BIGINT NOT NULL,
    total_amount    DECIMAL(12,2) NOT NULL,
    discount_amount DECIMAL(12,2) DEFAULT 0,
    pay_amount      DECIMAL(12,2) NOT NULL,
    status          TINYINT DEFAULT 1 COMMENT '1待付 2已付 3发货 4完成 5取消',
    coupon_code     VARCHAR(32),
    idempotency_key VARCHAR(64),
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
    order_id        BIGINT NOT NULL,
    sku_id          BIGINT NOT NULL,
    product_name    VARCHAR(255),
    spec_display    VARCHAR(100),
    quantity        INT NOT NULL,
    unit_price      DECIMAL(10,2) NOT NULL,
    total_price     DECIMAL(12,2) NOT NULL,
    INDEX idx_order (order_id),
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 8. 优惠券模板
-- ============================================
CREATE TABLE coupon_templates (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    template_id     VARCHAR(32) NOT NULL UNIQUE,
    name            VARCHAR(100) NOT NULL,
    description     VARCHAR(255),
    type            TINYINT NOT NULL COMMENT '1满减 2折扣 3立减',
    scope           TINYINT DEFAULT 1 COMMENT '1全平台 2品类 3单品',
    threshold       DECIMAL(10,2) DEFAULT 0,
    discount_amount DECIMAL(10,2),
    discount_rate   DECIMAL(3,2),
    max_discount    DECIMAL(10,2),
    total_count     INT NOT NULL,
    remaining_count INT NOT NULL,
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

-- ============================================
-- 9. 用户优惠券
-- ============================================
CREATE TABLE user_coupons (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    coupon_code     VARCHAR(32) NOT NULL UNIQUE,
    user_id         BIGINT NOT NULL,
    template_id     VARCHAR(32) NOT NULL,
    status          TINYINT DEFAULT 1 COMMENT '1未用 2已冻结 3已使用',
    freeze_time     DATETIME,
    order_id        VARCHAR(32),
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

-- ============================================
-- 10. 幂等键表
-- ============================================
CREATE TABLE idempotency_keys (
    key_hash        VARCHAR(64) PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    action          VARCHAR(50) NOT NULL,
    request_body    JSON,
    response_body   JSON,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at      DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================
-- 11. 商家表
-- ============================================
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
