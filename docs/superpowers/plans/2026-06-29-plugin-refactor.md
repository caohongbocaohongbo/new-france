# 2026-06-29 插件化重构总结

> **本文用途**：记录 2026-06-29 这一天把项目从「策略代码与主项目深度耦合」改造为「策略 = 独立可迁移插件」的关键改动。后续任何人维护或迁移插件时，可先读本文。

---

## 背景

主项目原有策略代码（涨停回撤 / 隔夜套利）直接散落在 `backend/services/` 与 `backend/agents/` 下，与 Layer1/2/3、邮件 notifier、运行时配置深度耦合，难以单独迁移到新项目。

业务诉求：**每个策略 = 一个独立目录，整目录复制就能在新项目跑起来，与主项目互不污染**。

本日完成：
1. **新增「主力资金双向监控」插件** — 全新功能，从零写在 `backend/plugins/principal_capital/`
2. **重构「隔夜套利」为独立插件** — 把 1098 行旧业务整体迁入 `backend/plugins/overnight_arbitrage/`，删除主项目下的旧文件
3. **「涨停回撤」完全不动** — 仍保留在原 `screening_service.py` + Layer1/2/3 体系下

---

## 主项目唯一耦合点（极简注入）

```
backend/main.py             try-except 注入两个插件 router + CLI 转发
frontend/index.html         顶层导航 +2 菜单项 / +2 独立 page section
frontend/js/app.js          路由分支 +2 / 标题映射 +2
docs/*                      与 frontend 镜像同步
.github/workflows/principal-capital-scan.yml   新增 5min Cron
```

主项目敏感模块（screening_service / drawdown / layer1-3 / notifier / models / router_watchlist）**零修改**。

---

## 插件目录骨架（两个插件一致）

```
backend/plugins/<name>/
├── __init__.py        # 仅暴露 register_router() / run_cli(args)
├── config.py          # 路径/阈值/SMTP，环境变量可全覆盖（迁移时无需改代码）
├── notifier.py        # 自带 SMTP，不依赖 layer3 notifier
├── service.py         # 业务核心
├── router.py          # FastAPI endpoint
├── sources/           # 数据源（独立复制，不引用主项目 layer1）
└── tests/             # 单元测试
```

前端对应：
```
frontend/js/plugins/<name>.js   独立 JS 模块, 自带 fallback 工具函数
docs/js/plugins/<name>.js       镜像
```

---

## 关键设计原则（写入插件代码以便自我守护）

| 原则 | 体现 |
|---|---|
| 邮件独立 | 每个插件 `notifier.py` 直接调用 SMTP，读 `SMTP_*` 环境变量；不调主项目 `_send_email` |
| 数据源独立 | 隔夜套利 `sources/zt_pool.py` 是主项目 `eastmoney_zt.py` 的 fork 副本，文件头标注派生关系 |
| 路径可覆盖 | 隔夜套利支持 `OA_DATA_DIR / OA_REPORT_DIR` 环境变量；插件运行时数据写到 `data/` 但都加专属命名前缀 |
| 前端 fallback | 插件 JS 顶部 `typeof apiFetch === 'function' ? apiFetch : <自带 fallback>`，主项目无该全局函数时也能跑 |
| 可选加载 | `backend/main.py` 用 `try/except ImportError`，删除插件目录后主项目仍能启动 |

---

## 主力资金插件特有能力（首次实现）

- 多源熔断：东方财富 push2 → push2his → akshare → 本地缓存降级
- 双向信号：买入 ≥50% 净流入 / 卖出 ≥30% 净流出（分 warn/alert/danger）
- 去重差异化：买入当日不重复 / 卖出 60min 冷却
- 抽样核验：可选启用 sina 单股核验前 5 名误差 < 10%
- 5min Cron：北京时间 9:30–15:00

---

## .gitignore 增补（保护邮件/监控列表不被推）

