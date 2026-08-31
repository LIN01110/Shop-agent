"""
反馈闭环系统（Phase 2）。

收集用户交互行为（点击、购买、停留时间、收藏等），用于：
1. 离线训练精排模型（Learning to Rank）
2. 在线调整检索参数（A/B 测试）
3. 生成用户画像和个性化推荐

数据结构：
- QueryLog: 查询日志（query, filters, timestamp, session_id）
- ClickEvent: 点击事件（query_id, product_id, position, dwell_time）
- ConversionEvent: 转化事件（query_id, product_id, action: purchase/cart/favorite）

设计要点：
- 异步写入：事件先写入内存队列，后台批量持久化
- 隐私安全：不记录用户身份，只用 session_id 关联
- 可插拔存储：支持 SQLite（默认）、JSONL、Kafka

依赖：
  无额外依赖（使用标准库 sqlite3 和 threading）

使用方式：
  from server.rag.feedback_loop import FeedbackLoop
  fb = FeedbackLoop(db_path="server/runtime/feedback.sqlite3")
  fb.log_query(session_id, query, filters)
  fb.log_click(query_id, product_id, position=1, dwell_time_ms=5000)
  fb.log_conversion(query_id, product_id, action="purchase")

  # 训练数据导出
  training_data = fb.export_training_pairs(min_dwell_ms=2000)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class QueryLog:
    """查询日志。"""

    id: str                          # 查询唯一 ID（UUID）
    session_id: str
    query_text: str
    query_image: bool = False        # 是否包含图片
    filters_json: str = "{}"         # 过滤条件 JSON
    timestamp: float = field(default_factory=time.time)
    user_agent: str = ""
    latency_ms: float = 0.0          # 检索耗时
    result_count: int = 0            # 返回结果数


@dataclass
class ClickEvent:
    """点击事件。"""

    id: str
    query_id: str
    product_id: str
    position: int                    # 在结果列表中的位置（1-based）
    timestamp: float = field(default_factory=time.time)
    dwell_time_ms: int = 0           # 停留时间（毫秒）
    source: str = "search"           # search / recommendation / similar


@dataclass
class ConversionEvent:
    """转化事件。"""

    id: str
    query_id: str
    product_id: str
    action: str                      # purchase / add_to_cart / favorite / share
    timestamp: float = field(default_factory=time.time)
    value: float = 0.0               # 订单金额（purchase 时）


@dataclass
class TrainingPair:
    """用于 LTR 训练的数据对。"""

    query_id: str
    query_text: str
    product_id: str
    product_text: str
    label: int                       # 0: 未点击, 1: 点击, 2: 加购, 3: 购买
    position: int = 0
    dwell_time_ms: int = 0
    bm25_score: float = 0.0
    vector_score: float = 0.0
    ce_score: float = 0.0


# ---------------------------------------------------------------------------
# FeedbackLoop
# ---------------------------------------------------------------------------

class FeedbackLoop:
    """
    反馈闭环系统。

    架构：
    - 前端事件 → 内存队列 → 后台写入线程 → SQLite
    - 查询时读取历史数据用于个性化重排序
    """

    def __init__(
        self,
        db_path: str | Path = "server/runtime/feedback.sqlite3",
        batch_size: int = 50,
        flush_interval_seconds: float = 5.0,
        max_queue_size: int = 10000,
    ) -> None:
        self.db_path = Path(db_path) if isinstance(db_path, str) else db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self.flush_interval = flush_interval_seconds

        # 内存队列
        self._queue: Queue = Queue(maxsize=max_queue_size)
        self._pending: list[tuple[str, dict]] = []  # (table, data)
        self._lock = threading.Lock()

        # 初始化数据库
        self._init_db()

        # 启动后台写入线程
        self._shutdown = False
        self._worker = threading.Thread(target=self._flush_worker, daemon=True)
        self._worker.start()

    def _init_db(self) -> None:
        """初始化 SQLite 表结构。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS queries (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    query_image INTEGER DEFAULT 0,
                    filters_json TEXT DEFAULT '{}',
                    timestamp REAL NOT NULL,
                    user_agent TEXT,
                    latency_ms REAL DEFAULT 0,
                    result_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_queries_session ON queries(session_id);
                CREATE INDEX IF NOT EXISTS idx_queries_time ON queries(timestamp);

                CREATE TABLE IF NOT EXISTS clicks (
                    id TEXT PRIMARY KEY,
                    query_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    dwell_time_ms INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'search',
                    FOREIGN KEY (query_id) REFERENCES queries(id)
                );
                CREATE INDEX IF NOT EXISTS idx_clicks_query ON clicks(query_id);
                CREATE INDEX IF NOT EXISTS idx_clicks_product ON clicks(product_id);

                CREATE TABLE IF NOT EXISTS conversions (
                    id TEXT PRIMARY KEY,
                    query_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    value REAL DEFAULT 0,
                    FOREIGN KEY (query_id) REFERENCES queries(id)
                );
                CREATE INDEX IF NOT EXISTS idx_conversions_query ON conversions(query_id);
            """)
            conn.commit()
        logger.info("Feedback DB initialized: %s", self.db_path)

    # ------------------------------------------------------------------
    # 事件记录 API
    # ------------------------------------------------------------------

    def log_query(
        self,
        query_id: str,
        session_id: str,
        query_text: str,
        query_image: bool = False,
        filters: dict | None = None,
        latency_ms: float = 0.0,
        result_count: int = 0,
    ) -> None:
        """记录查询。"""
        data = {
            "id": query_id,
            "session_id": session_id,
            "query_text": query_text,
            "query_image": 1 if query_image else 0,
            "filters_json": json.dumps(filters or {}, ensure_ascii=False),
            "timestamp": time.time(),
            "latency_ms": latency_ms,
            "result_count": result_count,
        }
        self._enqueue("queries", data)

    def log_click(
        self,
        click_id: str,
        query_id: str,
        product_id: str,
        position: int,
        dwell_time_ms: int = 0,
        source: str = "search",
    ) -> None:
        """记录点击。"""
        data = {
            "id": click_id,
            "query_id": query_id,
            "product_id": product_id,
            "position": position,
            "timestamp": time.time(),
            "dwell_time_ms": dwell_time_ms,
            "source": source,
        }
        self._enqueue("clicks", data)

    def log_conversion(
        self,
        conversion_id: str,
        query_id: str,
        product_id: str,
        action: str,
        value: float = 0.0,
    ) -> None:
        """记录转化。"""
        data = {
            "id": conversion_id,
            "query_id": query_id,
            "product_id": product_id,
            "action": action,
            "timestamp": time.time(),
            "value": value,
        }
        self._enqueue("conversions", data)

    def _enqueue(self, table: str, data: dict) -> None:
        """将事件加入队列。"""
        try:
            self._queue.put_nowait((table, data))
        except Exception:
            # 队列满时直接丢弃（避免阻塞主流程）
            logger.debug("Feedback queue full, dropping event")

    # ------------------------------------------------------------------
    # 后台写入
    # ------------------------------------------------------------------

    def _flush_worker(self) -> None:
        """后台写入线程。"""
        while not self._shutdown:
            time.sleep(self.flush_interval)
            self._flush()

    def _flush(self) -> None:
        """将队列中的数据写入数据库。"""
        # 收集队列中的事件
        batch = []
        while len(batch) < self.batch_size:
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except Exception:
                break

        if not batch:
            return

        # 按表分组
        by_table: dict[str, list[dict]] = {}
        for table, data in batch:
            by_table.setdefault(table, []).append(data)

        # 批量写入
        try:
            with sqlite3.connect(self.db_path) as conn:
                for table, rows in by_table.items():
                    if not rows:
                        continue
                    columns = list(rows[0].keys())
                    placeholders = ",".join(["?"] * len(columns))
                    col_names = ",".join(columns)
                    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
                    values = [[row.get(c) for c in columns] for row in rows]
                    conn.executemany(sql, values)
                conn.commit()
            logger.debug("Flushed %d feedback events", len(batch))
        except Exception as exc:
            logger.error("Failed to flush feedback: %s", exc)

    def close(self) -> None:
        """关闭反馈系统，刷新剩余数据。"""
        self._shutdown = True
        self._worker.join(timeout=10.0)
        self._flush()

    # ------------------------------------------------------------------
    # 数据查询与导出
    # ------------------------------------------------------------------

    def get_query_stats(self, hours: int = 24) -> dict:
        """获取查询统计。"""
        since = time.time() - hours * 3600
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                "SELECT COUNT(*) FROM queries WHERE timestamp > ?", (since,)
            ).fetchone()[0]
            avg_latency = conn.execute(
                "SELECT AVG(latency_ms) FROM queries WHERE timestamp > ?", (since,)
            ).fetchone()[0] or 0
            click_rate = conn.execute(
                """
                SELECT CAST(COUNT(DISTINCT c.query_id) AS FLOAT) / NULLIF(COUNT(DISTINCT q.id), 0)
                FROM queries q
                LEFT JOIN clicks c ON q.id = c.query_id
                WHERE q.timestamp > ?
                """,
                (since,),
            ).fetchone()[0] or 0

        return {
            "total_queries": total,
            "avg_latency_ms": round(avg_latency, 2),
            "click_through_rate": round(click_rate, 4),
            "period_hours": hours,
        }

    def get_popular_queries(self, limit: int = 20, hours: int = 24) -> list[dict]:
        """获取热门查询。"""
        since = time.time() - hours * 3600
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT query_text, COUNT(*) as count,
                       AVG(latency_ms) as avg_latency,
                       COUNT(DISTINCT session_id) as unique_sessions
                FROM queries
                WHERE timestamp > ?
                GROUP BY query_text
                ORDER BY count DESC
                LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_training_pairs(
        self,
        min_dwell_ms: int = 2000,
        limit: int = 10000,
    ) -> list[TrainingPair]:
        """
        导出 LTR 训练数据对。

        标签生成规则：
        - 未点击：label=0
        - 点击但停留 < min_dwell_ms：label=0（误点击）
        - 点击且停留 >= min_dwell_ms：label=1
        - 加购：label=2
        - 购买：label=3
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 获取所有有反馈的查询
            rows = conn.execute(
                """
                SELECT
                    q.id as query_id,
                    q.query_text,
                    c.product_id,
                    c.position,
                    c.dwell_time_ms,
                    COALESCE(cv.action, '') as conversion_action
                FROM queries q
                JOIN clicks c ON q.id = c.query_id
                LEFT JOIN conversions cv ON c.query_id = cv.query_id AND c.product_id = cv.product_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        pairs = []
        for row in rows:
            # 确定标签
            action = row["conversion_action"]
            dwell = row["dwell_time_ms"]

            if action == "purchase":
                label = 3
            elif action == "add_to_cart":
                label = 2
            elif dwell >= min_dwell_ms:
                label = 1
            else:
                label = 0

            pairs.append(TrainingPair(
                query_id=row["query_id"],
                query_text=row["query_text"],
                product_id=row["product_id"],
                product_text="",  # 需要外部填充
                label=label,
                position=row["position"],
                dwell_time_ms=dwell,
            ))

        return pairs

    def get_user_preferences(self, session_id: str, limit: int = 50) -> dict:
        """
        获取用户偏好（基于历史交互）。
        返回：{categories: [...], brands: [...], price_range: [min, max]}
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # 获取该 session 的点击/转化商品
            rows = conn.execute(
                """
                SELECT DISTINCT c.product_id
                FROM clicks c
                JOIN queries q ON c.query_id = q.id
                WHERE q.session_id = ?
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        return {
            "session_id": session_id,
            "interacted_products": [r["product_id"] for r in rows],
            "product_count": len(rows),
        }

    def get_ctr_by_position(self, hours: int = 168) -> list[dict]:
        """获取各位置的点击率（用于评估检索质量）。"""
        since = time.time() - hours * 3600
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT position, COUNT(*) as clicks, AVG(dwell_time_ms) as avg_dwell
                FROM clicks c
                JOIN queries q ON c.query_id = q.id
                WHERE q.timestamp > ?
                GROUP BY position
                ORDER BY position
                """,
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]
