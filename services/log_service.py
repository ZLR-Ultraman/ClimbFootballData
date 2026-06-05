from __future__ import annotations

"""日志查询服务。

这个模块专门负责从 SQLite 中读取日志数据，避免把数据库查询逻辑
继续堆在 `app.py` 里。这样做的好处是：
1. 路由层更清爽；
2. 日志查询逻辑可以独立维护；
3. 以后如果要分页、加更多筛选条件，也更方便扩展。
"""

import sqlite3
from pathlib import Path


class LogService:
    """日志查询服务。

    仅负责读取日志与批次聚合信息，不修改现有写入逻辑，
    这样可以最大程度保证当前功能不受影响。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_logs(self, batch_id: str | None = None, log_type: str | None = None, date_from: str | None = None, date_to: str | None = None, limit: int = 500):
        """读取日志列表。

        参数支持按批次、类型、时间范围筛选。
        返回值保持为前端可直接消费的字典数组。
        """
        conn = self._connect()
        try:
            sql = "SELECT batch_id, crawl_date, match_id, level, log_type, message, created_at FROM crawl_logs"
            params: list[str] = []
            clauses: list[str] = []

            if batch_id:
                clauses.append("batch_id = ?")
                params.append(batch_id)
            if log_type and log_type != "all":
                clauses.append("log_type = ?")
                params.append(log_type)
            if date_from:
                clauses.append("created_at >= ?")
                params.append(date_from)
            if date_to:
                clauses.append("created_at <= ?")
                params.append(date_to)

            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(str(limit))

            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "batch_id": row["batch_id"],
                    "crawl_date": row["crawl_date"],
                    "match_id": row["match_id"],
                    "level": row["level"],
                    "type": row["log_type"],
                    "msg": row["message"],
                    "time": row["created_at"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def get_batch_options(self, limit: int = 200):
        """获取日志批次下拉选项。

        用于前端查看所有历史日志时，按批次快速筛选。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT batch_id,
                       MIN(created_at) AS started_at,
                       MAX(created_at) AS ended_at,
                       COUNT(*) AS log_count,
                       MAX(crawl_date) AS last_crawl_date,
                       MAX(log_type) AS last_log_type
                FROM crawl_logs
                WHERE batch_id IS NOT NULL AND batch_id != ''
                GROUP BY batch_id
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {
                    "batch_id": row["batch_id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "log_count": row["log_count"],
                    "last_crawl_date": row["last_crawl_date"],
                    "last_log_type": row["last_log_type"],
                }
                for row in rows
            ]
        finally:
            conn.close()
