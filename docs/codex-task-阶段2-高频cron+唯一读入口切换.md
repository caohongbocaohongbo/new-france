# 阶段 2：高频 cron + 唯一读入口切换

> 一次性执行指令。严格按范围执行，不要顺手重构无关代码。
> 项目根：`/Users/fangcang/new-france`
> 文档语言：中文。

## 目标

在不引入常驻 worker 的前提下，基于高频 cron 完成三件事：

1. 把 `zt_pool`、`quotes`、`index_snapshot` 的快照刷新独立成可单独运行的 CLI 入口。
2. 把这些快照同步写入 `reports/data_backend/`，交给 `data-snapshots` 分支跨实例持久化。
3. 让 `screening_service` 优先从 `data_backend.snapshots` 读取 `zt_pool / quotes / index_snapshot`，不再在主链路里直接调用源站函数。

## 范围

- `backend/services/data_backend/snapshots.py`
- `backend/services/screening_service.py`
- `backend/main.py`
- `render.yaml`
- `scripts/restore_screening_data.sh`
- 阶段 2 对应单测

## 非目标

- 不改 `historical_kline` 的读取模式
- 不改 `principal_capital` 插件
- 不改前端页面
- 不引入新数据库表
- 不引入常驻后台线程

## 实施原则

- 仍沿用高频 cron，不做常驻 poller。
- 快照真源优先级：
  1. 本地 `data/data_backend/*.json`
  2. 本地 `reports/data_backend/*.json`
  3. `data-snapshots` 分支 raw 快照
- `quotes` 快照必须记录本次覆盖的股票代码集合，读取时只有在“请求代码是快照代码子集”时才允许命中本地快照。
- 交易时段外允许读取旧快照并标记为 `stale`，不视为异常。

## 交付物

- `python -m backend.main --refresh-data-assets`
- Render 新增高频 cron
- `screening_service` 中 `zt_pool / quotes / index_snapshot` 切到统一快照读入口
- `reports/data_backend/*.json` 可被快照分支持久化
- 回归测试覆盖刷新与切换行为
