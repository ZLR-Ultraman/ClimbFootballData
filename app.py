from __future__ import annotations

import asyncio
import random
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests as http_requests
from flask import Flask, Response, jsonify, render_template, request

from db_manager import DatabaseManager

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"), static_folder=str(Path(__file__).parent / "static"))
db = DatabaseManager(Path(__file__).parent / "football_data.sqlite3")

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
    "finished": False,
    "error": None,
    "logs": [],
    "crawl_date": None,
}

_stop_event = threading.Event()


def _reset_crawl_state():
    crawl_state.update({
        "running": False, "progress": 0, "total": 0, "current": 0,
        "current_match_id": "", "current_match_name": "",
        "qualified": 0, "skipped": 0, "finished": False, "error": None, "logs": [],
        "crawl_date": None,
    })


def _add_log(msg):
    crawl_state["logs"].append({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})
    if len(crawl_state["logs"]) > 200:
        crawl_state["logs"] = crawl_state["logs"][-100:]


def _run_crawler(list_url=None, crawl_date=None):
    if list_url is None:
        from titan_scraper import LIST_URL
        list_url = LIST_URL

    async def _crawl():
        from titan_scraper import TitanScraper
        db_path = str(Path(__file__).parent / "football_data.sqlite3")
        db_local = DatabaseManager(db_path)
        _cd = crawl_date

        db_local.upsert_crawl_session(_cd, status="running", started_at=datetime.utcnow().isoformat())

        old_count = db_local.delete_matches_by_date(_cd)
        if old_count > 0:
            _add_log(f"已清除日期 {_cd} 的旧数据 {old_count} 条")

        _add_log(f"正在连接列表页: {list_url}")
        async with TitanScraper(db=db_local, headless=True) as scraper:
            matches = await scraper.fetch_list_page(list_url)
            if not matches:
                _add_log(f"列表页未获取到比赛数据，尝试前一天...")
                fallback_dt = datetime.strptime(_cd, "%Y%m%d") - timedelta(days=1)
                fallback_date = fallback_dt.strftime("%Y%m%d")
                fallback_url = f"https://bf.titan007.com/football/Over_{fallback_date}.htm"
                _add_log(f"正在连接: {fallback_url}")
                matches = await scraper.fetch_list_page(fallback_url)
                if matches:
                    _cd = fallback_date
                    crawl_state["crawl_date"] = fallback_date
                    old_count = db_local.delete_matches_by_date(_cd)
                    if old_count > 0:
                        _add_log(f"已清除日期 {_cd} 的旧数据 {old_count} 条")
            crawl_state["total"] = len(matches)
            _add_log(f"列表页获取到 {len(matches)} 场比赛")

            for i, row in enumerate(matches):
                if _stop_event.is_set():
                    _add_log("用户已停止爬取")
                    break

                match_id = row["match_id"]
                home = row.get("home_team") or ""
                away = row.get("away_team") or ""
                crawl_state["current"] = i + 1
                crawl_state["current_match_id"] = match_id
                crawl_state["current_match_name"] = f"{home} vs {away}"
                crawl_state["progress"] = int((i + 1) / len(matches) * 100)

                _add_log(f"[{i+1}/{len(matches)}] 正在查询 {match_id} {home} vs {away}")

                detail = await scraper.get_qualified_recent_stats(match_id)
                if detail:
                    row_with_date = dict(row)
                    row_with_date["crawl_date"] = _cd
                    crawl_state["qualified"] += 1
                    await scraper.sync_match(match_id, detail=detail, base_row=row_with_date)
                    _add_log(f"[{i+1}/{len(matches)}] {match_id} 已入库")
                else:
                    crawl_state["skipped"] += 1
                    _add_log(f"[{i+1}/{len(matches)}] {match_id} 已跳过")

                await asyncio.sleep(random.uniform(1.5, 3.0))

            crawl_state["finished"] = True
            crawl_state["running"] = False
            final_status = "completed" if not _stop_event.is_set() else "stopped"
            db_local.upsert_crawl_session(
                _cd, status=final_status,
                total=crawl_state["total"], qualified=crawl_state["qualified"],
                skipped=crawl_state["skipped"], finished_at=datetime.utcnow().isoformat(),
            )
            _add_log(f"爬取完成！入库 {crawl_state['qualified']} 条，跳过 {crawl_state['skipped']} 条")

    try:
        asyncio.run(_crawl())
    except Exception as e:
        crawl_state["error"] = str(e)
        crawl_state["running"] = False
        db.upsert_crawl_session(crawl_date, status="cancelled", finished_at=datetime.utcnow().isoformat())
        _add_log(f"爬取出错: {e}")


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


@app.route("/api/crawl/start", methods=["POST"])
def api_crawl_start():
    if crawl_state["running"]:
        return jsonify({"error": "爬取正在进行中"}), 409

    body = request.json or {}
    list_url = body.get("list_url")
    crawl_date = body.get("crawl_date")

    if not crawl_date:
        target = datetime.now() - timedelta(days=1)
        crawl_date = target.strftime("%Y%m%d")

    if not list_url:
        list_url = f"https://bf.titan007.com/football/Over_{crawl_date}.htm"

    _reset_crawl_state()
    _stop_event.clear()
    crawl_state["running"] = True
    crawl_state["crawl_date"] = crawl_date

    t = threading.Thread(target=_run_crawler, args=(list_url, crawl_date), daemon=True)
    t.start()

    return jsonify({"status": "started", "crawl_date": crawl_date})


