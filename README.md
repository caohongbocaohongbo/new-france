# new-france 尾盘涨停选股系统

## 项目定位

new-france 是 A 股尾盘涨停股监控与多因子推荐系统。前端用于查看监控列表、最新推荐、手动筛选与运行状态；后端负责采集东方财富/新浪/可选 Tushare 数据、维护 `data/france.md` 监控列表、执行多因子评分并输出 `reports/latest.json`。

## 本地运行

```bash
cd /Users/fangcang/new-france
pip install -r requirements.txt
python -m backend.main --serve
```

后端默认监听 `http://localhost:8000`，前端可直接访问 `http://localhost:8000/`。若只启动静态前端，也可以运行：

```bash
python -m http.server 5173 --directory frontend
```

静态前端在 localhost 环境会请求 `http://localhost:8000/api/v1`，因此仍需要后端同时运行。

## 前端页面

- Dashboard 总览：读取 `/api/v1/watchlist/stats` 和 `/api/v1/screening/latest`，展示监控数、今日新增、推荐数、上证指数当前点位/涨跌幅与最近推荐。
- 监控列表：读取 `/api/v1/watchlist`，支持搜索、状态筛选、排序、分页、导出当前页 CSV；分页固定在列表面板底部，表格区域内部滚动。列表列宽按可读性修正，不照抄 Figma 原稿中的错位。
- 推荐结果：读取 `/api/v1/screening/latest`，并按股票补查 `/api/v1/watchlist/{code}/detail` 与 `/api/v1/watchlist?search=`；推荐卡片展示审核状态、因子得分、关注日至今涨停板次数、真实 K 线趋势图，并可点击顶部统计卡片按 STRONG BUY / BUY / WATCH 筛选。
- 股票详情弹窗：读取 `/api/v1/watchlist/{code}/detail`，展示价格走势、成交量、均线、每日涨跌幅、回撤分布，以及换手率、量比、PE、封板时间、涨停板时间等补充图表。补充图表只使用后端真实返回字段，缺字段时显示空态。
- 手动筛选：调用 `/api/v1/screening/run`，随后轮询 `/api/v1/screening/latest`；未显式传 query 参数时使用后端运行时配置。
- 策略配置：读取 `/api/v1/config/strategy`，保存调用 `PUT /api/v1/config/strategy`。策略参数、因子权重、邮件收件账号、SMTP 主机/端口和涨停列表排序写入 SQLite `system_configs` 表，并同步 `config/strategy_params.py`。手动触发和定时任务后续会优先使用配置表中的值。

## 运行时配置

策略配置页不再使用浏览器本地暂存作为业务依据。配置保存后的生效顺序为：

1. `system_configs` 表中的 `strategy_runtime`。
2. `config/strategy_params.py` 中的默认值。
3. SMTP 密码等敏感字段仍只从环境变量读取，不返回前端、不写入数据库。

配置项包括：

- 策略参数：回撤、量比、换手率、流通市值、PE、监控周期、最晚封板时间、炸板次数上限、周期内涨停频率上限。
- 因子权重：回撤幅度、量能趋势、均线多头、强势确认、尾盘买点、流通市值、量比、换手率、市盈率、涨停质量，总和必须为 100%。
- 通知配置：发件邮箱、收件邮箱、SMTP 主机、SMTP 端口。
- 涨停列表排序：排序字段和升降序。

前端设置页会展示“上次保存值 / 当前编辑值”，便于保存前比对。保存成功后，筛选候选过滤、评分权重、邮件收件账号、监控周期状态判断、涨停频率统计都会优先读取后端配置。

## 旁路数据源接入

新增数据源默认旁路运行，不直接进入每日邮件和 Dashboard。每日流程会刷新旁路源并写入 `data/source_health.json`，字段包括来源、来源链接、采集时间、记录条数、错误信息、连续成功次数和稳定阈值。

生产展示开关统一放在 `config/optional_sources.json`：

