# AGENTS.md — new-france 尾盘涨停选股系统

## 项目概述

A 股尾盘涨停股监控与多因子推荐系统。三层 Agent 架构：
- **Layer 1**: 数据采集（东方财富涨停股池、实时行情、历史 K 线）
- **Layer 2**: 多因子信号引擎（回撤、量比、均线、PE 等 11 因子）
- **Layer 3**: 推荐生成 + 邮件通知

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python FastAPI + SQLite (SQLAlchemy) + uvicorn |
| 前端 | 原生 HTML/CSS/JS（无框架，无构建） |
| 部署 | Render（Web Service + Static Site + Cron） |
| 数据源 | 东方财富 API、新浪 API、akshare（可选） |

## 项目结构

```
new-france/
├── backend/                    # Python 后端
│   ├── main.py                # CLI + API 入口
│   ├── agents/                # 三级 Agent
│   │   ├── layer1_data_collector/
│   │   ├── layer2_signal_engine/
│   │   └── layer3_recommendation/
│   ├── api/router_*.py        # FastAPI 路由
│   ├── db/database.py         # SQLAlchemy ORM
│   ├── events/engine.py       # 事件驱动引擎
│   └── services/screening_service.py  # 筛选流水线
├── frontend/                   # 前端静态文件（Render Static Site）
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
├── docs/                       # 开发文档（产品/施工方案；非前端镜像）
├── config/settings.py          # Pydantic Settings 配置中心
├── data/                       # 运行时数据（france.md + SQLite DB）
├── reports/                    # 每日报告输出
├── render.yaml                 # Render Blueprint
├── requirements.txt
├── AGENTS.md                   # 本文件
└── .Codex/skills/             # Agent Skills
    └── pre-deploy-review.md    # 部署前代码审查
```

## 开发工作流

### 部署前必做

```
"用 pre-deploy-review skill 检查代码"
```

该 skill 会并行启动 2-3 个 agent 审查前后端代码，覆盖：
- 前端：重复 API 调用、fetch timeout、hashchange 循环、button loading 状态
- 后端：串行化并行优化、timeout 缺失、时区问题、异常处理
- 接口：前后端路由/字段/HTTP 方法一致性
- 配置：render.yaml / requirements.txt / .env 完整性

### 常用命令

```bash
cd /Users/fangcang/Documents/Codex/projects/new-france

# 本地开发
pip install -r requirements.txt
python -m backend.main --serve       # 启动 API (localhost:8000)

# 数据库
python -m backend.main --init-db     # 初始化表结构

# 测试
python -m backend.main --test-email  # 测试邮件
python -m backend.main --dry-run     # 筛选不发通知
```

### 前端开发注意事项

1. **一律使用 `apiFetch(path, opts)`** 发起 API 请求，不要直接用 `fetch()`
   - `apiFetch` 自带 10s 超时和 AbortController
   - 长耗时操作传 `{ timeout: 120000 }`
2. **导航用 `navigateTo(page)`，内部用 `history.replaceState`**（不是 `location.hash =`）
3. **init 时只让 `navigateTo` 触发数据加载**，不要在 `DOMContentLoaded` 里直接调 load 函数

### 后端开发注意事项

1. **外部 HTTP 调用必须设置 timeout**
2. **独立请求用 `asyncio.gather` 并行化**，用 `Semaphore` 限流
3. **时间用 `BEIJING_TZ = timezone(timedelta(hours=8))`**，禁止裸 `datetime.now()`
4. **API endpoint 要有 try/except** 返回有意义的错误信息

## 部署架构

```
Render (Oregon)
├── new-france-api       Web Service    — Python FastAPI
├── new-france           Static Site    — frontend/
└── new-france-daily-*   Cron Job       — UTC 07:10 = 北京 15:10
```

环境变量（Render Dashboard 配置）：
- `PORT` — Render 自动注入
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` — 邮件通知
- `EMAIL_TO` — 收件邮箱
- `DEBUG` — 调试模式

## 已知限制

- SQLite + Render 免费层文件系统=临时存储，每次部署数据清空，由 Cron 重建
- QQ 邮箱 SMTP 封锁海外 IP，已切换 Gmail
- Render 免费层 15 分钟无请求休眠，用 UptimeRobot 保持热度
