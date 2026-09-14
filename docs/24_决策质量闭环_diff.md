# 24 方案 · 决策质量闭环 diff.md（§12.4 第 1 步 · 本轮改动汇总）

> 2026-09-14 后续批次。范围：§12.2 的 P0/P1 缺陷修复（决策质量闭环），不含你此前的 §11 首阶段改动。
> 回归：本轮相关套件 139 passed（1 条既有 LibreSSL 警告）；smart_money_radar 套件未纳入（既有 pytdx 挂起，与本次无关）。

## 变更清单

| # | 文件 | 修复缺陷（对应 §12.2） |
|---|---|---|
| 1 | backend/services/data_backend/snapshots.py | P0-1 快照实际覆盖 |
| 2 | backend/agents/layer1_data_collector/sources/eastmoney_quote.py | P1 东财 legacy 未知 source_time 冒充新鲜 |
| 3 | backend/plugins/common.py | P1 K线短窗冒充长窗 |
| 4 | backend/plugins/overnight_arbitrage/service.py | P0 评分前质量门控 + 完成时刻复核 + yahoo5m 时间对齐 |
| 5 | tests/test_decision_quality_gates.py（新增） | 固化 8 个反例 |

---

## DIFF-1 snapshots.py（P0-1 快照实际覆盖）

before：live 拉取成功后 payload 的 codes 直接写请求清单（requested），部分返回也声称完整覆盖且标 fresh。

after：
- 新增 _records_codes：从 records 提取实际收到的代码集合；
- payload 新增 requested_codes / received_codes / missing_codes / missing_reasons；codes = 实际收到集合（非请求清单）；
- _is_payload_covering_codes 优先按 received_codes 判断 → 缺失代码不再命中覆盖缓存；
- _meta 新增 requested_count / received_count / missing_count；
- live 返回非空但全部行 degraded（如 source_time 未知）→ meta status="degraded"，不标 fresh。

## DIFF-2 eastmoney_quote.py（P1 未知 source_time 降级）

before：东财 legacy 行 source_time=None 且无 degraded/missing_fields，可被当作未降级。

after：_parse_response 对每行计算 missing_fields（涨跌幅/成交量/成交额任一为 None），并强制 degraded=True（未知行情时间不得冒充新鲜）。

## DIFF-3 common.py（P1 K线短窗不冒充长窗）

before：get_kline_cached 命中条件只看 days——请求 130 根返回 30 根后，再请求 100 根会命中 30 根缓存。

after：
- 缓存项新增 rows（实际行数）与 failed 标记；
- 命中条件加 entry.rows >= days（实际行数不足请求窗口则补取）；
- failed 负缓存保持原语义（短 TTL 内返回 None 不重复轰炸）。

## DIFF-4 overnight_arbitrage/service.py（P0 决策质量门控）

4.1 评分前统一质量门控（新增 _quote_quality_block）：
- 拦截：degraded=True 或 is_stale=True；source_time 不可解析 / 非当日 / 超 900s；
- source_time 缺失的旧/合成行保持兼容（真实兜底行由 degraded 标记拦截）；
- build_overnight_decision 在缺字段检查前先过质量门控，命中写入 removed[{"quality":...}]；
- data_quality.status：存在 quality 拦截时 "partial"，否则 "complete"。

4.2 完成时刻复核：run_overnight_arbitrage 按真实墙钟耗时（wall_started）>900s 时追加 blocked_reasons="task_exceeded_valid_window"，任务超时不得沿用启动时刻放行。

4.3 yahoo5m 时间对齐：_fetch_yahoo_5m_strength 改为按 timestamp 组合整根 bar（丢弃缺字段样本），排序后校验：最后一根 ≤now-10min、最近窗口同一交易日、相邻间隔 ≤10min（排除午休/跨日拼接）；不满足则返回空（可选增强不得加分）。

## DIFF-5 新增回归测试（8 反例）

tests/test_decision_quality_gates.py 固化：
1. 部分返回不得声称覆盖（received=1/missing=1）；
2. 全部 degraded 行不标 fresh；
3. 东财 legacy 未知时间 → degraded=True；
4. K线短窗缓存不得服务长窗请求；
5. 不同取数器不串缓存；
6. is_stale+旧 source_time 不得产出 BUY、data_quality≠complete；
7. degraded 兜底行不得产出 BUY、data_quality=partial；
8. yahoo5m 极旧时间戳不得产生强度。

---

## 回归结果

pytest tests/test_decision_quality_gates.py tests/test_source_contracts.py backend/services/data_backend/tests backend/plugins/overnight_arbitrage/tests backend/plugins/tech_indicators/tests backend/plugins/principal_capital/tests -q
→ 139 passed, 1 warning

## 未完成（后续批次）

- §12.4 第 1 步剩余：候选精查贯通（§12.2 P1 末条）、_eastmoney_all_a_snapshot 分页上限与有效证券全集核对；
- §12.4 第 2–5 步（覆盖与取数一致性 / 日K持久化 / 其余能力迁移 / 部署地影子运行）。
