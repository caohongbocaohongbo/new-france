# 主力资金 Summary Finalizer 可靠性改造 — Diff 汇总

> 状态：已实现、本地验收通过，**尚未提交**（工作区）
> 范围：`backend/plugins/principal_capital/*` + CLI + workflow + watchdog + 测试
> 规模：10 文件，+989 / −134

---

## 0. 总览

本次改造把「主力资金午间/收盘摘要 finalizer」从「一次性函数 + 简单 status」升级为
**可恢复状态机 + Reconciler 自愈 + 显式 session 绑定 + doctor/repair 工具**，
并顺手解决了线上 `snapshot=completed / finalizer=not_attempted` 误报。

核心根因（已定位）：finalizer 执行了但 `notify_eligible=false`（新浪无 source_time → provisional），
旧代码跳过时**只 `release_owner` 不记录 skip**，导致 `summary_state` 永远停在默认 `not_attempted`。

---

## 1. 逐文件 Diff 明细

### 1.1 `backend/plugins/principal_capital/intraday_state.py`（+407，核心）

新增三类枚举/常量：
- `class TradingSession(str, Enum)`：`AM="am"` / `PM="pm"`（值保留小写，兼容既有 JSON/前端/CLI）。
- `class SummaryStatus`：`not_attempted / queued / running / completed / failed / skipped`。
- `class SummaryReason`：`READY / SNAPSHOT_NOT_READY / SESSION_MISMATCH / NOT_DUE / NON_TRADING_DAY /
  ALREADY_COMPLETED / ALREADY_RUNNING / NO_VALID_SNAPSHOT / MAX_RETRIES_EXCEEDED /
  NOTIFY_NOT_ELIGIBLE / WORKER_STALE / SEND_FAILED / DELIVERY_UNKNOWN`。

新增 Summary Job 状态机纯函数（无 I/O）：
- `empty_summary_job(session, trade_date, max_attempts)` — 完整 job 骨架（job_id/selected_snapshot_id/
  status/finalizer_due_at/queued_at/started_at/completed_at/attempt_count/max_attempts/next_retry_at/
  last_error_*/skip_reason/trigger_source/worker_id）。
- `dispatch_summary_job()` — `not_attempted/failed → queued`（CAS，重复返回 `ok=False`）。
- `start_summary_job()` — `queued → running`（attempt_count+1）。
- `complete_summary_job()` / `fail_summary_job()` / `skip_summary_job()`。
- `recover_stale_running()` — `running` 超时 → `failed(WORKER_STALE)`。
- `summary_jobs_due()` — Reconciler 扫描 overdue job。
- `summary_eligibility()` — session 感知的 eligibility + reason code（替换旧的 `should_finalize_session` 语义）。
- `session_cutoff()` / `finalizer_due_at()` / `summary_job_idempotency_key()`（`summary:日期:AM/PM`）。
- `get_summary_job()` / `_summary_entry()` / `_set_summary_job()`。

新增原子 I/O（文件锁 CAS）：
- `atomic_dispatch_summary_job()` / `atomic_start_summary_job()` / `atomic_summary_mutate()`。
- `load_state_raw()`（doctor/repair 读历史状态，不跨日重置）。

改动：
- `empty_state()` / `reset_state_for_trade_date()` 的 `summary_state` 升级为完整 job 字典（additive 兼容）；
  新增 `session_snapshots` 字段。
- 保留旧 `should_finalize_session` / `mark_summary_*`（§26 兼容遗留，未删除）。

### 1.2 `backend/plugins/principal_capital/service.py`（+315，核心）

- 扫描主流程记录 `state["session_snapshots"][session_label]`（snapshot_id/captured_at/session/status/quality）。
- `finalize_principal_capital_session()` **重写**：非交易日 skip → `get_snapshot_for_session` 显式绑定 →
  `atomic_dispatch_summary_job`（CAS）→ `atomic_start_summary_job`（CAS）→ `summary_eligibility` →
  生成+发送 → `complete/fail/skip`。删除旧的 owner lease 校验（由 CAS 状态机取代），
  `owner_id` 保留为 `worker_id` 审计字段。
- 新增 `get_snapshot_for_session()` / `reconcile_summary_jobs()` / `summary_doctor()` / `summary_repair()` /
  `_summary_log()`（结构化 key=value 日志）。

