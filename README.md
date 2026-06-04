# Football Data Hub — 功能实现文档 & 使用手册

> Titan007 足球赛事数据爬取、查询与分析平台

---

## 一、系统概述

### 1.1 项目定位

Football Data Hub 是一个基于 **Flask + Vue 3 + Playwright** 的足球赛事数据管理平台，核心功能包括：

- **自动爬取**：从 Titan007（球探体育）抓取足球比赛数据
- **智能过滤**：仅入库符合条件（主/客队近10场+战绩完整）的比赛
- **日期管理**：按爬取日期隔离数据，支持多日查询与对比
- **详情展示**：通过反向代理在 iframe 中嵌入原始分析页面
- **实时监控**：爬取进度实时展示，支持暂停/恢复/关闭

### 1.2 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 后端框架 | Flask 3.x | Web 服务 / API / 反向代理 |
| 爬虫引擎 | Playwright (Chromium) | 模拟浏览器抓取动态页面 |
| 前端框架 | Vue 3 (CDN) | 单页应用，响应式界面 |
| 数据存储 | SQLite | 轻量级本地数据库 |
| HTML 解析 | BeautifulSoup4 | 反向代理时精确重写 URL |
| HTTP 客户端 | Requests | 代理转发请求 |

### 1.3 文件结构

```
ClimbFootballData/
├── app.py              # Flask 主程序（API路由 + 反向代理）
├── titan_scraper.py    # Playwright 爬虫核心逻辑
├── db_manager.py       # SQLite 数据库操作层
├── templates/
│   └── index.html      # Vue 3 前端单页
├── requirements.txt    # Python 依赖
└── football_data.sqlite3 # SQLite 数据库文件（运行后自动生成）
```

---

## 二、核心功能详解

### 2.1 数据爬取

#### 数据来源

- **列表页**：`https://bf.titan007.com/football/Over_{YYYYMMDD}.htm`
- **详情页**：`https://zq.titan007.com/analysis/{match_id}cn.htm`
- **动态 API**：`https://zq.titan007.com/default/getScheduleInfo?sid={match_id}&t={timestamp}`

#### 爬取流程

```
用户点击「开始爬取」
    │
    ▼
生成列表页 URL（基于选择的日期）
    │
    ▼
Playwright 打开列表页 → 解析 <table id="table_live"> → 提取所有比赛基础信息
    │
    ▼ (逐场遍历)
Playwright 打开详情页 → 提取 hometeam/guestteam 变量 → 解析近 N 场战绩表格
    │
    ├── 近期场次 >= 10 场？ ──→ 合并数据 → 写入 SQLite ✓
    │
    └── 不满足条件 ──→ 跳过 ✗
    │
    ▼
全部完成 → 记录 crawl_session 状态
```

#### 入库条件（质量门槛）

一场比赛要被入库，必须同时满足：

1. **主队近况**：详情页中存在至少 10 条历史比赛记录（`table_hn` 或 `id="hn"` 区域）
2. **客队近况**：详情页中存在至少 10 条历史比赛记录（`table_an` 或 `id="an"` 区域）
3. **数据完整**：能解析出胜/平/负场次、胜率、赢率等统计指标

不满足条件的比赛会被跳过，不会写入数据库。

#### 反爬策略

- 使用 **Playwright 持久化上下文**（`launch_persistent_context`），模拟真实浏览器
- 随机延迟：每场比赛间隔 **1.5 ~ 3 秒**
- 标准 User-Agent + 中文 locale
- 视口设置为移动端尺寸（430×932），降低被检测概率

### 2.2 日期管理系统

#### 设计原则

| 规则 | 说明 |
|------|------|
| 爬取范围 | 仅允许选择**昨天及前七天**的日期 |
| 默认查询 | 展示**昨天**的数据 |
| 查询范围 | 支持前七天内任意有数据的日期 |
| 数据去重 | 同一日期多次爬取，**保留最新一次**（先删旧再写新） |

