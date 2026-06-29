# 尾盘隔夜套利实施计划

> **⚠️ 已重构为插件 (2026-06-29 更新)：**
> 本计划是 2026-06-04 的初版实施记录，文中提到的文件路径已发生变化。当前实际形态：
> - 后端业务已迁移到 `backend/plugins/overnight_arbitrage/`（独立插件目录，可整体迁移到其他项目）
> - 前端已从「手动筛选」抽离为顶层菜单「隔夜套利」，独立模块 `frontend/js/plugins/overnight_arbitrage.js`
> - 原 `backend/services/overnight_arbitrage_service.py` 与 `backend/api/router_overnight_arbitrage.py` 已删除
> - CLI `--run-overnight-arbitrage` 与 GitHub Actions workflow 保持向后兼容
>
> 本文档以下内容保留作为业务设计的历史参考。

> **给自动化执行者：**按测试先行执行，每个任务完成后运行对应验证命令。该策略必须独立于现有回撤推荐，不得把运行数据写入 `main`。

**目标：**新增一条独立的尾盘隔夜套利任务，在交易日 14:43 左右输出当日可执行买入候选，并在次日开盘窗口仅用于复盘校准。

**架构：**新增 `overnight_arbitrage_service` 作为独立策略服务，复用现有东方财富/新浪行情源和报告缓存机制。前端在「手动筛选」页增加独立 Tab，展示「今日 14:43 决策」而不是混入原回撤推荐。

**技术栈：**Python FastAPI、SQLite 周边服务、原生 HTML/CSS/JS、GitHub Actions/Render Cron。

---

## 文件职责（**已迁移路径，仅作历史参考**）

- ~~`backend/services/overnight_arbitrage_service.py`~~ → 现位于 `backend/plugins/overnight_arbitrage/service.py`：尾盘隔夜套利候选过滤、评分、报告写入。
- ~~`backend/api/router_overnight_arbitrage.py`~~ → 现位于 `backend/plugins/overnight_arbitrage/router.py`：手动触发和读取最新套利决策。
- `backend/main.py`：通过 try-except 注入插件 router；CLI `--run-overnight-arbitrage` 入口转发到 `backend.plugins.overnight_arbitrage.run_cli`。
- `frontend/index.html`、`frontend/js/plugins/overnight_arbitrage.js`：顶层菜单"隔夜套利"独立 page，已不在「手动筛选」Tab 下。
- `docs/index.html`、`docs/js/app.js`、`docs/js/plugins/overnight_arbitrage.js`：同步静态页面镜像。
- `.github/workflows/overnight-arbitrage.yml`、`render.yaml`：交易日 14:43 定时任务，CLI 入口名不变保持兼容。
- `scripts/commit_screening_data.sh`：继续把 `reports/` 下运行结果推送到 `data-snapshots`，不推送到 `main`。
- ~~`tests/test_overnight_arbitrage_service.py`~~ → 现位于 `backend/plugins/overnight_arbitrage/tests/test_service.py`、`test_pipeline.py`、`test_notify.py`：覆盖策略评分、API 后台任务和定时任务配置。

## 执行步骤（**历史步骤，保留作为业务逻辑参考**）

1. 先写 `tests/test_overnight_arbitrage_service.py`，验证创业板/ST/低流动性排除、14:43 决策字段、Top 候选排序、5 分钟 K 线增强不阻断主链路。
2. 跑新测试，确认因缺少服务模块失败。
3. 实现 ~~`backend/services/overnight_arbitrage_service.py`~~ → 现在的 `backend/plugins/overnight_arbitrage/service.py`，只输出精简决策 JSON，不写 `data/france.md`。
4. 新增 API 路由，手动触发后台任务并读取 `reports/overnight_arbitrage_latest.json`。
5. 修改 `backend/main.py`，新增独立 CLI 入口。
6. 前端「手动筛选」页增加 `涨停回撤筛选 / 尾盘隔夜套利` Tab，套利 Tab 展示可买、观察、放弃三类结果和数据源状态（**注：已重构为顶层菜单**）。
7. 新增 GitHub Actions/Render Cron，在北京时间交易日 14:43 运行 `python -m backend.main --run-overnight-arbitrage`，数据仍由快照脚本推送到 `data-snapshots`。
8. 跑完整测试、编译、diff 检查，并给出验收报告。

## 验收点

- 原 `reports/latest.json` 和回撤推荐接口不被新策略覆盖。
- 新策略结果写入 `reports/overnight_arbitrage_latest.json`。
- 14:43 任务输出当日买入决策，次日开盘仅作为复盘字段，不阻塞当日推荐。
- 创业板、ST、停牌、低成交额、低换手标的被排除。
- GitHub Actions 不向 `main` 推送运行数据。
