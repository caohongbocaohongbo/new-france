# Codex 执行任务：修复"旧涨停池冒充今日"数据失真 + 建立外发防错机制（Phase 1）

> 一次性执行指令。**严格按范围执行**，不要顺手重构无关代码。
> 项目根：`/Users/fangcang/new-france`，所有路径相对该根。
> 语言：新增注释/文档用**中文**；**禁止 emoji**。
> 背景事故：2026-07-07 15:10 的每日推荐邮件把 `000656 金科股份` 放进"今日涨停池"，
> 实际是 2026-07-06 的涨停。用户要求：对外决策邮件的数据必须真实、准确、有迹可循；
> 数据不够确定时**宁可阻断也不外发**。

---

## 0. 已核实的根因（先读，避免修错方向）

1. **核心快照跨交易日失真（真正的数据错误）**
   - `reports/latest.json`：`zt_meta.fetched_at = 2026-07-06T17:43:40+08:00`，
     `index_snapshot.fetched_at = 2026-07-06T17:44:56+08:00` —— 均为**昨日**快照，却被当作今日数据。
   - 根因在 `backend/services/data_backend/snapshots.py` 的 `_is_fresh()`：
     当 `current_trading_session() != "open"`（如 15:10 收盘后）时**无条件返回 True**，
     缺少"快照必须属于当天"的校验。每日筛选 cron 在 15:10 运行，于是昨日 zt_pool/index
     被判为 fresh 直接返回，**根本没有去拉今天的数据**。

2. **`000656` 的定位（避免修错对象）**
   - 在 `results` 中它的 `recommendation = "PASS"`，**不在推荐区**。
   - 它出现在**"今日涨停池"展示区（`zt_list`）**，且该区数据来自上面那张昨日快照。
   - 因此本次修复重心是**核心行情源的交易日真实性 + 外发前硬阻断 + 证据链**，
     不是改候选/推荐算法（候选按监控池回撤生成本身是设计如此，不动）。

3. **审计代理不含交易日校验**
   - `backend/agents/layer4_audit/validators.py` 只校验价格/封板/K线完整/回撤一致/财务，
     没有"数据日期 == 目标交易日"的校验，也不能阻断外发。

---

## 1. 范围

**Phase 1（本次执行，关键防错三件套）**
- 修复 `_is_fresh` 交易日校验（根因）。
- 每日筛选主链路：核心行情源做交易日校验，不满足则强制现拉；仍失败则进入阻断。
- 外发前硬阻断：核心源非"今日有效"时，不发正式推荐邮件，改发一封"数据异常已阻断"告警邮件。
- 证据链：每只股票结果落盘 `evidence`（来源、fetched_at、zt_date、added_date、history 长度等）。
- 覆盖以上路径的测试。

**Phase 2（本次不执行，仅在文末登记为后续）**
- 邮件口径拆分（今日涨停池 vs 历史回撤推荐，逐股标注、排除 PASS）。
- 审计代理新增"交易日一致性"validator。
- 数据资产面板暴露阻断状态。

**范围外（不要做）**
- 不改候选生成/评分算法；不改前端页面结构；不引入新持久化存储；
- 不动 `principal_capital` 插件熔断逻辑；不改 `data-snapshots` 提交/恢复脚本。

---

## 2. 根因修复 —— `backend/services/data_backend/snapshots.py`

`_is_fresh()` 改为：**先判断快照是否属于当天**，跨日一律不新鲜；当天再按原有时段/TTL 规则。

```python
def _is_fresh(asset: str, payload: Optional[dict]) -> bool:
    if not payload:
        return False
    fetched = _parse_dt(payload.get("fetched_at"))
    if fetched is None:
        return False
    now = _now()
    # 跨交易日快照一律不新鲜：昨日日终快照不得在今天冒充"今日数据"。
    # 注意：节假日/半日市交易日历为已知 TODO，此处先按北京时间自然日判定。
    if fetched.date() != now.date():
        return False
    session = current_trading_session(now)
    if session != "open":
        # 当天且收盘后：最后一张当日快照仍视为有效（保留原设计意图）
        return True
    age = _age_seconds(payload.get("fetched_at"))
    return age is not None and age <= ASSET_TTLS[asset]
```

