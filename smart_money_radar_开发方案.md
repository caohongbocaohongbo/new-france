# smart_money_radar 盘中雷达插件开发方案（聪明钱预启动雷达）

> 交付 codex 的施工文档。项目 `/Users/fangcang/new-france`（Python FastAPI + SQLite）。
> 新增自包含插件 `backend/plugins/smart_money_radar/`，基于 pytdx 采集观察池实时数据，
> 计算 12 个指标 → 合成双评分 → 四阶段状态机报警 → 发邮件。**严格按本文档，勿自由发挥。**

## 0. 铁律（违反即返工）

- **零污染**：只新增插件目录 + 改 `backend/main.py` 三处。**不修改 principal_capital 及任何其它既有文件**，完工用 `git diff` 自证。
- **复用不重造**：
  - 邮件：`from backend.plugins.principal_capital.notifier import send_email`（签名 `send_email(subject, text_content, html_content, smtp_config=None, timeout=15) -> (bool, err)`，自带 SMTP 读 SMTP_* env）。
  - 市场过滤：`from backend.plugins.principal_capital.service import _stock_code, _is_main_board, _is_excluded_market, _is_star_market, _is_st_name`。
  - 去重范式：抄 principal_capital.service 的 `load_notified_map/save_notified_map/should_notify/cleanup_old_notified`。
  - 交易时段：`from backend.api.router_system import trading_session_status`（返回 `is_trading_hours`/`session`∈{trading,pre_open,midday_break,closed,non_trading_day}）。**勿自写时段逻辑**。
- **时区**：`BEIJING_TZ = timezone(timedelta(hours=8))`，全程 `datetime.now(BEIJING_TZ)`，**禁裸 datetime.now()**。
- **健壮性**：所有 pytdx/socket/SMTP 调用带 timeout；每只票采集异常隔离（`return_exceptions`）不崩全局；除法一律防 0。
- `requirements.txt` 新增 `pytdx>=1.72`（可另建 `requirements-local.txt` 让 Render 不装，二选一，注释说明）。

## 1. 已验证的 pytdx 事实（两次本地实测 2026-08-18，直接采信，勿再改接口用法）

- 服务器 `115.238.90.165:7709` 握手~164ms。market：`6开头→1(沪)`，`0开头→0(深)`。
- `get_security_quotes([(market,code)])` → 五档：`bid1~5/bid_vol1~5/ask1~5/ask_vol1~5/price/last_close/amount/vol/b_vol(累计主买)/s_vol(累计主卖)/servertime`。
- `get_transaction_data(market,code,0,count)` → 逐笔：`time`（**仅分钟级**如"13:58"）/`price`/`vol`/`buyorsell`(0主买/1主卖/2中性)/`num`（当日单调递增序号，用于增量去重）。
- `get_security_bars(category,market,code,0,n)` → 分钟K：`datetime/open/close/high/low/vol/amount`。**周期编号定死：1分钟=8，5分钟=0，15分钟=1，日=9**。
- `get_finance_info(market,code)` → `liutongguben`(流通股本，**单位=股**)、`zongguben`(总股本)。`name` 返回 None（名称从观察池取）。**流通市值 = price × liutongguben**。
- **数据陷阱（必须防御）**：收盘 14:59 集合竞价分钟bar 的 vol/amount 为非规格化浮点（约 `5.877e-39`≈0）。凡用 vol/amount 计算，`vol < 1 视为 0 并跳过该 bar`。
- 硬约束：Level-1 无委托队列/无撤单；逐笔 time 仅分钟级。国外 IP 连不上（采集端必须国内/本地）。

## 2. 目录结构

```
backend/plugins/smart_money_radar/
├── __init__.py       # register_router() + run_radar_once_cli(args) + run_radar_daemon_cli(args) + run_verify_tdx_cli(args)
├── config.py         # CONFIG(env 覆盖) + 路径常量 + RADAR_SOURCE
├── router.py         # GET /latest, GET /pool, POST /trigger(单轮调试)
├── service.py        # 编排：加载观察池→采集一轮→算12指标→双评分→状态机→发邮件→写 latest.json
├── scheduler.py      # 常驻循环(盘中轮询/非交易休眠)，--run-radar-daemon 入口 + 掉线看门狗
├── notifier.py       # 薄封装 build_and_send(hits,source)，复用 principal_capital.send_email
├── indicators.py     # 纯函数：全 12 指标
├── scoring.py        # Smart Money Score + Launch Score
├── store.py          # 内存滑动窗口: quotes deque + tx_minutes 桶 + last_num + fund_series + minute_bar 缓存 + stage 状态机
├── sources/{__init__.py, tdx_source.py}   # TdxPool 连接/故障切换/fetch_stock(五档+逐笔)/fetch_bars/fetch_finance
└── tests/{__init__.py, conftest.py, test_indicators.py, test_scoring.py, test_store.py, test_service.py}
```