- `surfaces.email` 控制是否进入每日邮件。
- `surfaces.dashboard` 控制是否进入 Dashboard 的旁路数据源区块。
- `required_successes` 和 `max_age_hours` 控制稳定判断。

稳定判断通过后，`.github/workflows/optional-source-promotion.yml` 会读取 `data-snapshots` 分支上的 `data/source_health.json`。如果某个旁路源连续成功、数据条数正常且尚未接入邮件/Dashboard，工作流会自动创建审核 PR，修改 `config/optional_sources.json` 并附带 `reports/optional-source-promotion.md` 证据。该 PR 需要人工确认合并，不自动进入生产展示。

可通过环境变量临时覆盖：

- `OPTIONAL_SOURCE_NATIONAL_TEAM_EMAIL=1`：临时允许国家队动向进入邮件。
- `OPTIONAL_SOURCE_NATIONAL_TEAM_DASHBOARD=1`：临时允许国家队动向进入 Dashboard。
- `OPTIONAL_SOURCE_NATIONAL_TEAM_PROMOTED=1`：临时视为已稳定。

## 生成数据快照

GitHub Actions 的 `Daily Stock Screening` 不再把 `data/france.md`、`reports/`、`data/new_france.db` 等生成物提交到 `main`。定时任务运行后会调用 `scripts/commit_screening_data.sh`，把生成物提交到 `data-snapshots` 分支，并写入 `data/snapshot_manifest.json` 记录来源分支、来源提交和生成时间。

工作流包含三层保护：

- `concurrency` 防止同一分支的筛选任务重叠写入。
- 旧提交检测：如果手动重跑的 workflow SHA 已落后 `origin/main`，会跳过筛选和数据提交。
- 推送重试：提交 `data-snapshots` 前会 fetch/rebase，最多重试 3 次。

## 视觉还原口径

当前前端按 Figma `france` 文件的 5 个业务画板优化：Dashboard 总览、监控列表、推荐结果、股票详情弹窗、手动筛选。整体采用浅灰左栏、白色工作区、8px 卡片、4px 表单控件、紫黑主按钮和百度财经式浅色图表。除监控列表原稿中存在的列错位不照抄外，其余布局密度、卡片层级、导航样式和图表风格按设计稿实现。

## 数据真实性口径

前端不渲染本地假数据。页面数据来自后端接口：

- 监控列表基础数据来自 `data/france.md`，展示页请求后端时会补充东方财富实时行情。
- 推荐结果主体来自 `reports/latest.json`，该文件由手动筛选或定时任务生成；其中包含上证指数真实点位快照 `index_value`、`index_gain`、`index_snapshot`。关注日、关注至今涨停板次数来自 `data/france.md` 经 `/api/v1/watchlist` 暴露的 `added_date`、`zt_count`、`follow_limit_up_count` 字段。
- 推荐卡片趋势图只使用接口返回的 `price_history` 或 `/api/v1/watchlist/{code}/detail` 的真实历史 K 线数据。两个接口都没有价格历史时，前端显示空状态，不用本地样例数据补线。
- 股票详情补充指标中，换手率、量比、PE 来自后端 Tushare 日频基础指标扩展；未配置 `TUSHARE_TOKEN` 或数据源不可用时前端显示空态，不用前端模拟线段。
- 旁路数据源的邮件和 Dashboard 展示由 `config/optional_sources.json` 控制；未打开前仅记录健康状态和独立 API，不进入生产展示。

需要注意：后端在实时行情缺失时会使用涨停池或历史 K 线作为降级价格来源，属于后端真实数据源降级，不是前端假数据；但严格审计“实时性”时，需要在接口中继续暴露价格来源字段。

## 发布目录同步

`docs/` 是 GitHub Pages/静态发布镜像。每次修改 `frontend/index.html`、`frontend/css/styles.css`、`frontend/js/app.js` 后，需要同步到：

- `docs/index.html`
- `docs/css/styles.css`
- `docs/js/app.js`

本次前端体验优化已同步上述发布目录。
