# 12 真实 Level-2 升级路径（QMT / PTrade） — 产品开发技术方案

## 1. 目标与定位

当前免费栈只能做“类 L2”近似。本方案给出真 Level-2 的接入路径，用于拿到：
- 十档 / 千档盘口；
- 逐笔委托、逐笔成交（毫秒时间戳）；
- 委托队列、撤单数据；
- 排队位置估算。

优先级：P2。等 07–11 稳定、且确认需要更细粒度后再上。

## 2. 产品方案

### 2.1 用户场景
- 盘口深度研究：十档委托分布与异动。
- 委托队列分析：封单排队位置、撤单率。
- 高频 / 实盘策略：需要真实逐笔与队列的算法交易。

### 2.2 页面模块（复用现有前端）
- 盘口深度：十档 / 千档展示。
- 逐笔明细：毫秒级逐笔 + 主动买卖。
- 队列分析：委托队列、撤单统计。

## 3. 技术方案

### 3.1 候选数据源对比

| 方案 | 能拿到 | 成本 / 门槛 | 适用 |
|---|---|---|---|
| QMT / miniQMT（迅投） | 逐笔委托、逐笔成交、十档、队列、撤单 | 券商开户 + 资金门槛，本地运行 | 高频 / 实盘自动交易 |
| PTrade（券商） | 类似 QMT，策略托管 | 券商申请 | 量化托管 |
| 同花顺 / 东财 L2 | 十档、逐笔、队列、大单统计 | 订阅费 + 授权限制 | 纯研究 |
| Tushare Pro | 资金流、龙虎榜、日频财务，无 L2 逐笔 | 积分 / 订阅 | 已作可选源 |

### 3.2 推荐架构
- QMT 只作为采集端，跑在本地国内网络。
- 新增 `backend/plugins/l2_feed/`，采集后落本地 Parquet / SQLite，再聚合快照。
- 复用现有 FastAPI + SQLite + 插件架构，不替换主系统。

### 3.3 与免费栈关系
- 免费栈继续跑：全市场资金流、涨停池、盘后筛选不变。
- L2 只覆盖重点观察池 / 自选股，避免海量存储。

## 4. 数据存储方案

```sql
CREATE TABLE l2_orderbook_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  code TEXT NOT NULL,
  levels_json TEXT NOT NULL
);
CREATE INDEX idx_l2_ob ON l2_orderbook_snapshot(code, ts);

CREATE TABLE l2_tick (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  code TEXT NOT NULL,
  price REAL,
  vol REAL,
  side INTEGER,
  order_id TEXT
);
CREATE INDEX idx_l2_tick ON l2_tick(code, ts);
```

- 原始 L2 数据量大，建议本地 Parquet 分区：`data/l2/{date}/{code}.parquet`。
- 云端只同步聚合快照，不同步原始 tick。

## 5. 数据读取方案

- `GET /api/v1/l2/orderbook/{code}`：读本地最新十档。
- `GET /api/v1/l2/ticks/{code}?start=&end=`：本地明细。
- 云端页面若需要，读聚合 JSON，避免 Render 直连本地。

## 6. 与现有系统集成

- `main.py` 可选加载 `l2_feed` router。
- 本地 daemon 与 `smart_money_radar` 并存，通过 `RADAR_SOURCE / L2_SOURCE` 区分。
- 邮件 tag 增加《本地L2》来源标识。

## 7. 测试与验收

- 采集端连通性测试：十档 / 逐笔 / 队列字段完整性。
- 存储吞吐测试：单日 tick 落 Parquet 无积压。
- 验收：本地页面可实时刷新十档与逐笔，数据字段与券商客户端一致。

## 8. 上线与运维

- 本地常驻，launchd / systemd 保活。
- 磁盘按日滚动清理，保留策略可配置。

## 9. 风险与限制

- QMT 有券商门槛与合规限制，实盘交易需额外风控。
- L2 数据量大，不能全市场存储，必须限定观察池。
- 数据授权可能禁止再分发，仅限个人研究使用。

## 10. 里程碑

1. M1：QMT / PTrade 账号与接口调研。
2. M2：L2 采集端 + 本地存储。
3. M3：十档 / 逐笔页面 + 队列分析。
