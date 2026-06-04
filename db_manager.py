from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass
class MatchRecord:
    match_id: str
    league_name: str | None = None
    match_time: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    match_status: str | None = None
    source_url: str | None = None
    crawl_date: str | None = None


class DatabaseManager:
    def __init__(self, db_path: str | Path = "football_data.sqlite3") -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    league_name TEXT,
                    match_time TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    home_score INTEGER,
                    away_score INTEGER,
                    match_status TEXT,
                    source_url TEXT,
                    home_recent_summary TEXT,
                    away_recent_summary TEXT,
                    crawl_date TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS match_details (
                    match_id TEXT PRIMARY KEY,
                    home_stats_json TEXT,
                    away_stats_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS crawl_sessions (
                    id TEXT PRIMARY KEY,
                    status TEXT DEFAULT 'running',
                    total INTEGER DEFAULT 0,
                    qualified INTEGER DEFAULT 0,
                    skipped INTEGER DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS crawl_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            try:
                conn.execute("ALTER TABLE matches ADD COLUMN home_recent_summary TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE matches ADD COLUMN away_recent_summary TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE matches ADD COLUMN crawl_date TEXT")
            except Exception:
                pass

    def upsert_match(self, record: Mapping[str, Any] | MatchRecord) -> None:
        data = record.__dict__ if isinstance(record, MatchRecord) else dict(record)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO matches (match_id, league_name, match_time, home_team, away_team, home_score,
                                     away_score, match_status, source_url, home_recent_summary, away_recent_summary,
                                     crawl_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(match_id) DO UPDATE SET
                    league_name=excluded.league_name,
                    match_time=excluded.match_time,
                    home_team=excluded.home_team,
                    away_team=excluded.away_team,
                    home_score=excluded.home_score,
                    away_score=excluded.away_score,
                    match_status=excluded.match_status,
                    source_url=excluded.source_url,
                    home_recent_summary=excluded.home_recent_summary,
                    away_recent_summary=excluded.away_recent_summary,
                    crawl_date=excluded.crawl_date,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    data.get("match_id"),
                    data.get("league_name"),
                    data.get("match_time"),
                    data.get("home_team"),
                    data.get("away_team"),
                    data.get("home_score"),
                    data.get("away_score"),
                    data.get("match_status"),
                    data.get("source_url"),
                    data.get("home_recent_summary"),
                    data.get("away_recent_summary"),
                    data.get("crawl_date"),
                ),
            )

    def save_match_details(self, match_id: str, home_stats_json: str, away_stats_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO match_details (match_id, home_stats_json, away_stats_json, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(match_id) DO UPDATE SET
                    home_stats_json=excluded.home_stats_json,
                    away_stats_json=excluded.away_stats_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (match_id, home_stats_json, away_stats_json),
            )

    def get_crawl_state(self, state_key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT state_value FROM crawl_state WHERE state_key=?", (state_key,)).fetchone()
            return None if row is None else row[0]

    def get_all_matches(self, crawl_date: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if crawl_date:
                rows = conn.execute(
                    "SELECT match_id, league_name, match_time, home_team, away_team, "
                    "home_score, away_score, match_status, source_url, "
                    "home_recent_summary, away_recent_summary, crawl_date, updated_at "
                    "FROM matches WHERE crawl_date=? ORDER BY updated_at DESC",
                    (crawl_date,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT match_id, league_name, match_time, home_team, away_team, "
                    "home_score, away_score, match_status, source_url, "
                    "home_recent_summary, away_recent_summary, crawl_date, updated_at "
                    "FROM matches ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(row) for row in rows]

    def get_match_by_id(self, match_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT match_id, league_name, match_time, home_team, away_team, "
                "home_score, away_score, match_status, source_url, "
                "home_recent_summary, away_recent_summary, crawl_date, updated_at "
                "FROM matches WHERE match_id=?",
                (match_id,),
            ).fetchone()
            return dict(row) if row else None

    def set_crawl_state(self, state_key: str, state_value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crawl_state (state_key, state_value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_value=excluded.state_value,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (state_key, state_value),
            )

    def delete_matches_by_date(self, crawl_date: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM matches WHERE crawl_date=?", (crawl_date,))
            return cursor.rowcount

    def upsert_crawl_session(self, session_id: str, status: str = "running", total: int = 0,
                              qualified: int = 0, skipped: int = 0, started_at: str | None = None,
                              finished_at: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crawl_sessions (id, status, total, qualified, skipped, started_at, finished_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    total=COALESCE(excluded.total, crawl_sessions.total),
                    qualified=COALESCE(excluded.qualified, crawl_sessions.qualified),
                    skipped=COALESCE(excluded.skipped, crawl_sessions.skipped),
                    started_at=CASE WHEN excluded.started_at IS NOT NULL THEN excluded.started_at ELSE crawl_sessions.started_at END,
                    finished_at=COALESCE(excluded.finished_at, crawl_sessions.finished_at),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (session_id, status, total, qualified, skipped, started_at, finished_at),
            )

    def get_crawl_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, status, total, qualified, skipped, started_at, finished_at, updated_at "
                "FROM crawl_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_available_dates(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT crawl_date FROM matches WHERE crawl_date IS NOT NULL ORDER BY crawl_date DESC"
            ).fetchall()
            return [r[0] for r in rows if r[0]]
