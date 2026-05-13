---
name: pre-deploy-review
description: 部署前全面代码审查 — 检查前后端常见陷阱（重复调用、超时缺失、串行瓶颈、时区、CSS 冲突等）
trigger: 部署前、提交 PR 前、用户说 "review 代码" / "检查代码" / "上线前检查"
agent: pre-deploy-reviewer
---

# 部署前代码审查 Skill

## 目的

在代码推送/部署前，系统性地发现前端、后端、接口层面的常见问题，避免上线后才发现。

## 审查维度

### 1. 前端 JavaScript

逐文件检查 `frontend/js/*.js` 和 `docs/js/*.js`：

| 检查项 | 方法 | 严重度 |
|--------|------|--------|
| **重复 API 调用** | 搜索 `DOMContentLoaded` 内的直接调用 + `navigateTo` / `hashchange` 是否形成二次触发链 | 高 |
| **fetch 缺少 timeout** | 检查所有 `fetch()` 调用是否用了 `AbortController` 或 `apiFetch()` 包装 | 高 |
| **`location.hash =` 触发的 hashchange 循环** | 搜索 `location.hash =` 并确认不会同步触发 `hashchange` 导致 navigateTo 二次执行 | 高 |
| **事件监听重复绑定** | 搜索 `addEventListener` 是否在会重复执行的函数内（init/load/navigate），确认不会逐次累加 | 中 |
| **按钮无防抖/loading 状态** | 检查 POST/PUT 类按钮是否在点击后禁用、显示加载文案 | 中 |
| **长耗时操作超时** | 检查触达后端流水线（筛选、报告生成）的前端调用超时 ≥ 60s | 中 |
| **硬编码 API 地址** | 确认 `API_BASE` 根据 `location.hostname` 动态切换 localhost/生产 | 低 |
| **日期/时间计算** | 检查 `new Date()` 是否混用 UTC/本地时区，`getDay()` 返回值是否正确 | 低 |
| **Demo 降级逻辑** | 确认 API 失败后有合适的 fallback，且 catch 块不会吞掉用户操作的错误 | 低 |
| **`docs/` 和 `frontend/` 同步** | diff 两个目录确认关键修复未漏同步 | 中 |

### 2. 前端 CSS

| 检查项 | 方法 | 严重度 |
|--------|------|--------|
| **全局样式覆盖 SVG** | 搜索 `svg rect { fill: }` 或 `.logo svg` 等会覆盖内联 SVG 颜色的规则 | 高 |
| **hardcoded px vs var()** | 检查颜色值是否使用了 CSS 变量而非硬编码 | 低 |

### 3. 后端 Python

逐文件检查 `backend/` 目录：

| 检查项 | 方法 | 严重度 |
|--------|------|--------|
| **串行可并行化操作** | 搜索 `for .* in .*: await` 模式 — 独立请求应改为 `asyncio.gather` | 高 |
| **外部请求缺少 timeout** | 检查所有 `requests.get()` / `smtplib.SMTP` / `akshare` 调用是否有 timeout | 高 |
| **API router 无异常捕获** | 检查 endpoint 函数是否有 try/except，避免 500 裸奔 | 高 |
| **时区硬编码** | 搜索 `datetime.now()` 确认使用了 `BEIJING_TZ` 或 `timezone` | 中 |
| **`os.environ` 未设默认值** | 检查 `os.environ.get()` 调用是否提供了合理的 fallback | 中 |
| **SQLite 并发安全** | 确认使用了 `check_same_thread=False` | 低 |
| **CORS 范围** | 确认 `allow_origins` 是否过于宽松（生产环境应收紧） | 低 |

### 4. API 接口一致性

| 检查项 | 方法 |
|--------|------|
| **前后端路由匹配** | 前端 `apiFetch('/xxx')` → 对比后端 `router` prefix + path |
| **响应字段匹配** | 前端 `resp.field` → 对比后端返回 JSON 的 key |
| **HTTP 方法匹配** | 前端 `method: 'POST'` → 对比后端 `@router.post/get/delete` |

### 5. 配置文件

| 检查项 | 方法 |
|--------|------|
| `render.yaml` 启动命令 | 确认 `startCommand` 路径正确，`buildCommand` 能装完所有依赖 |
| `requirements.txt` 完整性 | 确认 API 依赖已取消注释，无遗漏的 import 对应的包 |
| `.env.example` 与代码一致 | env var 名称与 `os.environ.get()` 调用匹配 |
| GitHub Actions 兼容 | 确认 CI workflow 不会因新增依赖而中断 |

## 执行方式

每次用户请求或推送前，将任务拆分为 2-3 个后台 agent 并行执行：

```
Agent 1 (Frontend): 检查所有 JS/CSS 文件
Agent 2 (Backend):  检查所有 Python 文件
Agent 3 (Integration): 检查前后端接口一致性 + 配置文件
```

每个 agent 返回发现的问题列表，主 agent 汇总后给出：
1. **阻断项**（部署前必须修）
2. **建议项**（可部署后迭代）
3. **确认已修复的重复问题**

## 参考：本项目历史上线后发现的问题

这些问题如果在部署前审查就能避免：

1. `loadDashboard()` 被 `DOMContentLoaded` + `navigateTo` 两次调用 → **检查项 1.1**
2. `navigateTo` 内 `location.hash =` 触发 hashchange 导致所有页面加载二次执行 → **检查项 1.3**
3. `fetch()` 全部无 timeout，慢接口卡死页面 → **检查项 1.2**
4. `/screening/run` 超时 10s 但流水线需 60s+ → **检查项 1.5**
5. `collect_historical_batch` 156 只股票串行抓取 → **检查项 3.1**
6. SMTP 无 timeout 导致 5 分钟无响应 → **检查项 3.2**
7. `datetime.now()` 用 UTC 判断 A 股交易时间 → **检查项 3.4**
8. `.logo svg rect { fill: var(--primary) }` 覆盖新 logo 颜色 → **检查项 2.1**
9. `frontend/` 修复了但 `docs/` 未同步 → **检查项 1.10**