### 1.3 `backend/plugins/principal_capital/config.py`（+12）

- `summary_schedule`（am: 11:30/11:35，pm: 15:00/15:05）。
- `summary_running_timeout_seconds=600`、`summary_max_attempts=3`、`summary_retry_backoff_seconds=[60,300,900]`。

### 1.4 `backend/plugins/principal_capital/__init__.py`（+32）

- `run_reconcile_cli` / `run_summary_doctor_cli` / `run_summary_repair_cli`。

### 1.5 `backend/main.py`（+64）

- 新增 4 个 CLI：`--reconcile-principal-capital-summary`、`--principal-capital-summary-doctor`、
  `--principal-capital-summary-repair`、`--summary-trade-date`、`--summary-repair-execute`。
- 新增 3 个分发函数 `_run_principal_capital_reconcile_cli` / `..._doctor_cli` / `..._repair_cli`。

### 1.6 `scripts/principal_capital_watchdog.py`（+27）

- `_summary_alert` 适配新状态机：`skipped/completed/sent` 不告警；`failed` 仅在重试耗尽时告警；
  `running/queued` 按卡死告警；保留 legacy `delivery_unknown/pending` 兼容。

### 1.7 `.github/workflows/principal-capital-scan.yml`（+7）

- 收盘后追加 `--reconcile-principal-capital-summary` 自愈补调度。
- env 新增 `PC_ALLOW_PROVISIONAL_NOTIFY: "1"`（允许 provisional 发摘要邮件）。

### 1.8 测试

- `backend/plugins/principal_capital/tests/test_session_finalizer.py`（重写，+226）：12 个用例
  （completed 发信、provisional skip、无快照 skip、PM 快照不供 AM、SESSION_MISMATCH、shadow 构造、
  delivery_unknown 不重发、非交易日 skip、状态机全转换、dispatch 幂等、start 仅从 queued、failed 重试、
  max_retries、stale recovery、skip 终态、幂等键）。
- `backend/plugins/principal_capital/tests/test_intraday_state.py`（+5）：`test_cross_trade_date_resets` 适配。
- `backend/plugins/principal_capital/tests/test_integration_v3.py`（+28）：崩溃测试改为 stale-running-recovery 语义。

---

## 2. 状态机定义

```
not_attempted → queued → running → completed
                        └→ failed →(retry)→ queued
skipped（终态：非交易日/无快照/session mismatch/notify_not_eligible/…）
```

唯一约束：`(trade_date, session)` 单槽位；幂等键 `summary:YYYY-MM-DD:AM|PM`。

---

## 3. 测试结果

```
python3 -m pytest tests/ backend/plugins/principal_capital/tests/ -q
→ 350 passed
```

（注：`backend/plugins/smart_money_radar/tests/test_service.py` 有 11 个**既有失败**，与本改动无关，
在干净 HEAD 上同样失败。）

---

## 4. 上一轮已提交改动（c91fbfb，供对照）

`fix(principal-capital): 修复 owner_conflict 跨日误报 + bulk 停牌股 non_finite 误判`
（5 文件：watchdog 日期过滤+标题标签、service 清理跨日冲突诊断、sina_market bulk non_finite 修复、
2 个测试文件）。

---

# 第二轮：可恢复性 + stale worker 防护 + 周期性 Reconciler + provisional 语义

> 针对评审的 7 点意见，落地 3×P0 + 1×P1。

## 5. P0-1 可恢复 skip（不再把「无快照」错终态化）

- `SummaryStatus` 拆分：`RETRY_WAIT`（失败待重试）/ `DEAD`（重试耗尽需人工），取代原 `FAILED`。
- `SummaryReason` 新增分类 `TERMINAL_SKIP_REASONS` / `RETRYABLE_REASONS` + `is_terminal_skip()` / `is_retryable()`。
- `NO_VALID_SNAPSHOT / SNAPSHOT_NOT_READY / WORKER_STALE / SEND_FAILED` → `retry_wait`（可被 reconciler 重试）；
  `NON_TRADING_DAY / SESSION_MISMATCH / NOTIFY_NOT_ELIGIBLE` → `skipped`（终态）。
