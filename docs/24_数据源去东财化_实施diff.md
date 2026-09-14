# 24 方案 · 分阶段实施 diff.md（§12.4 第1–4步核心）

> 2026-09-14 起，按 docs/24_数据源去东财化_主备倒置.md §12.4 施工顺序推进。
> 本文汇总目标执行以来的所有改动（不含 §11 首阶段既有改动）。

## 变更文件清单

| 文件 | 批次 | 内容 |
|---|---|---|
| backend/services/data_backend/snapshots.py | 第1/2步 | P0-1 实际覆盖 + 原子发布 + 批次一致性 |
| backend/agents/layer1_data_collector/sources/eastmoney_quote.py | 第1/2步 | 东财 legacy degraded + 候选精查入口 |
| backend/plugins/common.py | 第1/2/3步 | K线行数覆盖/覆盖元数据 + 旁路写库 hook |
| backend/plugins/overnight_arbitrage/service.py | 第1/2/4步 | 质量门控/完成复核/yahoo5m对齐/覆盖元数据/候选精查/zt事件能力标注 |
| backend/plugins/overnight_arbitrage/__init__.py | 第2步 | CLI 注入候选精查 |
| backend/services/data_backend/bars_store.py（新） | 第3步 | 日K持久化（upsert/补洞/复权失效/备份恢复） |
| backend/agents/layer1_data_collector/sources/zt_contract.py（新） | 第4步 | 涨停基础/增强拆分 + 规则边界 |
| tests/test_decision_quality_gates.py（新） | 第1/2/3/4步 | 决策质量 + 覆盖 + 精查 + zt 能力反例 |
| tests/test_bars_store.py（新） | 第3步 | 日K持久化单测 |
| tests/test_zt_contract.py（新） | 第4步 | 涨停规则/基础/增强单测 |

## 各步修复摘要

### 第1步 决策质量闭环
- snapshots：received/requested/missing 覆盖诚实化；全 degraded 不标 fresh。
- eastmoney_quote：东财 legacy source_time 未知 → degraded=True + missing_fields。
- common：K线缓存命中加 rows>=days，短窗不冒充长窗，负缓存保持。
- overnight：评分前 _quote_quality_block（degraded/is_stale/旧 source_time 不得 BUY）；完成时刻墙钟复核；yahoo5m 按 timestamp 整根对齐 + 新鲜度 + 同日连续窗口。

### 第2步 覆盖与取数一致性
- snapshots：唯一临时文件 + os.replace 原子写；双副本 snapshot_version；不一致以 canonical 为准标 _batch_inconsistent。
- common：K线返回附 kline_coverage（rows/first/last/short/reason）。
- overnight：_eastmoney_all_a_snapshot / _sina_all_a_snapshot 附 coverage 元数据，截断→degraded；候选精查贯通（fetch_tencent_quotes_for_codes + _refine_quotes_with_tencent + run CLI 注入）。

### 第3步 日K持久化
- 新增 bars_store：单表 bars_daily (code,trade_date,adjustment) 幂等 upsert、read、missing_dates（交易日历补洞）、delete_adjustment（复权失效）、backup（在线备份 API）、verify_backup。
- common：get_kline_cached 成功取数后 _persist_kline_best_effort（KLINE_STORE_WRITE_ENABLED 默认关闭，旁路不阻断）。

### 第4步 涨停拆分
- 新增 zt_contract：compute_limit_prices（规则版本 v2 主板ST=10%、Decimal ROUND_HALF_UP、未知返回 None）、classify_zt_basic（touched/at_limit/unknown）、enrich_zt_events、required_events_available。
- overnight：决策项/顶层暴露 zt_events_available + unavailable_required_fields（缺失显式标注，不静默当 0）。

## 回归结果

```
pytest tests/test_zt_contract.py tests/test_bars_store.py tests/test_decision_quality_gates.py        tests/test_source_contracts.py backend/services/data_backend/tests        backend/plugins/overnight_arbitrage/tests backend/plugins/tech_indicators/tests        backend/plugins/principal_capital/tests backend/plugins/trend_strength/tests        backend/plugins/pattern_scanner/tests backend/plugins/chip_scanner/tests -q
→ 170+ passed, 1 warning（既有 LibreSSL）
```

## 未完成（§12.4 后续）

- 第4步剩余：01/10/16 三个消费方接入 zt_contract（本轮仅完成 overnight 消费方）。
- 第5步：部署地影子运行（5 交易日覆盖/故障演练）——依赖真实部署环境，本机无法执行。

## 状态

所有改动仍未提交（叠加在 §11 首阶段未提交改动之上）；建议核验后统一 commit。

---

## 后续轮次补充（round 7-8）

### 第4步补充
- zt_contract.py 新增 resolve_zt_basic_from_quotes(quotes)：免费基础路径批量计算涨停基础状态，供 01/10/16 后续接入。
- tests/test_zt_contract.py 新增 test_resolve_zt_basic_from_quotes（7 passed）。

### 第5步补充（代码侧观测）
- 新增 backend/services/data_backend/shadow_run.py：SHADOW_RUN_ENABLED=1 时 record() 每轮观测（asset/source/status/coverage/source_time/age/error/deadline_met），summarize() 聚合 错误率/行情年龄P95/截止达成率/错误类型Top5。
- 新增 tests/test_shadow_run.py（record→summarize→reset，1 passed）。

### 最新回归
pytest shadow_run + zt_contract + bars_store + decision_quality_gates + source_contracts + data_backend + overnight_arbitrage + tech_indicators + principal_capital → 158 passed, 1 warning。

---

## 最终批次（授权后完成）

### 第4步 01/10 消费方迁移（显式 unavailable）
- emotion_cycle：涨停池源拉取失败（zt_pool=None）→ status="unavailable_required_fields" + unavailable_required_fields=["zt_events"]，区别于合法空池 no_data；不再把源不可用当"0 涨停"。
- zt_seal：同上，封单事件缺失时显式 unavailable，不静默当 0。
- 16 low_position_scanner 已有四级降级 + "不可用不剔除"语义，无需改动；免费基础路径 API（resolve_zt_basic_from_quotes）已就绪供后续接入。
- tests/test_decision_quality_gates.py 新增 test_emotion_unavailable_on_zt_fetch_failure / test_zt_seal_unavailable_on_zt_fetch_failure。

### 第5步 影子运行交付
- 代码侧：backend/services/data_backend/shadow_run.py（record/summarize）。
- 文档：docs/24_影子运行_观测报告模板与启动清单.md（启动清单 7 项 + 每日/5日报告模板 + 放行/回退条件 + 本机可复现命令）。
- 真实部署观测按用户指示延后（部署环境就绪后开启 SHADOW_RUN_ENABLED=1 连续 5 交易日）。

### 最终回归
pytest shadow_run + zt_contract + bars_store + decision_quality_gates + source_contracts + data_backend + overnight_arbitrage + tech_indicators + principal_capital + trend_strength + pattern_scanner + chip_scanner → 182 passed, 1 warning。
