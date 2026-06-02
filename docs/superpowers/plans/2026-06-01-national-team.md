# 国家队动向 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增国家队动向页面、后端真实数据采集接口和每日邮件摘要。

**Architecture:** 后端新增独立采集源、服务层、API 路由和数据库表；每日流水线在发邮件前刷新国家队数据；静态前端新增菜单页面并复用现有卡片、表格、状态样式。默认摘要、邮件和页面列表只展示最近一次刷新批次触达的数据，历史报告期通过 `period` 参数查询，避免历史缓存被误读为最新动向。

**Tech Stack:** Python FastAPI、SQLAlchemy、SQLite、东方财富股东分析接口、原生 HTML/CSS/JS、unittest/pytest。

---

## 文件结构

- 创建 `backend/agents/layer1_data_collector/sources/national_team.py`：封装东方财富股东分析接口、主体匹配、字段归一化。
- 创建 `backend/services/national_team_service.py`：负责刷新、入库、变动检测、摘要查询、事件查询。
- 创建 `backend/api/router_national_team.py`：提供 `/api/v1/national-team` API。
- 修改 `backend/db/models.py`：新增国家队持仓、变动、事件、快照表。
- 修改 `backend/main.py`：注册 API 路由，并在每日流水线发邮件前刷新国家队数据。
- 修改 `backend/agents/layer3_recommendation/agent.py`：把国家队摘要传给通知器。
- 修改 `backend/agents/layer3_recommendation/notifier.py`：邮件 HTML/文本新增国家队动向模块。
- 修改 `frontend/index.html`：新增菜单和页面结构。
- 修改 `frontend/js/app.js`：新增导航、数据加载、刷新、表格渲染。
- 修改 `frontend/css/styles.css`：新增国家队页面样式，沿用现有视觉风格。
- 同步 `frontend/` 到 `docs/`。
- 创建测试 `tests/test_national_team_source.py`、`tests/test_national_team_service.py`、`tests/test_national_team_api.py`。

## 任务

### Task 1: 数据源验证与字段归一化

**Files:**
- Create: `tests/test_national_team_source.py`
- Create: `backend/agents/layer1_data_collector/sources/national_team.py`

- [ ] Step 1: 写主体匹配和字段归一化失败测试。
- [ ] Step 2: 运行测试确认失败。
- [ ] Step 3: 实现主体匹配、报告期归一化、数值清洗和东方财富字段映射。
- [ ] Step 4: 运行测试确认通过。
- [ ] Step 5: 用真实东方财富接口探测最近可用报告期和字段。

### Task 2: 数据库模型与服务层

**Files:**
- Modify: `backend/db/models.py`
- Create: `tests/test_national_team_service.py`
- Create: `backend/services/national_team_service.py`

- [ ] Step 1: 写快照入库、去重、变动检测测试。
- [ ] Step 2: 运行测试确认失败。
- [ ] Step 3: 新增模型和服务层最小实现。
- [ ] Step 4: 运行测试确认通过。

### Task 3: API 路由

**Files:**
- Create: `backend/api/router_national_team.py`
- Modify: `backend/main.py`
- Create: `tests/test_national_team_api.py`

- [ ] Step 1: 写 summary、holdings、events、refresh API 测试。
- [ ] Step 2: 运行测试确认失败。
- [ ] Step 3: 实现 API 并注册路由。
- [ ] Step 4: 运行测试确认通过。

### Task 4: 每日流水线和邮件接入

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/agents/layer3_recommendation/agent.py`
- Modify: `backend/agents/layer3_recommendation/notifier.py`
- Modify: `tests/test_notification_enrichment.py`

- [ ] Step 1: 写邮件文本和 HTML 包含国家队动向、来源和空状态的测试。
- [ ] Step 2: 运行测试确认失败。
- [ ] Step 3: 在每日流水线中刷新国家队数据并传入邮件通知器。
- [ ] Step 4: 实现邮件模块渲染。
- [ ] Step 5: 运行测试确认通过。

### Task 5: 前端国家队动向页面

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/js/app.js`
- Modify: `frontend/css/styles.css`
- Modify: `tests/test_frontend_responsive_css.py`

- [ ] Step 1: 写前端静态结构和响应式样式测试。
- [ ] Step 2: 运行测试确认失败。
- [ ] Step 3: 新增菜单、页面、加载逻辑、刷新逻辑和状态处理。
- [ ] Step 4: 补充页面样式，复用现有设计风格。
- [ ] Step 5: 运行测试确认通过。

### Task 6: 同步 docs 与本地验证

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/js/app.js`
- Modify: `docs/css/styles.css`

- [ ] Step 1: 同步 `frontend/` 到 `docs/`。
- [ ] Step 2: 运行后端测试。
- [ ] Step 3: 启动后端服务验证 API。
- [ ] Step 4: 启动前端静态服务验证页面。
- [ ] Step 5: 做代码审查，修复发现的问题。