**副作用（预期且正确）**：收盘后每日批处理遇到昨日快照 → 不新鲜 → `_read_asset` 会继续调用
fetcher 现拉今日数据；现拉成功即今日数据，失败则回落本地并标 `degraded/stale`（供第 4 节阻断判断）。
数据资产面板同理不再把昨日快照显示为 `fresh`。

---

## 3. 主链路捕获核心源元信息 —— `backend/services/screening_service.py`

当前 `_fetch_index_snapshot()` 丢弃了 meta。改为在 `run_full_pipeline` 内**保留三个核心源的 meta**：
- `zt_pool`：已有 `zt_snapshot_meta`（`_read_zt_pool_with_cache` 返回）。
- `index_snapshot`：改为在主链路直接调用 `_read_index_snapshot_with_cache()` 并保留其 meta（不要经 `_fetch_index_snapshot` 丢弃）。
- `quotes`：`_read_quotes_with_cache` 返回的 meta 保留（监控池实时行情）。

将三者收集为：
```python
core_source_meta = {
    "zt_pool": zt_snapshot_meta,
    "index_snapshot": index_meta,
    "quotes": quotes_meta,
}
```
并写入返回结果与 `latest.json`（顶层新增 `core_source_meta` 字段）。

---

## 4. 外发前硬阻断 —— `backend/services/screening_service.py` + notifier

### 4.1 交易日校验
新增：
```python
def _validate_core_sources_for_date(target_date, core_source_meta) -> list[dict]:
    """返回问题列表；为空表示核心源均为目标交易日的有效数据。"""
    problems = []
    target_str = target_date.strftime("%Y-%m-%d")
    for asset, meta in core_source_meta.items():
        fetched_at = (meta or {}).get("fetched_at")
        status = (meta or {}).get("status")
        fetched_day = (fetched_at or "")[:10]
        if not fetched_at or fetched_day != target_str or status not in {"fresh"}:
            problems.append({
                "asset": asset,
                "expected_date": target_str,
                "fetched_at": fetched_at,
                "status": status,
            })
    return problems
```

### 4.2 阻断分流
在 `run_full_pipeline` 调用 `recom.execute(...)` **之前**插入判断：
- `problems = _validate_core_sources_for_date(target_date, core_source_meta)`
- 若 `problems` 非空：
  - **不**调用正常推荐外发（即不走 `recom.execute` 的 `send_notification`；可传 `dry_run=True` 让其只生成报告文件，或跳过外发分支）。
  - 若非 `dry_run`：调用新函数 `send_data_integrity_alert(target_date, problems, ...)` 发一封**明确标题**的告警邮件（例如"[New France] 数据异常：核心行情源非今日，已阻断今日推荐"），正文列出每个 problem 的 asset/expected_date/fetched_at/status。
  - 返回结果 `status="blocked_stale_data"`，并在返回体与 `latest.json` 写入 `block_reason=problems`。
  - 记录 `logger.error`。
- 若 `problems` 为空：走原有正常流程。

### 4.3 告警邮件函数 —— `backend/agents/layer3_recommendation/notifier.py`
新增 `send_data_integrity_alert(target_date, problems, config=None)`：
- 复用现有 SMTP/收件人配置读取（`_get_notify_config` / `_resolve_recipient_list`）。
- 纯文本或极简 HTML 即可，**不得**包含任何股票推荐内容。
- 返回是否发送成功；发送失败仅 `logger.error`，不抛出。

---

## 5. 证据链 —— `backend/services/screening_service.py`

