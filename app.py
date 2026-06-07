from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from services.proxy_service import build_proxy_target_url, proxy_request

from db_manager import DatabaseManager
from services.log_service import LogService
from services.crawl_state_service import reset_crawl_state, update_batch_summary
from services.crawler_service import CrawlerService

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"), static_folder=str(Path(__file__).parent / "static"))
DB_PATH = Path(__file__).parent / "football_data.sqlite3"
db = DatabaseManager(DB_PATH)
log_service = LogService(DB_PATH)

PROXY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://zq.titan007.com/",
}

crawl_state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "current": 0,
    "current_match_id": "",
    "current_match_name": "",
    "qualified": 0,
    "skipped": 0,
    "failed": 0,
    "finished": False,
    "error": None,
    "logs": [],
    "crawl_date": None,
    "batch_id": None,
    "start_date": None,
    "end_date": None,
    "day_index": 0,
    "day_total": 0,
    "day_summary": {},
    "batch_summary": {
        "total_days": 0,
        "finished_days": 0,
        "success_days": 0,
        "failed_days": 0,
        "total_matches": 0,
        "qualified_matches": 0,
        "skipped_matches": 0,
        "failed_matches": 0,
    },
}

_stop_event = threading.Event()


def _reset_crawl_state():
    reset_crawl_state(crawl_state)


def _add_log(msg, level="info", log_type="day", match_id=None, crawl_date=None, batch_id=None):
    payload = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "msg": msg,
        "level": level,
        "type": log_type,
        "crawl_date": crawl_date or crawl_state.get("crawl_date"),
        "batch_id": batch_id or crawl_state.get("batch_id"),
        "match_id": match_id,
    }
    crawl_state["logs"].append(payload)
    if len(crawl_state["logs"]) > 300:
        crawl_state["logs"] = crawl_state["logs"][-150:]
    try:
        db.add_crawl_log(
            msg,
            batch_id=payload["batch_id"],
            crawl_date=payload["crawl_date"],
            match_id=match_id,
            level=level,
            log_type=log_type,
        )
    except Exception:
        pass

def _update_batch_summary(batch_totals, finished_days=None):
    update_batch_summary(crawl_state, batch_totals, finished_days=finished_days)


def _run_crawler(start_date=None, end_date=None, allowed_leagues=None):
    service = CrawlerService(DB_PATH, crawl_state, _add_log, update_batch_summary, _stop_event)
    service.run(start_date, end_date, allowed_leagues)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/matches", methods=["GET"])
def api_matches():
    match_id = request.args.get("match_id", "").strip()
    if match_id:
        row = db.get_match_by_id(match_id)
        if row is None:
            return jsonify({"matches": [], "total": 0})
        return jsonify({"matches": [row], "total": 1})
    query_date = request.args.get("date", "").strip()
    if not query_date:
        query_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    rows = db.get_all_matches(query_date)
    return jsonify({"matches": rows, "total": len(rows), "query_date": query_date})


@app.route("/api/dates", methods=["GET"])
def api_dates():
    dates = db.get_available_dates()
    return jsonify({"dates": dates})


@app.route("/api/odds/<match_id>", methods=["GET"])
def api_match_odds(match_id):
    odds_data = db.get_match_odds(match_id)
    return jsonify(odds_data)


@app.route("/api/crawl/start", methods=["POST"])
def api_crawl_start():
    if crawl_state["running"]:
        return jsonify({"error": "爬取正在进行中"}), 409

    body = request.json or {}
    start_date = (body.get("start_date") or "").replace("-", "").strip()
    end_date = (body.get("end_date") or "").replace("-", "").strip()
    crawl_date = (body.get("crawl_date") or "").replace("-", "").strip()
    leagues_input = (body.get("leagues") or "").strip()

    if not start_date and not end_date:
        if not crawl_date:
            crawl_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        start_date = end_date = crawl_date
    elif start_date and not end_date:
        end_date = start_date
    elif end_date and not start_date:
        start_date = end_date

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    allowed_leagues = None
    if leagues_input:
        allowed_leagues = {league.strip() for league in leagues_input.split(",") if league.strip()}
        _add_log(f"用户自定义联赛: {allowed_leagues}", level="info", log_type="batch")

    _reset_crawl_state()
    _stop_event.clear()
    crawl_state.update({"running": True, "start_date": start_date, "end_date": end_date})

    t = threading.Thread(target=_run_crawler, args=(start_date, end_date, allowed_leagues), daemon=True)
    t.start()

    return jsonify({"status": "started", "start_date": start_date, "end_date": end_date, "leagues": list(allowed_leagues) if allowed_leagues else None})