#### 日期限制实现

```javascript
// 前端日期选择器限制
const yesterday = new Date() - 1天          // 最大可选：昨天
const sevenDaysAgo = new Date() - 8天        // 最小可选：前7天
```

#### 数据隔离机制

每次执行爬取任务时：

1. 根据 `crawl_date` 参数删除该日期的所有旧数据
2. 重新爬取并写入新数据
3. 在 `crawl_sessions` 表中记录本次会话信息

### 2.3 爬取状态控制

系统支持四种状态切换：

```
        ┌───────────── 开始爬取 ─────────────┐
        │                                      │
        ▼                                      ▼
   [ running ] ──→ 停止 ──→ [ stopped ] ──→ 恢复 ──→ [ running ]
        │                                      │
        │         完成 / 出错                   │ 关闭
        ▼                                      ▼
   [ finished ]                         [ closed ]
```

| 操作 | API 接口 | 行为描述 |
|------|----------|----------|
| 开始爬取 | `POST /api/crawl/start` | 重置状态，启动后台线程 |
| 停止爬取 | `POST /api/crawl/stop` | 设置停止信号，等待当前比赛完成后终止 |
| 恢复爬取 | `POST /api/crawl/resume` | 从上次中断的位置重新开始（同日期） |
| 关闭任务 | `POST /api/crawl/close` | 清除内存状态，隐藏爬取面板 |

**状态持久化**：
- 运行中的状态保存在 Python 内存变量 `crawl_state` 中
- 爬取会话元信息持久化到 `crawl_sessions` 表
- 页面刷新后可通过 `/api/crawl/status` 恢复现场

### 2.4 反向代理（iframe 详情展示）

#### 为什么需要反向代理？

Titan007 的详情页设置了安全响应头（`X-Frame-Options`、`Content-Security-Policy`），直接用 `<iframe src="https://zq.titan007.com/...">` 会被浏览器拦截。

#### 解决方案架构

```
┌─────────────────────────────────────────────────┐
│                  用户浏览器                       │
│                                                  │
│  <iframe src="/proxy/analysis/2991286cn.htm">     │
│                                                  │
└──────────────────────┬──────────────────────────┘
                       │ HTTP 请求
                       ▼
┌─────────────────────────────────────────────────┐
│              Flask 后端 (localhost:5000)           │
│                                                  │
│  1. 接收 /proxy/analysis/2991286cn.htm            │
│  2. 拼接真实 URL: https://zq.titan007.com/...     │
│  3. 用浏览器 UA 请求 Titan007                      │
│  4. 删除 X-Frame-Options / CSP 安全响应头          │
│  5. BeautifulSoup 重写 HTML 中的相对路径:          │
│     /Style/analy.css  →  /proxy/Style/analy.css  │
│     /Script/xxx.js     →  /proxy/Script/xxx.js   │
│     /default/getScheduleInfo → /proxy/default/... │
│  6. 返回清洗后的内容给浏览器                        │
│                                                  │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼ （浏览器认为是自己的页面，不拦截）
              iframe 正常渲染 ✅
```

#### 代理路由设计

| 路由模式 | 匹配路径 | URL 重写 | 用途 |
|----------|----------|----------|------|
| `/proxy/analysis/<path>` | 详情页主入口 | ✅ 是 | 分析页面（含 JS 动态 URL 重写） |
| `/proxy/<path>` | 其他静态资源 | ❌ 否 | CSS / JS / 图片等原样转发 |
| `/proxy?url=<url>` | 兜底通用代理 | ✅ 是 | 兼容任意 URL |

#### URL 重写技术细节

使用 **BeautifulSoup DOM 操作**（而非正则替换），确保：

