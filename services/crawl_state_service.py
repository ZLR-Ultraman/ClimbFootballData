from __future__ import annotations

"""爬虫状态辅助服务。\n\n这个模块只放与爬取运行状态相关的通用工具函数，\n避免 `app.py` 继续堆积太多与路由无关的辅助逻辑。\n\n注意：这里不改变原有业务流程，只是把重复且独立的状态操作抽出来。"""

from datetime import datetime, timedelta


DEFAULT_BATCH_SUMMARY = {
    "total_days": 0,
    "finished_days": 0,
    "success_days": 0,
    "failed_days": 0,
    "total_matches": 0,
    "qualified_matches": 0,
    "skipped_matches": 0,
    "failed_matches": 0,
}


def reset_crawl_state(crawl_state: dict) -> None:
    """重置前端轮询依赖的爬虫状态。"""
    crawl_state.update({
        "running": False, "progress": 0, "total": 0, "current": 0,
        "current_match_id": "", "current_match_name": "",
        "qualified": 0, "skipped": 0, "failed": 0, "finished": False, "error": None, "logs": [],
        "crawl_date": None, "batch_id": None, "start_date": None, "end_date": None,
        "day_index": 0, "day_total": 0, "day_summary": {},
        "batch_summary": dict(DEFAULT_BATCH_SUMMARY),
    })


def update_batch_summary(crawl_state: dict, batch_totals: dict, finished_days: int | None = None) -> None:
    """同步批次汇总到内存状态。"""
    crawl_state["batch_summary"] = {
        "total_days": crawl_state.get("day_total", 0),
        "finished_days": finished_days if finished_days is not None else crawl_state.get("day_index", 0),
        "success_days": batch_totals.get("success_days", 0),
        "failed_days": batch_totals.get("failed_days", 0),
        "total_matches": batch_totals.get("total_matches", 0),
        "qualified_matches": batch_totals.get("qualified_matches", 0),
        "skipped_matches": batch_totals.get("skipped_matches", 0),
        "failed_matches": batch_totals.get("failed_matches", 0),
    }


def daterange(start_date: str, end_date: str) -> list[str]:
    """生成 yyyyMMdd 格式的日期范围。"""
    days: list[str] = []
    current = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days
