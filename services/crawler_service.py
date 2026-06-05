from __future__ import annotations

"""爬虫执行服务。\n\n这个模块封装批量爬取的执行流程，避免 `app.py` 继续承担过多业务细节。\n\n重构原则：\n1. 不改变现有抓取行为；\n2. 保留原有日志、批次和会话写库逻辑；\n3. 仅将执行入口抽离，方便后续继续拆分和维护。"""

import asyncio
import random
from datetime import datetime
from pathlib import Path

from db_manager import DatabaseManager


class CrawlerService:
    """批量爬虫执行器。"""

    def __init__(self, db_path: Path, crawl_state: dict, add_log, update_batch_summary, stop_event):
        self.db_path = db_path
        self.crawl_state = crawl_state
        self.add_log = add_log
        self.update_batch_summary = update_batch_summary
        self.stop_event = stop_event

    def run(self, start_date: str, end_date: str) -> None:
        """同步入口，内部运行异步爬取流程。"""
        try:
            asyncio.run(self._crawl(start_date, end_date))
        except Exception as e:
            self.crawl_state["error"] = str(e)
            self.crawl_state["running"] = False
            self.add_log(f"批次出错: {e}", level="error", log_type="batch")

    async def _crawl(self, start_date: str, end_date: str) -> None:
        """执行批量爬取。\n\n这里保持原有逻辑，只是把实现集中到独立模块中。"""
        from scraper.titan_scraper import TitanScraper
        from services.crawl_state_service import daterange

        db_local = DatabaseManager(str(self.db_path))
        batch_id = f"batch_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        dates = daterange(start_date, end_date)
        batch_started_at = datetime.utcnow().isoformat()
        self.crawl_state.update({"batch_id": batch_id, "start_date": start_date, "end_date": end_date, "day_total": len(dates), "day_index": 0})
        db_local.upsert_crawl_batch(batch_id, start_date, end_date, status="running", total_days=len(dates), started_at=batch_started_at)
        self.add_log(f"开始批次抓取 {start_date} ~ {end_date}", log_type="batch", batch_id=batch_id)

        batch_totals = {"total_matches": 0, "qualified_matches": 0, "skipped_matches": 0, "failed_matches": 0, "success_days": 0, "failed_days": 0}
        self.update_batch_summary(self.crawl_state, batch_totals, finished_days=0)

        self.crawl_state["running"] = True
        async with TitanScraper(db=db_local, headless=True) as scraper:
            for idx, day in enumerate(dates, start=1):
                if self.stop_event.is_set():
                    self.add_log("收到停止信号，批次提前结束", level="warning", log_type="batch", batch_id=batch_id)
                    break

                self.crawl_state.update({"crawl_date": day, "day_index": idx, "total": 0, "current": 0, "current_match_id": "", "current_match_name": "", "qualified": 0, "skipped": 0, "failed": 0, "finished": False, "error": None, "progress": 0})
                self.update_batch_summary(self.crawl_state, batch_totals, finished_days=idx - 1)
                session_id = day
                old_count = db_local.delete_matches_by_date(day)
                if old_count:
                    self.add_log(f"[{day}] 已清除旧数据 {old_count} 条", log_type="day", crawl_date=day, batch_id=batch_id)
                list_url = f"https://bf.titan007.com/football/Over_{day}.htm"
                self.add_log(f"[{day}] 正在连接列表页: {list_url}", log_type="day", crawl_date=day, batch_id=batch_id)
                day_started_at = datetime.utcnow().isoformat()
                day_status = "running"
                try:
                    matches = await scraper.fetch_list_page(list_url)
                    matches = scraper.filter_allowed_leagues(matches)
                    self.crawl_state["total"] = len(matches)
                    self.add_log(f"[{day}] 白名单后剩余 {len(matches)} 场比赛", log_type="day", crawl_date=day, batch_id=batch_id)
                    db_local.upsert_crawl_session(session_id, batch_id=batch_id, crawl_date=day, status="running", started_at=day_started_at, total=len(matches))
                    batch_totals["total_matches"] += len(matches)
                    for i, row in enumerate(matches):
                        if self.stop_event.is_set():
                            day_status = "stopped"
                            self.add_log(f"[{day}] 用户已停止爬取", level="warning", log_type="day", crawl_date=day, batch_id=batch_id)
                            break
                        match_id = row["match_id"]
                        home = row.get("home_team") or ""
                        away = row.get("away_team") or ""
                        self.crawl_state.update({"current": i + 1, "current_match_id": match_id, "current_match_name": f"{home} vs {away}", "progress": int((i + 1) / max(len(matches), 1) * 100)})
                        self.add_log(f"[{day} {i+1}/{len(matches)}] 正在查询 {match_id} {home} vs {away}", crawl_date=day, batch_id=batch_id, match_id=match_id)
                        detail = await scraper.get_qualified_recent_stats(match_id)
                        if detail:
                            row_with_date = dict(row)
                            row_with_date["crawl_date"] = day
                            self.crawl_state["qualified"] += 1
                            batch_totals["qualified_matches"] += 1
                            await scraper.sync_match(match_id, detail=detail, base_row=row_with_date)
                            self.add_log(f"[{day} {i+1}/{len(matches)}] {match_id} 已入库", crawl_date=day, batch_id=batch_id, match_id=match_id)
                        else:
                            self.crawl_state["skipped"] += 1
                            batch_totals["skipped_matches"] += 1
                            self.add_log(f"[{day} {i+1}/{len(matches)}] {match_id} 已跳过", level="warning", crawl_date=day, batch_id=batch_id, match_id=match_id)
                        await asyncio.sleep(random.uniform(1.5, 3.0))
                    if day_status == "running":
                        day_status = "completed"
                    self.crawl_state["finished"] = day_status == "completed"
                    if day_status == "completed":
                        batch_totals["success_days"] += 1
                    else:
                        batch_totals["failed_days"] += 1
                    self.update_batch_summary(self.crawl_state, batch_totals, finished_days=idx)
                    db_local.upsert_crawl_session(session_id, batch_id=batch_id, crawl_date=day, status=day_status, total=self.crawl_state["total"], qualified=self.crawl_state["qualified"], skipped=self.crawl_state["skipped"], failed=self.crawl_state["failed"], started_at=day_started_at, finished_at=datetime.utcnow().isoformat(), error_message=self.crawl_state.get("error"))
                    self.add_log(f"[{day}] 完成，入库 {self.crawl_state['qualified']} 条，跳过 {self.crawl_state['skipped']} 条", log_type="day", crawl_date=day, batch_id=batch_id)
                except Exception as e:
                    day_status = "failed"
                    self.crawl_state["failed"] += 1
                    batch_totals["failed_matches"] += 1
                    batch_totals["failed_days"] += 1
                    self.crawl_state["error"] = str(e)
                    db_local.upsert_crawl_session(session_id, batch_id=batch_id, crawl_date=day, status=day_status, total=self.crawl_state["total"], qualified=self.crawl_state["qualified"], skipped=self.crawl_state["skipped"], failed=self.crawl_state["failed"], started_at=day_started_at, finished_at=datetime.utcnow().isoformat(), error_message=str(e))
                    self.add_log(f"[{day}] 爬取失败: {e}", level="error", log_type="day", crawl_date=day, batch_id=batch_id)
                    continue

                db_local.upsert_crawl_batch(batch_id, start_date, end_date, status="running", total_days=len(dates), finished_days=idx, success_days=batch_totals["success_days"], failed_days=batch_totals["failed_days"], total_matches=batch_totals["total_matches"], qualified_matches=batch_totals["qualified_matches"], skipped_matches=batch_totals["skipped_matches"], failed_matches=batch_totals["failed_matches"], started_at=batch_started_at)

        final_status = "stopped" if self.stop_event.is_set() else ("failed" if batch_totals["failed_days"] and batch_totals["success_days"] == 0 else "completed")
        db_local.upsert_crawl_batch(batch_id, start_date, end_date, status=final_status, total_days=len(dates), finished_days=self.crawl_state.get("day_index") or 0, success_days=batch_totals["success_days"], failed_days=batch_totals["failed_days"], total_matches=batch_totals["total_matches"], qualified_matches=batch_totals["qualified_matches"], skipped_matches=batch_totals["skipped_matches"], failed_matches=batch_totals["failed_matches"], started_at=batch_started_at, finished_at=datetime.utcnow().isoformat(), error_message=self.crawl_state.get("error"))
        self.crawl_state["running"] = False
        self.crawl_state["finished"] = True
        self.add_log(f"批次结束：{start_date} ~ {end_date}", log_type="batch", batch_id=batch_id)