## 3. 采集器 sources/tdx_source.py

pytdx 同步阻塞库 → 采集放线程池，异步侧 `asyncio.to_thread` + `asyncio.Semaphore`。V1 单连接顺序轮询（fetch_concurrency=1）。

```python
class TdxUnavailable(Exception): ...

class TdxPool:
    def __init__(self, servers, connect_timeout, socket_timeout): ...
    def connect(self) -> bool          # 按 servers 顺序尝试，长连接复用
    def ensure_alive(self)             # 每轮前 get_security_count(0) 探活，失败切下一服务器
    def fetch_stock(self, market:int, code:str) -> dict   # {quote, txs, servertime, fetched_at}
    def fetch_bars(self, market, code, category, n) -> list
    def fetch_finance(self, market, code) -> dict
    def disconnect(self)
# 熔断字段抄 principal_capital：failure_threshold / circuit_break_minutes；
# 连续 N 次全失败抛 TdxUnavailable，service 记录跳过该轮不崩。

def market_of(code:str)->int:  # 6→1, else→0

async def poll_pool_once(pool, cfg) -> list:  # Semaphore + to_thread(fetch_stock)，return_exceptions=True
```

## 4. 全 12 指标 indicators.py（纯函数，防 0 除）

**资金类**（读观察池 JSON 字段，principal_capital 已算好）：
- 指标3 大单净流入 = `main_net_inflow`（或 super_net+big_net）
- 指标4 大单买入占比 = `main_inflow_ratio`
- 指标5 连续净流入时间 = fund_series 中 main_net_inflow 单调递增的分钟数
- 指标1/2 大单主动买/卖额：仅新浪源有 r1_in/r1_out（当日累计），无则置 None 不参与

**价格类**（pytdx）：
- 指标6 = 1/5/15分钟涨跌幅：`get_security_bars(8/0/1)`，`(last.close - bar_n_ago.close)/bar_n_ago.close`
- 指标7 = VWAP偏离：当日均价 `amount/(vol*100)`，`(price-vwap)/vwap`
- 指标8 = 近期高低点：分钟bar `max(high)/min(low)`，距高点% `(hi-price)/hi`
- 指标9 = **Price Impact（核心猎物）**：`change_pct / (main_net_inflow / (price*liutongguben))`；流通股本每票每日缓存

**成交盘口类**：
- 指标10 = 量比：当日累计 vol vs 过去 N 日同时段均量
- 指标11 = 买卖盘强弱：`bid_amt=Σbid_i*bid_vol_i; ask_amt=Σask_i*ask_vol_i; strength_amt=100*bid_amt/(bid_amt+ask_amt)`；辅助 `active_buy_ratio=b_vol/(b_vol+s_vol)`；用最近 strength_smooth_frames(3) 帧均值抗抖
- 指标12 = 卖压衰减（近似，双路）：
  - 路A(秒级)：最近 decay_window_s(90) 的 `(ts, ask_vol_sum)` 最小二乘斜率 slope_ask，`decay_ask=-slope_ask*window/mean(ask_vol_sum)`
  - 路B(分钟)：去重逐笔按分钟桶 `sell_amt=Σprice*vol*100(buyorsell==1)`，取最近 decay_minutes(3) 个**已收口**整分钟桶斜率，`decay_sell=-slope/mean(sell_amt)`；**当前未完成分钟桶不参与**
  - `decay_score=0.5*clip(decay_ask,0,1)+0.5*clip(decay_sell,0,1)`

## 5. 双评分 scoring.py（权重全进 config）

```
SmartMoneyScore(0-100)=Σ 归一化因子×权重：
  大单主买占比20·大单持续性15·资金价格背离(低PriceImpact)20·成交量异常10·
  卖压衰减10·买卖盘口10·VWAP位置5·短线结构5·板块资金5(V1置0)

LaunchScore(0-100)：卖压衰减速度·买盘增强趋势·距短周期高点·量能突放·突破VWAP
# 每指标先 clip 到经验区间归一化到 0-1，再加权求和×100
```

