from __future__ import annotations

"""代理访问辅助服务。\n\n这个模块负责把对 titan007 的静态资源和页面请求代理出去，\n并在必要时重写 HTML 中的资源链接。\n\n保持原始行为不变，只是把与代理相关的逻辑从 `app.py` 中拆出来，\n让路由文件更聚焦于接口编排。"""

import re
from urllib.parse import urljoin

import requests as http_requests
from flask import Response, request
from bs4 import BeautifulSoup


PROXY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://zq.titan007.com/",
}

TARGET_BASE = "https://zq.titan007.com"
STRIP_HEADERS = {
    "x-frame-options", "content-security-policy", "strict-transport-security",
    "content-encoding", "content-length", "transfer-encoding", "connection",
    "content-security-policy-report-only", "permissions-policy", "referrer-policy",
}


def build_proxy_target_url(subpath: str, prefix: str = "") -> str:
    """拼接代理目标地址。"""
    target_url = f"{TARGET_BASE}{prefix}/{subpath}" if prefix else f"{TARGET_BASE}/{subpath}"
    return target_url


def proxy_request(target_url: str, rewrite: bool = False):
    """向远端站点发起代理请求。"""
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
        body = rewrite_html(resp.text, target_url)
    else:
        body = resp.content

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in STRIP_HEADERS}
    return Response(body, status=resp.status_code, headers=out_headers, content_type=ct)


def rewrite_html(html: str, page_url: str) -> str:
    """重写 HTML 里的资源地址，让它们走本地代理。"""
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
                abs_url = urljoin(page_url, raw)
            rel = abs_url.replace(TARGET_BASE + "/", "", 1) if abs_url.startswith(TARGET_BASE) else abs_url
            return f"url({q}/proxy/{rel}{q})"

        tag["style"] = re.sub(r'url\((["\']?)([^)]+)\1?\)', _css_url, style, flags=re.I)

    result = str(soup)

    for prefix in ["/default/", "/Script/", "/Style/"]:
        result = result.replace(f"\'{prefix}", f"\'/proxy/{prefix.lstrip('/')}/")
        result = result.replace(f'\"{prefix}', f'"/proxy/{prefix.lstrip("/")}/')

    return result