@app.route("/api/crawl/status", methods=["GET"])
def api_crawl_status():
    result = dict(crawl_state)
    if result.get("crawl_date"):
        session = db.get_crawl_session(result["crawl_date"])
        if session:
            result["session"] = session
    return jsonify(result)


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
    crawl_date = body.get("crawl_date")
    if not crawl_date and crawl_state.get("crawl_date"):
        crawl_date = crawl_state["crawl_date"]
    if not crawl_date:
        return jsonify({"error": "缺少日期参数"}), 400

    list_url = f"https://bf.titan007.com/football/Over_{crawl_date}.htm"

    _reset_crawl_state()
    _stop_event.clear()
    crawl_state["running"] = True
    crawl_state["crawl_date"] = crawl_date

    t = threading.Thread(target=_run_crawler, args=(list_url, crawl_date), daemon=True)
    t.start()

    return jsonify({"status": "resumed", "crawl_date": crawl_date})


@app.route("/api/crawl/close", methods=["POST"])
def api_crawl_close():
    _reset_crawl_state()
    _stop_event.set()
    return jsonify({"status": "closed"})


TARGET_BASE = "https://zq.titan007.com"
STRIP_HEADERS = {
    "x-frame-options", "content-security-policy", "strict-transport-security",
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "content-security-policy-report-only", "permissions-policy", "referrer-policy",
}


@app.route("/proxy/analysis/<path:subpath>")
def proxy_analysis(subpath):
    target_url = f"{TARGET_BASE}/analysis/{subpath}"
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")
    return _do_proxy(target_url, rewrite=True)


@app.route("/proxy/<path:subpath>")
def proxy_static(subpath):
    target_url = f"{TARGET_BASE}/{subpath}"
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")
    return _do_proxy(target_url, rewrite=False)


@app.route("/proxy")
def proxy_fallback():
    url = request.args.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return Response("missing/invalid url parameter", status=400)
    return _do_proxy(url, rewrite=True)


def _do_proxy(target_url: str, rewrite: bool = False):
    fwd_headers = {
        **PROXY_HEADERS,
        "User-Agent": request.headers.get("User-Agent", PROXY_HEADERS["User-Agent"]),
        "Referer": TARGET_BASE,
    }
    try:
        resp = http_requests.get(target_url, headers=fwd_headers, timeout=30, allow_redirects=True)
    except http_requests.RequestException as e:
        return Response(f"Proxy error: {e}", status=502)

    ct = resp.headers.get("Content-Type", "")
    ct_lower = ct.lower()
    is_html = "text/html" in ct_lower or "application/xhtml" in ct_lower

    if is_html and rewrite:
        resp.encoding = resp.apparent_encoding or "utf-8"
        body = _rewrite_html(resp.text, target_url)
    else:
        if "charset" not in ct_lower and ("text/" in ct_lower or "application/javascript" in ct_lower or "application/json" in ct_lower):
            resp.encoding = resp.apparent_encoding or "utf-8"
            body = resp.content
        else:
            body = resp.content

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in STRIP_HEADERS}
    return Response(body, status=resp.status_code, headers=out_headers, content_type=ct)


def _rewrite_html(html: str, page_url: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["link", "script", "img", "a", "iframe", "form", "input"]):
        for attr in ("src", "href", "action", "srcset", "content"):
            val = tag.get(attr)
            if not val:
                continue
            if val.startswith(("#", "javascript:", "data:", "mailto:", "/proxy")):
                continue
            if val.startswith("//"):
                val = "https:" + val
            elif val.startswith("/"):
                val = TARGET_BASE + val
            elif not val.startswith("http"):
                from urllib.parse import urljoin
                val = urljoin(page_url, val)
            if val.startswith(TARGET_BASE):
                rel = val[len(TARGET_BASE):].lstrip("/")
                tag[attr] = f"/proxy/{rel}"

    for tag in soup.find_all(style=True):
        style = tag.get("style", "")
        def _css_url(m):
            q, raw = m.group(1), m.group(2)
            if raw.startswith(("/proxy", "data:", "http")):
                return m.group(0)
            if raw.startswith("//"):
                abs_url = "https:" + raw
            elif raw.startswith("/"):
                abs_url = TARGET_BASE + raw
            else:
                from urllib.parse import urljoin
                abs_url = urljoin(page_url, raw)
            rel = abs_url.replace(TARGET_BASE + "/", "", 1) if abs_url.startswith(TARGET_BASE) else abs_url
            return f"url({q}/proxy/{rel}{q})"
        tag["style"] = re.sub(r'url\((["\']?)([^)]+)\1?\)', _css_url, style, flags=re.I)

    result = str(soup)

    for prefix in ["/default/", "/Script/", "/Style/"]:
        result = result.replace(f"\'{prefix}", f"\'/proxy/{prefix.lstrip('/')}/")
        result = result.replace(f'\"{prefix}', f'"/proxy/{prefix.lstrip("/")}/')

    return result


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