- `fail_summary_job` 按 `attempt_count` vs `max_attempts` 自动落 `retry_wait` 或 `dead`。
- `DEAD` + `force`（manual repair）重置 `attempt_count` 重新入队；自动 dispatch 拒绝 `DEAD`（`MAX_RETRIES_EXCEEDED`）。
- 状态流：`not_attempted → queued → running → completed`；`running → retry_wait → queued → …`；`retry_wait(超限) → dead`。

## 6. P0-2 run_id/lease_token 防 stale worker 回魂

- `empty_summary_job` 新增 `run_id` 字段；`start_summary_job` 写入 `run_id`（uuid）。
- `complete/fail/skip_summary_job` 校验 `_run_id_matches`，run_id 不匹配拒绝写入（旧 worker 回魂无效）。

## 7. P0-3 Reconciler 周期性兜底

- `principal-capital-scan.yml` 循环内每轮顺带 reconcile（同 job，文件锁有效）。
- 新增 `.github/workflows/principal-capital-reconcile.yml`：`*/10 1-7 * * 1-5`（北京 09:00–15:59 每 10 分钟），
  覆盖午间 11:30–13:00 空窗。

## 8. P1-4 provisional 语义统一

- `summary_eligibility`：`accepted`→READY；`provisional`+allow→`READY_PROVISIONAL`；`provisional`+不允许→`NOTIFY_NOT_ELIGIBLE`；
  `degraded`→不通知；旧快照无 `status` 字段退回 `notify_eligible` 布尔。
- 双分支测试：`test_provisional_skips_when_not_allowed` / `test_provisional_allowed_completes`。

## 9. 顺手修复的两个真 bug

1. shadow/readonly 不再污染 official 状态（读-only 早退，不 dispatch/start）。
2. reconcile 重复 dispatch（改为直接调 finalize，CAS 在 finalize 内部）。

## 10. 新增测试

`test_stale_worker_cannot_write`、`test_retryable_reason_goes_retry_wait_not_skipped`、`test_dead_requires_manual_repair`、
`test_no_snapshot_is_retryable`、`test_provisional_allowed_completes`、`test_transitions_*`（含 history 断言）。

测试：`353 passed`（`tests/` + `principal_capital/tests/`）。

---

# 第三轮：delivery state / 邮件发送幂等 + doctor 健康判定 + StateStore 抽象

## 11. delivery state（邮件发送幂等，封堵 crash 窗口）

- `empty_summary_job` 新增 `delivery` 子对象（`status: not_started|sending|accepted|failed|unknown` + idempotency_key/attempt_id/sent_at/message_id/last_error）。
- `SummaryStatus` 新增 `DELIVERY_UNKNOWN`（终态，禁止自动重发）；`DELIVERY_UNKNOWN` 从 `RETRYABLE_REASONS` 移除（投递不明不得自动重试）。
- 新增 `mark_delivery_sending / accepted / failed / unknown_terminal`；`recover_stale_running` 遇 `delivery=sending` 的 stale running 直接落 `delivery_unknown` 终态（而非 `WORKER_STALE` 重试）。
- finalizer：send 前原子落盘 `sending`；成功→`accepted`+`completed` 同一次原子写；异常→`delivery_unknown` 终态；明确失败→`failed`+`retry_wait(SEND_FAILED)`。
- 崩溃窗口测试 `test_send_then_crash_no_resend`：send 后 complete 前 crash → 恢复为 delivery_unknown，自动 dispatch 被拒。

## 12. doctor 健康判定（单一判定来源）

- 新增 `assess_summary_health(state, session, snapshot, now, cfg)` → `{health, repairable, reason, recommended_action}`，health ∈ healthy|warning|broken。
- `summary_doctor` 输出增加 `health` 字段（`suggested_action` 直接取 `health.recommended_action`）。

## 13. StateStore 抽象

- 新增 `SummaryStateStore`（抽象基类：load/load_raw/save/mutate/dispatch/start）+ `FileSummaryStateStore`（fcntl 文件实现）+ 默认 `STATE_STORE` 实例。
- `atomic_start_summary_job` 增加 `run_id` 透传（修 StateStore.start 的调用签名）。
- fcntl/filelock/json 逻辑全部封装在 `intraday_state.py`（service 层只调 `intraday.atomic_*`/`STATE_STORE`），后续可无痛切 Redis/Postgres 实现。