1. **HTML 标签属性**：`<link href>`、`<script src>`、`<img src>`、`<a href>` 等 → 精确修改为 `/proxy/...` 路径
2. **内联样式**：`background-image: url(/image/xxx.png)` → 重写为代理路径
3. **JS 字符串中的路径**：`document.write("...src='/default/getScheduleInfo...'")` → 通过字符串 replace 处理转义引号

---

## 三、数据库设计

### 3.1 ER 图

```
┌──────────────────────────────────────┐
│              matches                 │
├──────────────────────────────────────┤
│ match_id (PK)        TEXT           │
│ league_name          TEXT           │
│ match_time           TEXT           │
│ home_team            TEXT           │
│ away_team            TEXT           │
│ home_score           INTEGER        │
│ away_score           INTEGER        │
│ match_status         TEXT           │
│ source_url           TEXT           │
│ home_recent_summary  TEXT           │
│ away_recent_summary  TEXT           │
│ crawl_date           TEXT ← 日期索引  │
│ updated_at           TEXT           │
└──────────┬───────────────────────────┘
           │ 1:1
           ▼
┌──────────────────────────────────────┐
│           match_details              │
├──────────────────────────────────────┤
│ match_id (PK)        TEXT           │
│ home_stats_json      TEXT ← JSON     │
│ away_stats_json      TEXT ← JSON     │
│ updated_at           TEXT           │
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│          crawl_sessions              │
├──────────────────────────────────────┤
│ id (PK) = crawl_date  TEXT           │
│ status               TEXT           │
│ total                INTEGER        │
│ qualified            INTEGER        │
│ skipped              INTEGER        │
│ started_at           TEXT           │
│ finished_at          TEXT           │
│ updated_at           TEXT           │
└──────────────────────────────────────┘
```

### 3.2 核心字段说明

**matches.home_recent_summary / away_recent_summary** 格式示例：

```
近10场,胜5平1负4,胜率:50%赢率:42.8%大:0%单率:50%
```

字段含义：
- `近N场`：统计的比赛场次
- `胜X平Y负Z`：胜负平分布
- `胜率`：主/客队的胜场百分比
- `赢率`：赢盘率（含小数如 42.8%）
- `大`：大球率
- `单率`：单双率

---

## 四、API 接口文档

### 4.1 数据查询

#### `GET /api/matches`

查询比赛数据。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `match_id` | string | 否 | 按 ID 精确查询 |
| `date` | string | 否 | 按日期查询，格式 `YYYYMMDD`。默认为昨天 |

**响应示例：**

```json
{
  "matches": [
    {
      "match_id": "2991286",
      "league_name": "英超",
      "match_time": "2026-06-02 21:00",
      "home_team": "阿森纳",
      "away_team": "切尔西",
      "home_score": 2,
      "away_score": 1,
      "match_status": "完",
      "source_url": "https://zq.titan007.com/analysis/2991286cn.htm",
      "home_recent_summary": "近10场,胜7平2负1,胜率:70%赢率:55%大:40%单率:50%",
      "away_recent_summary": "近10场,胜4平3负3,胜率:40%赢率:35%大:50%单率:60%",
      "crawl_date": "20260602",
      "updated_at": "2026-06-03T08:30:15"
    }
  ],
  "total": 1,
  "query_date": "20260602"
}
```

#### `GET /api/dates`

获取所有已爬取数据的可用日期列表。

**响应示例：**

```json
{
  "dates": ["20260602", "20260601", "20260531", "20260530"]
}
```

### 4.2 爬取控制

#### `POST /api/crawl/start`

启动爬取任务。

**请求体：**

```json
{
  "crawl_date": "20260602",    // 可选，默认昨天
  "list_url": ""               // 可选，默认根据日期自动生成
}
```

**响应：**

```json
{
  "status": "started",
  "crawl_date": "20260602"
}
```

#### `GET /api/crawl/status`

获取当前爬取状态。

**响应：**