SmartMoney 高 + Launch 低 = 潜伏/吸筹；两者都高 = 预启动。

## 6. 四阶段状态机 + 失败报警（service.py，状态存 data/smart_money_radar_state_{date}.json）

```
观察池 →[潜伏]→[吸筹确认]→[启动前夕]→[启动]  ；启动前夕后可→[启动失败]
```

| 阶段 | 触发（V1 阈值全 config） | 报警 |
|------|--------------------------|------|
| 🟡潜伏 | 买入占比>55 AND 净流入>门槛 AND 持续≥3轮 AND 涨幅<1.5% AND 量比>1.2 | 入池不报 |
| 🟠吸筹确认 | 买入占比>60 AND 净流入>P80 AND 持续≥5分钟 AND 涨幅<2% AND price>VWAP AND 卖压未增 | 报警一次 |
| 🚨启动前夕 | SmartMoney>80 AND Launch>80 AND PriceImpact异常低 AND 卖压快降 AND 买盘增强 AND 距高点<1% | 高优先级 |
| 🚀启动 | 启动前夕 + 突破20分钟高点 + 量能突放 + 主动买盘增强 | 报警 |
| ⚠️失败 | 启动前夕后：净流入转负 OR 跌破VWAP OR 卖压重暴增 | 报警"逻辑失效" |

- **只在首次进入/升级时报警**，不重复刷；措辞用"启动条件正在形成"，勿写"即将上涨"。
- 冷却去重抄 principal_capital：`should_notify(code,"radar",now,map,alert_cooldown_minutes(30))`。
- 每次报警写 history（时间/两评分/各指标/阶段），为未来"报警→收益"回测积累样本。

## 7. 观察池 & 邮件

- `load_watch_pool()`：读 `reports/principal_capital_latest.json` 的 `buy_candidates`+`sell_candidates`（item 有 code/name/main_inflow_ratio/total_amount），**off-hours 为空数组必须容忍跳过**；用复用的过滤函数再过滤一遍剔除 300/301/688/ST；截断 pool_max(40)；缓存 pool_refresh_min(10)。
- notifier：一轮多只命中**合并一封**；`subject=f"{tag} 盘中信号 {n}只 {HH:MM}"`，`tag=《本地雷达》if RADAR_SOURCE==local else 《云端雷达》`；正文按阶段分区（启动前夕/启动置顶红色）+ Top-N 排行榜表（排名/代码/SmartMoney/Launch/背离度/阶段）。

## 8. config.py（全 env 可覆盖，前缀 RADAR_）

- 数据源：tdx_servers(csv,默认 115.238.90.165:7709 等 3-5 个)/tdx_connect_timeout_s=3/tdx_socket_timeout_s=3/failure_threshold=5/circuit_break_minutes=8/tx_count=800/fetch_concurrency=1
- 观察池：pool_source_file=reports/principal_capital_latest.json/pool_keys=[buy_candidates,sell_candidates]/pool_max=40/pool_refresh_min=10/exclude_gem=True/exclude_star=True
- 窗口：poll_interval_s=4/decay_window_s=90/decay_minutes=3/strength_smooth_frames=3/bar_cache_ttl_s=45
- 阈值：strength_threshold=65/active_buy_threshold=0.55/decay_threshold=0.3/各阶段阈值/评分权重
- 报警：alert_cooldown_minutes=30/history_keep_days=7
- 来源：radar_source=env RADAR_SOURCE(默认 local)  落盘：enable_sqlite_dump=False
- 路径：LATEST_FILE=REPORT_DIR/smart_money_radar_latest.json；notified/state 文件 DATA_DIR 下加插件前缀。SMTP 复用同名 SMTP_* env。

## 9. scheduler.py 常驻

```
while True:
  sess = trading_session_status(now)
  trading      → 读池(缓存)→poll_pool_once→算/评分/状态机/报警→sleep(poll_interval_s)
  pre_open     → sleep 30(预热连接)
  midday_break → store.gc(); sleep 60
  closed/非交易 → store.reset(); sleep 到下个 09:25
```

- 掉线看门狗：采集连续失败超阈值 → 记录 + 发一次"雷达掉线"告警(带 source tag) 不退出。
- 交付 launchd plist 模板文本（KeepAlive/RunAtLoad/Nice 低优先级/日志→logs/radar.log），放 README，**不代装**。

