# 筛选任务稳定化与旁路数据源接入规范

## 目标

降低后续功能提交对 `Daily Stock Screening` 定时任务的影响，同时让新增抓取数据源能先独立运行、积累稳定性证据，稳定后再通过审核 PR 接入每日邮件和 Dashboard。

## 范围

- GitHub Actions `Daily Stock Screening`
- 生成数据快照分支 `data-snapshots`
- 旁路数据源健康状态 `data/source_health.json`
- 旁路数据源展示开关 `config/optional_sources.json`
- 自动接入审核工作流 `Optional Source Promotion`

## Daily Screening 主链路

核心筛选链路仍然负责：

- 抓取当日涨停池。
- 更新 `data/france.md` 监控列表。
- 执行多因子筛选。
- 生成 `reports/latest.json` 与每日报告。
- 发送已启用的数据模块邮件。

工作流必须具备：

- `concurrency`：同一分支只允许一个筛选任务写数据。
- 旧提交检测：当前运行提交落后 `origin/main` 时，跳过依赖安装、筛选和数据提交。
- 生成物隔离：不得把运行生成物提交回 `main`。

## data-snapshots 分支

`data-snapshots` 是运行生成物快照分支，只保存每日筛选产生的数据，不承载业务代码。

允许提交：

- `data/france.md`
- `data/new_france.db`
- `data/source_health.json`
- `data/snapshot_manifest.json`
- `reports/`

`data/snapshot_manifest.json` 需要记录：

- 来源代码分支。
- 来源代码提交 SHA。
- 快照生成 UTC 时间。
- 目标数据分支。

推送策略：

- 先 fetch/rebase `data-snapshots`。
- 最多重试 3 次。
- 失败时让 workflow 失败，避免静默丢失数据。

## 旁路数据源

新增数据源默认旁路运行，不进入邮件和 Dashboard。旁路阶段允许：

- 独立采集并入库。
- 提供独立 API 页面。
- 记录健康状态。

旁路阶段不允许：

- 直接进入每日邮件。
- 直接进入 Dashboard 生产展示。
- 用不稳定数据影响筛选评分或核心报告判断。

## 健康状态格式

`data/source_health.json` 中每个数据源至少包含：

- `ok`：最近一次是否成功。
- `stable`：是否达到稳定阈值。
- `consecutive_successes`：连续成功次数。
- `required_successes`：接入阈值。
- `max_age_hours`：最近采集最大有效小时数。
- `source_status.source`：数据源名称。
- `source_status.source_url`：可追溯来源链接。
- `source_status.fetched_at`：最近采集时间。
- `source_status.errors`：错误列表。
- `data.record_count`：最近一次有效记录数。
- `error`：最近一次主要错误。

## 接入邮件和 Dashboard

生产展示开关只在 `config/optional_sources.json` 中配置：

- `surfaces.email`
- `surfaces.dashboard`

自动接入流程：

1. `Daily Stock Screening` 写入 `data-snapshots:data/source_health.json`。
2. `Optional Source Promotion` 读取健康状态。
3. 连续成功次数、记录条数、采集时间满足阈值时，自动创建 PR。
4. PR 修改 `config/optional_sources.json`，打开邮件和 Dashboard 开关。
5. PR 附带 `reports/optional-source-promotion.md` 作为证据。
6. 人工确认合并后才正式进入生产展示。

## 验收口径

- 旧 SHA workflow 不再运行主链路。
- 生成数据不再推回 `main`。
- `data-snapshots` 分支保存生成物。
- 旁路源失败不影响核心筛选链路。
- 未打开配置前，邮件和 Dashboard 不展示旁路源。
- 达到稳定阈值后只自动开 PR，不自动合并。