```gitignore
# 邮件密码（之前已忽略）
.env

# 监控列表数据 + 数据库（15:10 尾盘任务生成，之前已忽略）
data/*.db
data/france.md
data/source_health.json

# 每日报告（之前已忽略）
reports/*.md / *.html / *.json

# 本日新增：插件运行时数据
data/principal_capital_*.json
data/principal_capital_cache.*
data/fund_flow_*

# 本日新增：codex 工具本地环境
.codex/
```

---

## 迁移到新项目的步骤（30 秒上手）

```bash
# 后端
cp -r backend/plugins/<plugin_name> /new_project/backend/plugins/
# 在新项目 FastAPI 入口加 5 行：
try:
    from backend.plugins.<plugin_name> import register_router
    app.include_router(register_router(), prefix="/api/v1/<plugin_name>")
except ImportError:
    pass

# 前端
cp frontend/js/plugins/<plugin_name>.js /new_project/js/plugins/
# 在新项目 index.html 加 1 个菜单项、1 个 page section、1 行 <script>
# 在新项目 app.js 加 1 行路由分支

# 环境变量
export SMTP_HOST=... SMTP_USER=... SMTP_PASSWORD=... SMTP_TO=...
export OA_DATA_DIR=... OA_REPORT_DIR=...   # 隔夜套利可选
```

迁移**不需要复制主项目任何工具函数、邮件模块、数据源代码**。

---

## 推送结果

| commit | 简述 |
|---|---|
| `d95ba97` | feat(plugins): 新增主力资金双向监控插件 |
| `6da97c4` | refactor(plugins): 隔夜套利重构为独立插件 + 前端两插件菜单集成 |

42 files changed, +3461 / −523。已推 `origin/main`。

---

## 验证标准（CI 应保持绿）

```bash
# 主项目 unittest
python3 -m unittest discover tests/             # 80/80 通过

# 插件 unittest
python3 -m unittest backend.plugins.principal_capital.tests.test_service \
                    backend.plugins.principal_capital.tests.test_eastmoney \
                    backend.plugins.principal_capital.tests.test_sina \
                    backend.plugins.principal_capital.tests.test_multi_source
python3 -m unittest backend.plugins.overnight_arbitrage.tests.test_service \
                    backend.plugins.overnight_arbitrage.tests.test_pipeline \
                    backend.plugins.overnight_arbitrage.tests.test_notify

# 插件迁移性自检
mv backend/plugins/<name> /tmp/_bak
python3 -c "from backend.main import get_app; get_app()"   # 应仍能启动
mv /tmp/_bak backend/plugins/<name>
```

---

## 后续待办（已识别，不属于今日范围）

1. **主力资金路径环境变量化** — 目前只有隔夜套利做了 `OA_DATA_DIR/REPORT_DIR`，主力资金 plugin 也建议补 `PC_DATA_DIR/REPORT_DIR` 以对齐迁移性
2. **涨停回撤插件化** — 工程量大（约 4000 行 + Layer1/2/3 + EventEngine）。建议先做「核心抽离」再决定是否迁入 plugin
3. **历史报告 fork 维护** — `backend/plugins/overnight_arbitrage/sources/zt_pool.py` 是主项目 `eastmoney_zt.py` 的派生副本，主项目修 bug 时需人工评估是否同步回插件
4. **GitHub Actions 额度** — 主力资金 5min Cron 每月约 1200–1500 分钟，私人仓库 2000 分钟免费额度临界。如要更稳定，考虑：(a) 仓库设为 Public；(b) 迁 Render Worker；(c) 自建 VPS

---

## 注意事项 / 维护红线

- ❌ **不要**在插件 service 里直接 import 主项目 `notifier` / `eastmoney_zt` / `layer3` 等业务模块
- ❌ **不要**为了"复用代码"删除插件自带的 fallback / 数据源副本
- ❌ **不要**在主项目其他模块 import 插件内部符号（除了 `register_router` / `run_cli` 两个对外入口）
- ✅ 插件唯一允许复用的主项目函数：`backend.api.router_system.trading_session_status`（纯只读交易时段判断）
- ✅ 修改插件后跑 `tests/test_frontend_responsive_css.py` 守护前端 fallback 不被破坏