在构建 `results` 的每只股票 dict 中新增 `evidence`：
```python
"evidence": {
    "entry_reason": "watchlist_drawdown",   # 本链路候选均来自监控池回撤
    "zt_date": s.zt_date,
    "added_date": (s.extra or {}).get("added_date"),
    "quote_source": quotes_meta.get("source"),
    "quote_fetched_at": quotes_meta.get("fetched_at"),
    "zt_source": zt_snapshot_meta.get("source"),
    "zt_fetched_at": zt_snapshot_meta.get("fetched_at"),
    "index_fetched_at": index_meta.get("fetched_at"),
    "history_length": len(historical.get(s.code)) if historical.get(s.code) is not None else 0,
},
```
落盘进 `latest.json` 的 `results[].evidence`。**本阶段不要求邮件渲染 evidence**（Phase 2 处理展示）。

---

## 6. 测试

新增/补充：
- `backend/services/data_backend/tests/test_snapshots.py`：
  - 昨日 `fetched_at` 的快照，即便当前为收盘时段，`_is_fresh` 返回 `False`；
  - 当日 `fetched_at`、收盘时段 → `True`；当日、交易时段内超 TTL → `False`。
- `tests/test_email_block_on_stale.py`（新增）：
  - monkeypatch 使核心源 meta 的 `fetched_at` 为昨日 →
    `_validate_core_sources_for_date` 返回非空；
  - 走 `run_full_pipeline`（可 mock 下游 fetch/notifier）验证：
    结果 `status=="blocked_stale_data"`、正常 `send_notification` **未被调用**、
    `send_data_integrity_alert` **被调用一次**。
  - 核心源全为今日 → 不阻断、正常路径。
- 证据链：断言 `results[0]["evidence"]` 含上述键。
- 网络与文件全部 monkeypatch/tmp 隔离，**不得真实发 HTTP / 真实发邮件**。

---

## 7. 验收标准

1. `_is_fresh`：昨日快照在任何时段都不再判为 fresh；当日快照行为不变。
2. 每日筛选在核心源仅有昨日快照且现拉失败时：**不发**正式推荐邮件，改发数据异常告警邮件，
   `latest.json.status == "blocked_stale_data"` 且含 `block_reason`。
3. `latest.json` 顶层含 `core_source_meta`；`results[].evidence` 字段完整。
4. 复现验证：用昨日 `fetched_at` 的 zt_pool/index 快照跑一次（现拉打桩为失败），确认被阻断且
   `000656` 不会出现在任何对外推荐邮件内容中。
5. `pytest backend/services/data_backend/tests/ tests/ -q` 全绿。
6. `python -m backend.main --serve` 正常启动（无 import 错误）。
7. 未改动候选/评分算法、未改前端、未新增持久化存储。

---

## 8. 交付物
```
backend/services/data_backend/snapshots.py        # _is_fresh 交易日校验
backend/services/screening_service.py             # 核心源 meta 捕获 + 交易日校验 + 阻断分流 + evidence
backend/agents/layer3_recommendation/notifier.py  # send_data_integrity_alert
backend/services/data_backend/tests/test_snapshots.py
tests/test_email_block_on_stale.py
reports/latest.json                                # 运行后产物, 勿手工提交(已 gitignore reports/*.json)
```

完成后请在回复中列出：改动文件、验收 1-7 实际结果、以及复现验证（第 4 项）的实际输出。

---

## 附：Phase 2 后续登记（本次不实现）
1. 邮件口径拆分：`今日涨停池`（须今日实时、逐股标注）与 `历史监控池回撤推荐`（仅 STRONG_BUY/BUY/WATCH，排除 PASS/审核失败）分区展示，各股标 `zt_date/added_date/recommendation` 与 evidence。
2. `backend/agents/layer4_audit/validators.py` 新增 `validate_trading_day_consistency`。
3. 数据资产面板暴露 `blocked_stale_data` 状态。
