# Codex 执行任务：数据后台层改造（阶段 0 + 阶段 1）

> 一次性执行指令。**严格按范围执行**，不要顺手重构无关代码。
> 项目根：`/Users/fangcang/new-france`，所有路径相对该根。
> 语言：新增注释/文档用**中文**；**禁止 emoji**。
> 目标模块：新增 `backend/services/data_backend/`；只读接入 `backend/api/router_system.py`。

---

## 0. 背景与事实（先读，避免做错方向）

目标不是"再加一个因子"，而是把**数据获取 / 状态判断 / 结果消费**拆开，减少重复查询、重复排障、重复汇报。

### 0.1 现状（已核实，勿推翻）
- 主力资金插件已成熟实现"快照 + 缓存 + 熔断 + 远程回退"：
  `backend/plugins/principal_capital/sources/multi_source.py`（`_HEALTH` 熔断 + `_read_cache`/`_write_cache` + `data-snapshots` 回退）。
- 已有一套 health registry 雏形：`backend/services/optional_source_health.py`
  （类含 `record_result` / `source_status` / `errors` / `record_count` / `max_age_hours`）。
- 定时任务历史已走"本地读不到回退 `data-snapshots`"：`backend/services/task_history.py:read_task_history_resilient`。
- 生成物快照分支机制：`scripts/commit_screening_data.sh` / `scripts/restore_screening_data.sh`。
- 主链路仍是"请求来了现拉数据"：`backend/services/screening_service.py`、`backend/agents/layer1_data_collector/agent.py`
  （`zt_pool` / `quotes` / `historical_kline` / `index_snapshot` / `principal_capital` 均有同步直连）。

### 0.2 部署硬约束（决定方案边界，务必遵守）
`render.yaml`：**1 个 web + 2 个 cron，无 background worker，无持久磁盘。**
1. **web 进程无常驻后台**，免费层还会休眠 → **禁止**在 web 里起"每 30-60s 的常驻 poller / `asyncio.create_task` 死循环"。本次不做常驻轮询。
2. **web 与 cron 是独立实例、文件系统隔离** → 一个进程写的本地快照，另一个进程读不到。
3. **本地文件系统 / SQLite 都是 ephemeral**（redeploy、实例切换即丢），**不能当跨实例快照存储**。
4. **跨实例真源是 `data-snapshots` 分支**，写入方式为"每日一次 git commit、历史累积"（见 `commit_screening_data.sh`），**不能当高频写通道**。

### 0.3 本次范围（阶段 0 + 阶段 1）
- **阶段 0（零风险，只读聚合）**：新增统一 registry + read_model，暴露 `/api/system/data-assets` 数据资产面板。**完全不改采集逻辑**。
- **阶段 1（低风险，统一快照读）**：为 `zt_pool` / `quotes` / `index_snapshot` 增加统一快照读写封装（进程内热缓存 TTL + `data-snapshots` 冷回退），**但仍允许同步补拉**，不切断 `screening_service` 现有直连。

### 0.4 范围外（不要做）
- 不做常驻 poller / background worker / 消息队列（阶段 2，需先定部署形态）。
- 不把 `screening_service` 改成"唯一读入口 / 禁止直连"（阶段 2）。
- 不引入新的持久化存储（不新建 SQLite 表、不加持久磁盘）。
- 不重写 `principal_capital._HEALTH` 或 `optional_source_health` 的既有行为——只**抽象上提复用**。
- 不改前端页面结构（仅可在需要时新增只读接口，前端接线由后续单独任务处理）。
- 不改 `historical_kline` 的采集频率（日级/按需，保持现状）。

---

## 1. 统一元信息模型 —— `backend/services/data_backend/registry.py`

新增统一 `DataAsset` 元信息 schema。**不要新造第三套 health**：本模块提供统一 schema + 聚合读，数据来源是"读取已有的 `source_health.json`、`principal_capital` 缓存/health、`reports/latest.json` 等现有产物"，做**只读归一**。

单个资产元信息字段（统一 schema）：
```python
{
  "asset": "zt_pool",            # 资产键：zt_pool | quotes | index_snapshot | principal_capital | historical_kline
  "source": "eastmoney",         # 当前生效源；降级时为 cache / data-snapshots / none
  "fetched_at": "2026-07-06T15:10:00+08:00",
  "age_seconds": 123,            # 相对北京时间 now
  "record_count": 87,
  "status": "fresh",             # fresh | stale | degraded | unavailable
  "trading_session": "closed",   # open | closed | pre | post —— 见第 4 节，非交易时段 stale 不算异常
  "degraded_from": "eastmoney",  # 若走了降级，记录原本应用的源；否则 None
  "error": null,                 # 最近一次错误摘要
}
```

要求：
- `age_seconds` 与所有时间戳统一用**北京时间**（复用 `BEIJING_TZ = timezone(timedelta(hours=8))` 写法，与 `multi_source.py` 一致）。
- 归一函数按资产分别实现（`_read_principal_capital_asset()` 读 `principal_capital` 的缓存 JSON + health；`_read_optional_source_asset()` 复用 `optional_source_health.py`；等等）。**读不到不报错，返回 `status="unavailable"`**。
- 提供 `get_all_assets() -> list[DataAsset]` 聚合入口。

---

## 2. 只读读模型 —— `backend/services/data_backend/read_model.py`

- `get_data_assets_overview() -> dict`：调用 `registry.get_all_assets()`，附加整体摘要
  `{"generated_at":..., "trading_session":..., "assets":[...], "summary":{"fresh":n,"stale":n,"degraded":n,"unavailable":n}}`。