```json
{
  "running": true,
  "progress": 45,
  "total": 86,
  "current": 39,
  "current_match_id": "2991286",
  "current_match_name": "阿森纳 vs 切尔西",
  "qualified": 32,
  "skipped": 6,
  "finished": false,
  "error": null,
  "logs": [
    {"time": "14:30:01", "msg": "[39/86] 正在查询 2991286 阿森纳 vs 切尔西"},
    {"time": "14:30:05", "msg": "[39/86] 2991286 已入库"}
  ],
  "crawl_date": "20260602",
  "session": {
    "id": "20260602",
    "status": "running",
    "total": 86,
    "qualified": 32,
    "skipped": 6
  }
}
```

#### `POST /api/crawl/stop`

停止当前爬取任务（优雅停止，等待当前比赛处理完毕）。

#### `POST /api/crawl/resume`

恢复已停止的爬取任务（从同日期重新开始）。

#### `POST /api/crawl/close`

关闭爬取面板，清除内存状态。

### 4.3 反向代理

#### `GET /proxy/analysis/<subpath>`

代理 Titan007 的分析详情页，重写 HTML 中的资源路径。

**示例：**
- `/proxy/analysis/2991286cn.htm` → 代理 `https://zq.titan007.com/analysis/2991286cn.htm`
- `/proxy/analysis/2991286cn.htm?a=1&b=2` → 保留查询参数

#### `GET /proxy/<subpath>`

代理 Titan007 的静态资源（CSS/JS/图片），原样转发不做 URL 重写。

**示例：**
- `/proxy/Style/analy.css` → 代理 CSS 文件
- `/proxy/Script/analyTop.js` → 代理 JS 文件
- `/proxy/default/getScheduleInfo?sid=2991286&t=12345` → 代理动态数据接口

---

## 五、使用手册

### 5.1 环境准备

#### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单：
```
playwright>=1.50.0
flask>=3.0.0
requests>=2.28.0
beautifulsoup4    # 反向代理 URL 重写
```

首次安装 Playwright 浏览器：
```bash
playwright install chromium
```

#### 启动服务

```bash
python app.py
```

服务默认运行在 **http://127.0.0.1:5000**

> ⚠️ 注意：生产环境请勿使用 Flask 内置开发服务器，建议使用 Gunicorn/uWSGI。

### 5.2 操作指南

#### 步骤一：打开主页

浏览器访问 http://127.0.0.1:5000 ，默认显示昨天的已爬取数据。

#### 步骤二：执行爬取

1. 在工具栏找到「爬取日期」选择器（默认显示昨天）
2. 选择想要爬取的日期（仅限昨天及前七天）
3. 点击 **🚀 开始爬取** 按钮
4. 页面下方出现爬取面板，实时显示进度

爬取面板包含以下信息：

| 元素 | 说明 |
|------|------|
| 进度条 | 当前完成百分比 |
| 总计/进度 | 总比赛数 vs 已处理数 |
| 已入库 | 符合条件并成功入库的数量 |
| 已跳过 | 不满足条件被跳过的数量 |
| 当前处理 | 正在处理的比赛名称 |
| 日志区 | 实时滚动的操作日志 |

#### 步骤三：爬取过程中的操作

**暂停爬取**：点击 **⏹ 停止** 按钮
- 系统会等待当前比赛处理完毕后停止
- 面板标题变为「⏸ 爬取已暂停」

**恢复爬取**：点击 **▶️ 恢复爬取** 按钮
- 使用相同日期重新开始爬取
- 同日期旧数据会在开始时自动清除

**关闭面板**：点击 **❌ 关闭** 按钮
- 清除内存中的爬取状态
- 面板消失，回到正常浏览模式
- 刷新页面后不会再显示爬取元素

#### 步骤四：查看数据

**按日期查询**：
1. 在「查询日期」选择器中选择日期
2. 点击 **📋 查询** 按钮
3. 底部表格刷新为对应日期的数据

