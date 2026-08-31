#!/usr/bin/env python3
"""
将现有 products_ref.json 数据迁移到 MySQL

用法：
    python migrate_products.py --json data/products_ref.json --db mysql://user:pass@host/db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def normalize_spec(specs: dict) -> str:
    """规范化规格编码"""
    SORT_ORDER = {"颜色": 0, "尺码": 1, "容量": 2, "规格": 3}
    
    sorted_items = sorted(
        specs.items(),
        key=lambda x: (SORT_ORDER.get(x[0], 99), x[0])
    )
    
    parts = []
    for key, value in sorted_items:
        normalized = str(value).upper().replace(" ", "").replace("/", "-")
        parts.append(f"{key}:{normalized}")
    
    return "_".join(parts)


def migrate_products(json_path: str, db_url: str):
    """迁移产品数据"""
    # 读取 JSON
    with open(json_path, "r", encoding="utf-8") as f:
        products = json.load(f)
    
    print(f"读取到 {len(products)} 个产品")
    
    # 连接数据库
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # 导入 ORM
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    
    from server.database.mysql_client import Base
    from server.models.sku import SKUORM, SKUInventoryORM, SKUPriceORM
    
    # 创建表
    Base.metadata.create_all(engine)
    
    migrated = 0
    for product in products:
        product_id = product.get("id", "")
        base_price = product.get("price", 0)
        sku_options = product.get("attributes", {}).get("sku_options", {})
        
        if not sku_options:
            # 无规格，创建一个默认 SKU
            sku_id = f"sku_{product_id}_DEFAULT"
            spec_display = "默认规格"
            spec_json = {}
            
            sku = SKUORM(
                sku_id=sku_id,
                product_id=product_id,
                product_name=product.get("name", ""),
                brand=product.get("brand", ""),
                category=product.get("category", ""),
                spec_json=spec_json,
                spec_display=spec_display,
                image_url=product.get("image_url", ""),
            )
            session.add(sku)
            session.flush()
            
            # 价格
            price = SKUPriceORM(
                price_id=f"{sku_id}_INIT",
                sku_id=sku.id,
                price=base_price,
                effective_from=datetime.utcnow(),
                is_active=True,
            )
            session.add(price)
            
            # 库存
            inv = SKUInventoryORM(
                sku_id=sku.id,
                available_qty=product.get("stock", 100),
            )
            session.add(inv)
            
            migrated += 1
        else:
            # 有规格，为每个规格创建 SKU
            for spec_name, spec_values in sku_options.items():
                for spec_value in spec_values:
                    specs = {spec_name: spec_value}
                    spec_hash = normalize_spec(specs)
                    sku_id = f"sku_{product_id}_{spec_hash}"
                    spec_display = f"{spec_value}"
                    
                    sku = SKUORM(
                        sku_id=sku_id,
                        product_id=product_id,
                        product_name=product.get("name", ""),
                        brand=product.get("brand", ""),
                        category=product.get("category", ""),
                        spec_json=specs,
                        spec_display=spec_display,
                        image_url=product.get("image_url", ""),
                    )
                    session.add(sku)
                    session.flush()
                    
                    # 价格（基于 base_price 和规格调整）
                    price = SKUPriceORM(
                        price_id=f"{sku_id}_INIT",
                        sku_id=sku.id,
                        price=base_price,  # 实际应该按规格调整
                        effective_from=datetime.utcnow(),
                        is_active=True,
                    )
                    session.add(price)
                    
                    # 库存
                    inv = SKUInventoryORM(
                        sku_id=sku.id,
                        available_qty=50,  # 默认库存
                    )
                    session.add(inv)
                    
                    migrated += 1
    
    session.commit()
    print(f"成功迁移 {migrated} 个 SKU")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="迁移产品数据到 MySQL")
    parser.add_argument("--json", default="data/products_ref.json", help="产品 JSON 文件路径")
    parser.add_argument("--db", default="mysql+pymysql://root:root@localhost/shop_agent?charset=utf8mb4", help="数据库连接串")
    
    args = parser.parse_args()
    
    from datetime import datetime
    migrate_products(args.json, args.db)