## 14. watchdog 复用统一健康判定（消除三套规则）

- `scripts/principal_capital_watchdog.py::_summary_alert` 改为调用 `intraday.assess_summary_health` 作为唯一「broken」判定来源，映射：broken→告警、running/queued/not_attempted→卡死告警、retry_wait/completed/skipped→不告警。

## 15. 新增测试

`test_send_then_crash_no_resend`、`test_two_reconcilers_only_one_dispatches`、`test_completed_cannot_regress`、
`test_reconcile_recovers_missed_scheduler`、`test_reconcile_retries_after_snapshot_arrives`、`test_timezone_independent`。

测试：`359 passed`（`tests/` + `principal_capital/tests/`）。

---

# 第四轮：可靠性收口（4×P0 + 3×P1 + 额外 P1）

## 16. P0 修复

- **stale recovery 原子化**：新增 `atomic_recover_stale_running()`，reconcile 的 recovery 走文件锁临界区（修「read→modify→write 无锁导致 completed 被覆盖成 retry_wait」）。
- **run_id 严格匹配**：`_run_id_matches` 改为严格相等（`run_id` 为空且 job 无 token 才放行终态迁移；`current=None` 不再放行旧 worker）。
- **from-status 校验**：新增 `ALLOWED_TRANSITIONS` 白名单 + `_transition_allowed()`，`complete/fail/skip/delivery_unknown` 均校验 from 状态，终态 completed/skipped 不可回退。
- **workflow 统一 concurrency + 持久化失败致命**：scan/reconcile 共用 `principal-capital-state-${{ github.ref }}`；finalizer/reconcile 后的 `commit_screening_data.sh` 失败 `exit 1`（不再 `|| echo 忽略`）。

## 17. P1 修复

- **transition history from/to**：`_record_transition` 显式传 `from_status`，修「queued→queued / completed→completed」错记 bug；测试精确断言 `not_attempted→queued→running→completed`。
- **finalizer_due / watchdog 提前告警**：`assess_summary_health` 用 `finalizer_due_at` 判断 not_attempted 是否 overdue；watchdog 用 `finalizer_due_at + 5min 宽限` 取代硬编码 11:30/15:00。
- **SESSION_MISMATCH → broken**：`assess_summary_health` 对 skipped 且 reason=SESSION_MISMATCH 判定 broken（非 healthy）。
- **StateStore 真正接管**：service 层 Summary 状态访问全部走 `STATE_STORE`（`load/mutate/dispatch/start/recover_stale/load_raw`），不再直连 `intraday.atomic_*`。

## 18. 额外 P1

- **NO_VALID_SNAPSHOT 不消耗重试预算**：`start_summary_job` 改增 `dispatch_count`；`mark_delivery_sending` 才增 `attempt_count`（实际发送次数），前置失败不再导致 DEAD。
- **历史日期 repair 保护**：`summary_repair --execute` 对非当前 `state_trade_date` 抛 `RuntimeError`。

## 19. 新增测试

`test_stale_worker_after_recovery_before_new_start`、`test_completed_cannot_fail_skip_delivery_unknown`、
`test_session_mismatch_is_broken`、`test_summary_alert_grace_period_no_premature_alert`、
`test_summary_alert_after_grace`、`test_no_snapshot_does_not_consume_retry_budget`、
`test_repair_blocks_non_current_trade_date`、history 精确断言。

测试：`366 passed`（`tests/` + `principal_capital/tests/`）。

## 20. P2 代码整洁（收口补）

- `SummaryStateStore` 改为 `ABC + @abstractmethod`（不再靠 `raise NotImplementedError`）。
- `reconcile_summary_jobs` 结果键重命名：`dispatched/skipped` → `processed/deferred/broken`（语义更清晰）。
- docstring 统一 `retry_wait/dead`，清理旧 `failed` 术语。

---

# 附：全量规模

- 修改 11 文件 + 新增 2 文件（`.github/workflows/principal-capital-reconcile.yml` + 本 doc）。
- 总计 **+1680 / −142**（`git diff`，不含新增文件）。
- 完整原始 diff 见 `docs/25_主力资金摘要finalizer_可靠性改造_full.diff`。