**按 ID 查询**：
1. 在搜索框输入比赛 ID（如 `2992577`）
2. 点击 **🔍 查询** 按钮
3. 显示匹配的单条记录

#### 步骤五：查看详情

1. 在数据表格中找到目标比赛
2. 点击右侧 **「详情」** 按钮
3. 弹出详情弹窗，分为左右两栏：

| 区域 | 内容 |
|------|------|
| 左侧面板 | 本地数据库中的结构化信息（球队、比分、近期战绩摘要） |
| 右侧面板 | 通过反向代理嵌入的 Titan007 原始分析页面的 iframe |

弹窗顶部提供 **「新窗口打开」** 和 **「关闭」** 按钮。

### 5.3 日常使用流程

```
每天早上打开系统
    │
    ├─ 昨天已有数据？ → 直接查看/查询
    │
    └─ 需要更新？
        │
        ▼
    选择昨天日期 → 点击「开始爬取」
        │
        ▼ (等待完成)
    自动显示最新数据
        │
        ▼
    点「详情」查看完整的 Titan007 分析页面
```

---

## 六、常见问题

### Q1: 爬取速度慢怎么办？

这是正常现象。系统设计了 **1.5~3 秒/场** 的随机延迟以避免被封禁。一场比赛约需 5-8 秒（含页面加载）。86 场比赛大约需要 **10~15 分钟**。

### Q2: 为什么有些比赛被跳过了？

只有主客双方都拥有 **≥10 场近期战绩** 的比赛才会入库。这是为了确保数据分析的质量。被跳过的比赛通常是因为：
- 新球队/青年队，历史数据不足
- 近期比赛场次不够

### Q3: 详情页 iframe 显示空白或样式异常？

可能原因：
1. Titan007 服务器暂时不可达 → 检查网络连接
2. 代理路由返回错误 → 查看 F12 控制台的网络请求
3. CSS/JS 资源加载失败 → 通常由相对路径重写问题导致，已通过 BS4 方案修复

### Q4: 可以同时运行多个爬取任务吗？

不可以。同一时间只允许一个爬取任务运行。如果需要爬取不同日期的数据，请依次执行。

### Q5: 数据库文件在哪里？

SQLite 数据库文件位于项目根目录：`football_data.sqlite3`。可以直接用 SQLite 工具（如 DB Browser for SQLite）打开查看。

### Q6: 如何备份/迁移数据？

直接复制 `football_data.sqlite3` 文件即可。这是一个独立的单文件数据库。

### Q7: 页面刷新后爬取面板还在吗？

取决于爬取状态：
- **正在运行/已完成/出错/已暂停** → 刷新后会恢复显示（通过 `/api/crawl/status` 获取状态）
- **已关闭** → 刷新后不再显示，默认展示昨天的数据

---

## 七、开发注意事项

### 7.1 Flask Debug 模式

本项目 **必须设置 `use_reloader=False`**：

```python
app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
```

原因：Flask 的 auto-reload 机制会重启进程，导致后台爬虫线程被杀掉。

### 7.2 端口冲突

如果遇到端口 5000 被占用：
```bash
netstat -ano | findstr :5000
taskkill /F /PID <进程ID>
```

### 7.3 Playwright 浏览器缓存

项目使用 `./playwright_profile` 目录作为 Chromium 持久化配置目录。如果遇到登录态失效等问题，可尝试删除此目录让 Playwright 重新初始化。

### 7.4 扩展建议

| 方向 | 建议 |
|------|------|
| 数据导出 | 添加 CSV/Excel 导出功能 |
| 定时任务 | 使用 APScheduler 实现每日自动爬取 |
| 多线程爬取 | 将 Playwright 改为多标签页并发（注意频率限制） |
| 数据分析 | 基于 Pandas 做胜率趋势、赔率分析图表 |
| 部署上线 | 使用 Gunicorn + Nginx 反代部署到云服务器 |

---

*文档版本：v2.0 | 最后更新：2026-06-04*