@app.route("/api/crawl/status", methods=["GET"])
def api_crawl_status():
    result = dict(crawl_state)
    if result.get("crawl_date"):
        session = db.get_crawl_session(result["crawl_date"])
        if session:
            result["session"] = session
    if result.get("batch_id"):
        batch = db.get_crawl_batch(result["batch_id"])
        if batch:
            result["batch"] = batch
    result["recent_logs"] = result.get("logs", [])[-50:]
    return jsonify(result)


@app.route("/api/logs", methods=["GET"])
def api_logs():
    batch_id = request.args.get("batch_id", "").strip() or None
    log_type = request.args.get("type", "").strip() or None
    date_from = request.args.get("date_from", "").strip() or None
    date_to = request.args.get("date_to", "").strip() or None
    logs = log_service.get_logs(batch_id=batch_id, log_type=log_type, date_from=date_from, date_to=date_to)
    return jsonify({"logs": logs, "total": len(logs), "filters": {"batch_id": batch_id, "type": log_type, "date_from": date_from, "date_to": date_to}})


@app.route("/api/logs/batches", methods=["GET"])
def api_logs_batches():
    return jsonify({"batches": log_service.get_batch_options()})


@app.route("/api/crawl/stop", methods=["POST"])
def api_crawl_stop():
    if crawl_state["running"]:
        _stop_event.set()
        return jsonify({"status": "stopping"})
    return jsonify({"status": "not_running"})


@app.route("/api/crawl/resume", methods=["POST"])
def api_crawl_resume():
    if crawl_state["running"]:
        return jsonify({"error": "爬取正在进行中"}), 409

    body = request.json or {}
    start_date = (body.get("start_date") or crawl_state.get("start_date") or "").replace("-", "").strip()
    end_date = (body.get("end_date") or crawl_state.get("end_date") or "").replace("-", "").strip()
    leagues_input = (body.get("leagues") or "").strip()
    if not start_date or not end_date:
        return jsonify({"error": "缺少日期参数"}), 400

    allowed_leagues = None
    if leagues_input:
        allowed_leagues = {league.strip() for league in leagues_input.split(",") if league.strip()}

    _reset_crawl_state()
    _stop_event.clear()
    crawl_state.update({"running": True, "start_date": start_date, "end_date": end_date})

    t = threading.Thread(target=_run_crawler, args=(start_date, end_date, allowed_leagues), daemon=True)
    t.start()

    return jsonify({"status": "resumed", "start_date": start_date, "end_date": end_date, "leagues": list(allowed_leagues) if allowed_leagues else None})


@app.route("/api/crawl/close", methods=["POST"])
def api_crawl_close():
    _reset_crawl_state()
    _stop_event.set()
    return jsonify({"status": "closed"})


TARGET_BASE = "https://zq.titan007.com"


@app.route("/proxy/analysis/<path:subpath>")
def proxy_analysis(subpath):
    target_url = build_proxy_target_url(subpath, prefix="/analysis")
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")
    return proxy_request(target_url, rewrite=True)


@app.route("/proxy/<path:subpath>")
def proxy_static(subpath):
    target_url = build_proxy_target_url(subpath)
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")
    return proxy_request(target_url, rewrite=False)


@app.route("/proxy")
def proxy_fallback():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return Response("missing/invalid url parameter", status=400)
    return proxy_request(url, rewrite=True)


if __name__ == "__main__":
    import subprocess, socket

    def _kill_port(port=5000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, encoding="gbk"
            )
            pids = set()
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and f":{port}" in parts[1] and "LISTENING" in line:
                    pids.add(parts[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        except Exception:
            pass
    _kill_port()

    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