- 纯读、无副作用、任何子项失败都降级为该资产 `unavailable`，**不得抛异常**（面板必须永远能渲染）。

---

## 3. 数据资产面板接口 —— `backend/api/router_system.py`

新增只读端点：
```
GET /api/system/data-assets  ->  read_model.get_data_assets_overview()
```
- 复用该 router 现有依赖/前缀风格；不改动其他端点。
- 输出经过 NaN/Infinity 清洗（复用 `backend/main.py` 已有的清洗思路，保证可被 FastAPI 直接序列化）。

**阶段 0 到此为止即可交付并验收**（第 5 节验收 A）。

---

## 4. 交易时段判定 —— `backend/services/data_backend/trading_session.py`

- `current_trading_session(now=None) -> str`：返回 `open | closed | pre | post`。
  A 股交易时段（北京时间）：09:30-11:30、13:00-15:00 为 `open`；周末/非交易时段为 `closed`。节假日日历可暂不精确（先按周一至周五 + 时段判定，注释标注 TODO：接入交易日历）。
- 用途：`quotes`/`index_snapshot` 在 `trading_session != "open"` 时，即便 `age_seconds` 很大也应标 `status="stale"` 而**非** `degraded`（收盘定格是正常态，面板不应报红）。

---

## 5. 阶段 1：统一快照读写封装 —— `backend/services/data_backend/snapshots.py`

为 `zt_pool` / `quotes` / `index_snapshot` 提供**统一读取封装**，实现两层：
1. **热层**：进程内内存缓存 + 本地文件缓存（TTL 由各资产配置，见下）。
2. **冷层**：本地缺失/过期时，回退 `data-snapshots` 分支 GitHub raw（复用 `task_history.py:_fetch_snapshot_json` 的写法与 `SNAPSHOT_RAW_BASE` 约定）。

统一降级顺序（对每个资产）：
```
读本地新鲜快照(未过期) -> [允许时]同步补拉一次 -> 读本地过期快照 -> 读 data-snapshots 冷快照 -> unavailable
```
- **保留同步补拉**：本阶段不禁止直连。补拉调用现有 source 函数（`fetch_zt_pool` / `fetch_stock_quotes` / `fetch_index_snapshot`），不重写它们。
- 每次读取都通过 registry 记录元信息（source / fetched_at / status / degraded_from / error）。
- TTL 建议（写进配置常量，允许 env 覆盖）：`zt_pool` 120s、`quotes` 45s、`index_snapshot` 45s。交易时段外 TTL 视为"当日有效"，不触发补拉。

**接入方式（低风险）**：`snapshots.py` 提供 `read_zt_pool()` / `read_quotes(codes)` / `read_index_snapshot()`，
**先只在 `read_model` / 面板侧使用以验证正确性**；是否切换 `screening_service` 调用点，留待人工审查后决定（本任务可在 `screening_service` 对应位置加 TODO 注释指明切换点，但**不改调用**）。

---

## 6. 回归安全网（改造前必须先补）—— `backend/services/data_backend/tests/`

`screening_service` 目前无测试。为本次新增模块补最小单测：
- `test_registry.py`：给定伪造的 `source_health.json` / 缓存 JSON，`get_all_assets()` 返回字段完整、缺失源标 `unavailable`。
- `test_trading_session.py`：09:30/12:00/14:00/周六 各时点判定正确。
- `test_snapshots.py`：模拟"本地新鲜 / 本地过期+补拉成功 / 全失败回退冷快照 / 全空 unavailable"四条降级路径。
- 用 `tmp_path` + monkeypatch 隔离文件与网络，**不得真实发 HTTP**（参考 `backend/plugins/principal_capital/tests/test_service.py` 的 patch 写法）。

---

## 7. 验收标准

**验收 A（阶段 0）**
1. `GET /api/system/data-assets` 返回全部资产元信息，任一源不可用时该资产 `status="unavailable"` 而非接口 500。
2. 面板能在"东财全挂 / 缓存过期 / 走了 data-snapshots"三种情况下正确显示 `source` 与 `degraded_from`。
3. 未改动任何采集逻辑，`screening_service` 行为与改造前一致。

**验收 B（阶段 1）**
4. `snapshots.read_*` 四条降级路径单测通过。
5. 交易时段外 `quotes`/`index_snapshot` 显示 `stale` 而非 `degraded`。
6. `screening_service` 调用点已加 TODO 注释标出未来切换点，但**未改调用**，主链路无回归。

**通用**
7. `pytest backend/services/data_backend/tests/ -q` 全绿。
8. `python -m backend.main --serve` 能正常启动（无 import 错误）。
9. 无新增持久化存储、无常驻后台线程、无对前端页面结构的改动。

---

## 8. 交付物清单
```
backend/services/data_backend/__init__.py
backend/services/data_backend/registry.py
backend/services/data_backend/read_model.py
backend/services/data_backend/trading_session.py
backend/services/data_backend/snapshots.py
backend/services/data_backend/tests/__init__.py
backend/services/data_backend/tests/test_registry.py
backend/services/data_backend/tests/test_trading_session.py
backend/services/data_backend/tests/test_snapshots.py
backend/api/router_system.py                 # 仅新增 /api/system/data-assets
backend/services/screening_service.py        # 仅新增 TODO 注释，不改逻辑
```

完成后请在回复中列出：改动文件、验收项 1-9 的实际结果、以及阶段 2（解耦采集/常驻轮询/唯一读入口）留待人工决策的部署形态问题（付费 Worker vs 加密 cron）。
