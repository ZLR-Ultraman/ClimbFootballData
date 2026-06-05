from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from db_manager import DatabaseManager

LIST_URL = "https://bf.titan007.com/football/Over_20260602.htm"
DETAIL_URL = "https://zq.titan007.com/analysis/{match_id}cn.htm"
ALLOWED_LEAGUES = {"英超", "意甲", "德甲", "西甲", "法甲"}
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class MatchMeta:
    match_id: str
    match_url: str
    league_name: str | None = None
    match_time: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    match_status: str | None = None


class TitanScraper:
    def __init__(self, db: DatabaseManager, user_data_dir: str = "./playwright_profile", headless: bool = True) -> None:
        self.db = db
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self.browser = None
        self.context = None

    async def __aenter__(self):
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            user_agent=DEFAULT_UA,
            viewport={"width": 430, "height": 932},
            locale="zh-CN",
        )
        self.context = self.browser
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.browser:
            await self.browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()

    async def _new_page(self):
        page = await self.context.new_page()
        page.set_default_timeout(30000)
        return page

    async def fetch_list_page(self, url: str = LIST_URL) -> list[dict[str, Any]]:
        page = await self._new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            html = await page.content()
            return self._parse_list_html(html)
        finally:
            await page.close()

    def _parse_list_html(self, html: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        tbody = re.search(r'<table[^>]*id="table_live"[\s\S]*?<tbody>([\s\S]*?)</tbody>', html, re.I)
        block = tbody.group(1) if tbody else html
        for tr in re.finditer(r'<tr[^>]*sid="(\d+)"[\s\S]*?</tr>', block, re.I):
            row_html = tr.group(0)
            match_id = tr.group(1)
            cells = re.findall(r'<t[hd][^>]*>([\s\S]*?)</t[hd]>', row_html, re.I)
            if len(cells) < 10:
                continue
            league_name = self._clean(self._strip_html(cells[0]))
            match_time = self._clean(self._strip_html(cells[1]))
            match_status = self._clean(self._strip_html(cells[2]))
            home_team = self._clean(self._strip_html(cells[3]))
            score_text = self._clean(self._strip_html(cells[4]))
            away_team = self._clean(self._strip_html(cells[5]))
            home_score, away_score = self._parse_score(score_text)
            rows.append(
                {
                    "match_id": match_id,
                    "match_url": DETAIL_URL.format(match_id=match_id),
                    "league_name": league_name,
                    "match_time": match_time,
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_score": home_score,
                    "away_score": away_score,
                    "match_status": match_status,
                }
            )
        return rows

    def filter_allowed_leagues(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if (row.get("league_name") or "").strip() in ALLOWED_LEAGUES]

    async def fetch_detail_page(self, match_id: str) -> dict[str, Any]:
        page = await self._new_page()
        try:
            await page.goto(DETAIL_URL.format(match_id=match_id), wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1800)
            html = await page.content()
            return self._parse_detail_html(html)
        finally:
            await page.close()

    def _parse_detail_html(self, html: str) -> dict[str, Any]:
        home_team, away_team = self._extract_teams(html)
        return {
            "home_team": home_team,
            "away_team": away_team,
            "home_recent": self._extract_recent_block(html, side="home"),
            "away_recent": self._extract_recent_block(html, side="away"),
        }

    def _extract_teams(self, html: str) -> tuple[str | None, str | None]:
        patterns = [
            r'var\s+hometeam\s*=\s*"(.*?)";.*?var\s+guestteam\s*=\s*"(.*?)";',
            r'var\s+home_team\s*=\s*"(.*?)";.*?var\s+guestteam\s*=\s*"(.*?)";',
            r'hometeam\s*=\s*"(.*?)".*?guestteam\s*=\s*"(.*?)"',
            r'home_team\s*=\s*"(.*?)".*?guestteam\s*=\s*"(.*?)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.S | re.I)
            if m:
                return self._clean(m.group(1)), self._clean(m.group(2))
        return None, None

    def _extract_recent_block(self, html: str, side: str) -> dict[str, Any]:
        candidates = []
        if side == "home":
            candidates.append(r'(?is)<div[^>]*id="hn"[^>]*>([\s\S]*?)</div>\s*</td>')
            candidates.append(r'(?is)<table[^>]*id="table_hn"[\s\S]*?</table>')
        else:
            candidates.append(r'(?is)<div[^>]*id="an"[^>]*>([\s\S]*?)</div>\s*</td>')
            candidates.append(r'(?is)<table[^>]*id="table_an"[\s\S]*?</table>')
        block = None
        for pattern in candidates:
            m = re.search(pattern, html)
            if m:
                block = m.group(0)
                break
        if block is None:
            block = html

        text = self._clean(self._strip_html(block))
        row_count = self._count_recent_rows(block)
        summary = self._summarize_recent(text)
        summary["matches"] = row_count if row_count else summary.get("matches")
        return {"raw_text": text, "summary": summary, "row_count": row_count}

    def _count_recent_rows(self, block: str) -> int:
        count = 0
        for tr in re.finditer(r'<tr[\s\S]*?</tr>', block, re.I):
            cells = re.findall(r'<t[hd][^>]*>([\s\S]*?)</t[hd]>', tr.group(0), re.I)
            if len(cells) < 2:
                continue
            date_cell = self._clean(self._strip_html(cells[1]))
            if re.search(r"\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{2}", date_cell):
                count += 1
        return count

    def _summarize_recent(self, text: str) -> dict[str, Any]:
        return {
            "matches": self._safe_int(self._match(text, r"近(\d+)场")),
            "win": self._safe_int(self._match(text, r"胜(\d+)")),
            "draw": self._safe_int(self._match(text, r"平(\d+)")),
            "loss": self._safe_int(self._match(text, r"负(\d+)")),
            "win_rate": self._match(text, r"胜率[：:]\s*([\d.]+%)"),
            "profit_rate": self._match(text, r"赢率[：:]\s*([\d.]+%)"),
            "big_rate": self._match(text, r"大[：:]\s*([\d.]+%)"),
            "single_rate": self._match(text, r"单率[：:]\s*([\d.]+%)"),
        }

    async def get_qualified_recent_stats(self, match_id: str, min_matches: int = 10) -> dict[str, Any] | None:
        data = await self.fetch_detail_page(match_id)
        home_n = data.get("home_recent", {}).get("row_count") or 0
        away_n = data.get("away_recent", {}).get("row_count") or 0
        if home_n < min_matches or away_n < min_matches:
            return None
        return data

    def format_recent_summary(self, recent: dict[str, Any]) -> str:
        summary = recent.get("summary", {})
        matches = summary.get("matches") or recent.get("row_count") or 0
        win = summary.get("win") or 0
        draw = summary.get("draw") or 0
        loss = summary.get("loss") or 0
        win_rate = summary.get("win_rate") or "-"
        profit_rate = summary.get("profit_rate") or "-"
        big_rate = summary.get("big_rate") or "-"
        single_rate = summary.get("single_rate") or "-"
        return f"近{matches}场,胜{win}平{draw}负{loss},胜率:{win_rate}赢率:{profit_rate}大:{big_rate}单率:{single_rate}"

    async def sync_match(self, match_id: str, detail: dict[str, Any] | None = None, base_row: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if detail is None:
            detail = await self.get_qualified_recent_stats(match_id)
        if not detail:
            return None
        record = {
            "match_id": match_id,
            "home_team": detail.get("home_team"),
            "away_team": detail.get("away_team"),
            "source_url": DETAIL_URL.format(match_id=match_id),
            "home_recent_summary": self.format_recent_summary(detail["home_recent"]),
            "away_recent_summary": self.format_recent_summary(detail["away_recent"]),
        }
        if base_row:
            for k in ("league_name", "match_time", "home_score", "away_score", "match_status", "crawl_date"):
                if k in base_row and base_row[k] is not None:
                    record[k] = base_row[k]
        self.db.upsert_match(record)
        self.db.save_match_details(
            match_id,
            json.dumps(detail.get("home_recent", {}), ensure_ascii=False),
            json.dumps(detail.get("away_recent", {}), ensure_ascii=False),
        )
        self.db.set_crawl_state(f"detail:{match_id}", datetime.utcnow().isoformat())
        return detail

    async def run_from_list(self, min_matches: int = 10) -> list[dict[str, Any]]:
        matches = await self.fetch_list_page()
        result = []
        for row in matches:
            match_id = row["match_id"]
            detail = await self.get_qualified_recent_stats(match_id, min_matches=min_matches)
            if detail:
                result.append(detail)
                await self.sync_match(match_id, detail=detail, base_row=row)
                self.db.save_match_details(
                    match_id,
                    json.dumps(detail.get("home_recent", {}), ensure_ascii=False),
                    json.dumps(detail.get("away_recent", {}), ensure_ascii=False),
                )
                self.db.set_crawl_state(f"detail:{match_id}", datetime.utcnow().isoformat())
            await asyncio.sleep(random.uniform(1.5, 3.0))
        return result

    def _match(self, text: str, pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(1) if m else None

    def _parse_score(self, text: str) -> tuple[int | None, int | None]:
        m = re.search(r"(\d+)\s*[-:]\s*(\d+)", text)
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))

    def _safe_int(self, value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except Exception:
            return None

    def _strip_html(self, text: str) -> str:
        return re.sub(r"<[^>]+>", "", text or "")

    def _clean(self, s: str | None) -> str:
        return re.sub(r"\s+", " ", s or "").strip()