## 10. main.py 三处改动（仿 overnight_arbitrage）

1. router 注入块加同款 try/except：`include_router(smart_money_radar.register_router(), prefix="/api/v1/smart-money-radar", tags=["盘中雷达"])`
2. argparse 加：`--run-radar-once` / `--run-radar-daemon` / `--verify-tdx`
3. dispatch 分发到对应 cli 函数

## 11. 测试（tests/，pytdx 全 mock，绝不联网）

- conftest：fake_quote()/fake_txs()(num 递增+buyorsell 混合)/fake_bars()/fake_finance()，monkeypatch TdxPool.fetch_*
- test_indicators：11(边界 ask=0/全买盘=100)、12(ask_vol 递减→slope<0、分钟桶递减、当前分钟桶排除)、9(PriceImpact 防0、集合竞价 vol≈0 被跳过)、6/7/8/10
- test_scoring：归一化边界、权重生效、SmartMoney 高 Launch 低场景
- test_store：num 去重(两轮重叠只吃新 num)、跨日 reset、maxlen 淘汰、minute_buckets 排除当前分钟、stage 迁移
- test_service：空池不报错无邮件、命中判定+冷却去重、dry_run 断言 send_email 未调用、混入 300/688 被剔除、失败报警从启动前夕触发
- 运行 `pytest backend/plugins/smart_money_radar/tests -q`

## 12. 分阶段交付（每阶段可独立验收）

- **Phase 0（已完成 2026-08-18）**：分钟线/财务接口已实测，指标 6/8/9/10 写实，周期编号定死，集合竞价陷阱已记录。
- **Phase 1（管道+盘口）**：pytdx 依赖→骨架+config→tdx_source(五档逐笔+--verify-tdx)→store(quotes+num 去重)→indicators 指标11+3+4→service(观察池+poll+指标11判警+去重+写 latest)→notifier→router→main 接入+--run-radar-once→tests→盘中手动验证一封邮件
- **Phase 2（价格+资金）**：接 get_security_bars/get_finance_info→指标6/7/8/9/10→store 加 fund_series+minute_bar 缓存(低频拉,股本每日一次)→防集合竞价陷阱
- **Phase 3（评分+卖压+状态机）**：指标12 路A+路B→scoring 双评分→四阶段状态机+失败报警→邮件分区+Top-N→tests 补
- **Phase 4（常驻）**：scheduler daemon+--run-radar-daemon+看门狗→launchd 模板→收盘 reset/节假日空转→(可选)SQLite 回放

## 完工自检

`git diff` 确认仅动 main.py + 新增插件目录；pytest 全绿且不联网；--verify-tdx 手动可跑；所有除法防 0；集合竞价 vol≈0 已处理；无裸 datetime.now()。

---

## 附：部署与既有系统的关系

- **Render（美国）那套完全不动**：principal_capital 扫描、尾盘筛选、Cron 原样跑。雷达通过 main.py 的 try/except 可选加载，Render 上永不启动 daemon（即使误触发，pytdx 在美国 IP 连不上，只会空转跳过）。
- **采集端只在本地电脑（国内 IP）跑**：`python -m backend.main --run-radar-daemon`。两套各读各的，邮件靠《本地雷达》/《云端雷达》tag 区分来源。
- **观察池获取**：本地读 principal_capital 报告；若本地无该文件，principal_capital 内置 `read_report_resilient()` 会从 data-snapshots 分支拉快照兜底。
- **资源占用**：网络 I/O 型进程，CPU≈0、内存几 MB（滑动窗口有上界，每日收盘 reset），不拖慢 Figma/codex。忙时可调大 poll_interval_s / 调小 pool_max / launchd 设低优先级 Nice。

## 附：待校准与已知限制

- 所有阈值（strength 65 / decay 0.3 / cooldown 30min / 各阶段）是经验初值，需盘中实盘校准；长期跑起来积累"报警→未来收益"样本后可回测自动调参。
- 指标12 是 Level-1 近似，看不到撤单/委托队列。严格版需 Wind L2 或券商 API（成本数量级上升）。
- 本地常驻依赖电脑开机；launchd 只能自愈崩溃，不能自愈关机/断网。以后若要 7×24 可迁国内轻量 VPS（改 RADAR_SOURCE=cloud + RADAR_TDX_SERVERS）。
