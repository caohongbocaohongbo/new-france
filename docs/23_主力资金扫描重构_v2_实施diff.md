# 23 主力资金扫描重构 v2 — 实施 diff（第三轮修复后）

> 状态：代码完成（M1-M4），第三轮 P0-R1~R5 / P1-R1~R8 已修复；未启用 hybrid，未启动 M5，bulk_admitted=false。
>
> 依据：可验证漏斗与单写者.md + 代码核验与修复清单.md + 技术总监复验报告_2026-09-16.md

## 回归结果

- 主回归（排除 radar 目录）：**512 passed**
- radar v2 + workflow + watchdog 定向：**25 passed**
- compileall：OK；git diff --check：OK

## 变更文件

| 文件 | 类型 |
|---|---|
| .github/workflows/principal-capital-scan.yml | 修改 |
| backend/main.py | 修改 |
| backend/plugins/principal_capital/__init__.py | 修改 |
| backend/plugins/principal_capital/config.py | 修改 |
| backend/plugins/principal_capital/notifier.py | 修改 |
| backend/plugins/principal_capital/router.py | 修改 |
| backend/plugins/principal_capital/service.py | 修改 |
| backend/plugins/principal_capital/sources/sina.py | 修改 |
| backend/plugins/principal_capital/sources/sina_market.py | 修改 |
| backend/plugins/principal_capital/sources/multi_source.py | 修改 |
| backend/plugins/principal_capital/tests/test_service.py | 修改 |
| backend/plugins/principal_capital/tests/test_sina.py | 修改 |
| backend/plugins/principal_capital/tests/test_sina_market.py | 修改 |
| backend/plugins/smart_money_radar/config.py | 修改 |
| backend/plugins/smart_money_radar/service.py | 修改 |
| frontend/js/plugins/principal_capital.js | 修改 |
| scripts/commit_screening_data.sh | 修改 |
| scripts/restore_screening_data.sh | 修改 |
| scripts/verify_fund_flow_leaderboard.py | 修改 |
| scripts/principal_capital_watchdog.py | 修改 |
| backend/plugins/principal_capital/intraday_state.py | 新增 |
| backend/plugins/principal_capital/pipeline.py | 新增 |
| backend/plugins/principal_capital/tests/test_acceptance_v2.py | 新增 |
| backend/plugins/principal_capital/tests/test_bulk_pipeline.py | 新增 |
| backend/plugins/principal_capital/tests/test_intraday_state.py | 新增 |
| backend/plugins/principal_capital/tests/test_integration_v3.py | 新增 |
| backend/plugins/principal_capital/tests/test_pipeline_modes.py | 新增 |
| backend/plugins/principal_capital/tests/test_session_finalizer.py | 新增 |
| backend/plugins/smart_money_radar/tests/test_pool_selection_v2.py | 新增 |

---

## .github/workflows/principal-capital-scan.yml

```diff
diff --git a/.github/workflows/principal-capital-scan.yml b/.github/workflows/principal-capital-scan.yml
index 0cc74be..4d01274 100644
--- a/.github/workflows/principal-capital-scan.yml
+++ b/.github/workflows/principal-capital-scan.yml
@@ -92,6 +92,8 @@ jobs:
           SELL_THRESHOLD: ${{ github.event.inputs.sell_threshold || '30' }}
           ENABLE_VERIFY_FLAG: ${{ github.event.inputs.enable_verify == 'true' && '--enable-verify' || '' }}
           FORCE_FLAG: ${{ github.event.inputs.force == 'true' && '--force' || '' }}
+          PC_EXECUTION_MODE: official
+          PC_OFFICIAL_OWNER: "${{ github.run_id }}:${{ github.run_attempt }}:${{ github.job }}"
         run: |
           # ── 自循环：每 5 分钟扫描一次，直至本时段收盘 ──
           # 触发点无论早晚，只要落在交易日内进入循环即可覆盖整段。
@@ -99,12 +101,22 @@ jobs:
           #   这样上午盘 job 不会空占午休（11:30–13:00）时段。
           # 手动 workflow_dispatch 带 --force 时，只跑一轮即退出，避免非交易时段空转。
           scan_once() {
+            set +e
             timeout --signal=TERM 120 \
             python -m backend.main --run-principal-capital-scan \
               --buy-threshold "${BUY_THRESHOLD}" \
               --sell-threshold "${SELL_THRESHOLD}" \
               ${ENABLE_VERIFY_FLAG} \
-              ${FORCE_FLAG} || echo "本轮扫描非零退出（继续下一轮）"
+              ${FORCE_FLAG}
+            rc=$?
+            set -e
+            if [ "${rc}" -eq 2 ]; then
+              echo "关键失败（owner_conflict/consistency_error），终止本 session"
+              exit 2
+            fi
+            if [ "${rc}" -ne 0 ]; then
+              echo "本轮扫描非零退出（继续下一轮）"
+            fi
           }
 
           # 手动强制运行：仅一轮
@@ -117,9 +129,11 @@ jobs:
           start_hm="$(TZ=Asia/Shanghai date +%H%M)"
           if [ "${start_hm}" -lt 1200 ]; then
             SESSION_END=1130   # 上午盘
+            SESSION=am
             echo "进入上午盘循环（收于 11:30），触发时刻北京 ${start_hm}"
           else
             SESSION_END=1500   # 下午盘
+            SESSION=pm
             echo "进入下午盘循环（收于 15:00），触发时刻北京 ${start_hm}"
           fi
 
@@ -141,3 +155,7 @@ jobs:
             echo "等待 5 分钟进入下一轮..."
             sleep 300
           done
+
+          # 23 v2：session 边界额外调用一次 finalizer（午间/收盘日内汇总，恰好一次）
+          python -m backend.main --finalize-principal-capital-session --session "${SESSION}" --execution-mode official --owner-id "${PC_OFFICIAL_OWNER}" || exit 2
+          bash scripts/commit_screening_data.sh || echo "快照提交失败（忽略）"
```

## backend/main.py

```diff
diff --git a/backend/main.py b/backend/main.py
index d478cb7..c48bb88 100644
--- a/backend/main.py
+++ b/backend/main.py
@@ -278,6 +278,15 @@ def main():
                         help="运行尾盘隔夜套利 14:43 决策任务")
     parser.add_argument("--run-principal-capital-scan", action="store_true",
                         help="[插件] 执行主力资金双向扫描")
+    parser.add_argument("--execution-mode", choices=["official", "shadow", "readonly"],
+                        default=None, help="[插件23v2] 执行模式（official/shadow/readonly，默认 readonly）")
+    parser.add_argument("--pipeline-mode", choices=["strict", "hybrid"],
+                        default=None, help="[插件23v2] 流水线模式（strict/hybrid）")
+    parser.add_argument("--owner-id", default=None, help="[插件23v2] official owner 标识")
+    parser.add_argument("--finalize-principal-capital-session", action="store_true",
+                        help="[插件23v2] 执行午间/收盘日内汇总 finalizer")
+    parser.add_argument("--session", choices=["am", "pm"], default="am",
+                        help="[插件23v2] finalizer 会话（am=午间，pm=收盘）")
     parser.add_argument("--run-radar-once", action="store_true",
                         help="[插件] 执行 smart_money_radar 盘中雷达单轮扫描")
     parser.add_argument("--run-radar-daemon", action="store_true",
@@ -375,6 +384,10 @@ def main():
         _run_principal_capital_cli(args, logger)
         return
 
+    if args.finalize_principal_capital_session:
+        _run_principal_capital_finalize_cli(args, logger)
+        return
+
     if args.run_radar_once:
         _run_smart_money_radar_once_cli(args, logger)
         return
@@ -460,14 +473,38 @@ def _run_principal_capital_cli(args, logger):
         return
     result = run_scan_cli(args)
     logger.info(
-        "主力资金扫描: status=%s source=%s scanned=%s buy_fresh=%s sell_fresh=%s email=%s",
+        "主力资金扫描: status=%s mode=%s source=%s scanned=%s buy_fresh=%s sell_fresh=%s email=%s",
         result.get("status"),
+        result.get("execution_mode"),
         (result.get("source_status") or {}).get("active_source"),
         result.get("scanned"),
         result.get("buy_fresh_count"),
         result.get("sell_fresh_count"),
         result.get("email_sent"),
     )
+    # P1-8：owner conflict / 一致性错误属于关键失败，映射为非零退出（workflow 不得忽略）
+    if result.get("status") in {"owner_conflict", "consistency_error"}:
+        raise SystemExit(2)
+
+
+def _run_principal_capital_finalize_cli(args, logger):
+    """[插件23v2] 午间/收盘日内汇总 finalizer CLI 入口。"""
+    try:
+        from .plugins.principal_capital import run_finalize_cli
+    except ImportError as exc:
+        logger.error("主力资金插件未安装: %s", exc)
+        return
+    result = run_finalize_cli(args)
+    logger.info(
+        "主力资金日内汇总 finalizer: session=%s status=%s reason=%s email=%s",
+        result.get("session"),
+        result.get("status"),
+        result.get("reason"),
+        result.get("email_sent"),
+    )
+    # P1-8：finalizer 关键失败（owner 冲突 / 发送失败 / 投递结果不明）非零退出
+    if result.get("status") in {"owner_conflict", "send_failed", "delivery_unknown"}:
+        raise SystemExit(2)
 
 
 def _run_smart_money_radar_once_cli(args, logger):
```

## backend/plugins/principal_capital/__init__.py

```diff
diff --git a/backend/plugins/principal_capital/__init__.py b/backend/plugins/principal_capital/__init__.py
index 68c9d5f..0da9d11 100644
--- a/backend/plugins/principal_capital/__init__.py
+++ b/backend/plugins/principal_capital/__init__.py
@@ -28,4 +28,17 @@ def run_scan_cli(args):
         enable_verify=getattr(args, "enable_verify", False),
         dry_run=getattr(args, "dry_run", False),
         force=getattr(args, "force", False),
+        execution_mode=getattr(args, "execution_mode", None),
+        pipeline_mode=getattr(args, "pipeline_mode", None),
+        owner_id=getattr(args, "owner_id", None),
+    )
+
+
+def run_finalize_cli(args):
+    """午间/收盘摘要 finalizer CLI 入口。"""
+    from .service import finalize_principal_capital_session
+    return finalize_principal_capital_session(
+        session=getattr(args, "session", "am"),
+        execution_mode=getattr(args, "execution_mode", None),
+        owner_id=getattr(args, "owner_id", None),
     )
```

## backend/plugins/principal_capital/config.py

```diff
diff --git a/backend/plugins/principal_capital/config.py b/backend/plugins/principal_capital/config.py
index f85ed31..ef59def 100644
--- a/backend/plugins/principal_capital/config.py
+++ b/backend/plugins/principal_capital/config.py
@@ -5,6 +5,29 @@
 import os
 from pathlib import Path
 
+
+def _env_bool(name: str, default: bool) -> bool:
+    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}
+
+
+def _env_int(name: str, default: int) -> int:
+    try:
+        return int(float(os.environ.get(name, str(default))))
+    except (TypeError, ValueError):
+        return int(default)
+
+
+def _env_float(name: str, default: float) -> float:
+    try:
+        return float(os.environ.get(name, str(default)))
+    except (TypeError, ValueError):
+        return float(default)
+
+
+def _env_csv(name: str, default: str) -> list:
+    return [item.strip() for item in str(os.environ.get(name, default)).split(",") if item.strip()]
+
+
 # 插件数据/报告目录（与主项目共享 data/ reports/，但文件名都加 plugin 前缀避免冲突）
 PROJECT_DIR = Path(__file__).resolve().parents[3]
 DATA_DIR = PROJECT_DIR / "data"
@@ -39,6 +62,42 @@ CONFIG = {
     "sina_max_workers": 40,                       # 并发线程数（美国 IP 实测 40 并发仍 100% 成功）
     "sina_codes_cache_ttl_seconds": 259200,       # 主板代码清单缓存 3 天（新股极少，省每轮翻页 ~25s）
 
+    # ---- 23 v2：执行模式与唯一写者 ----
+    # 副作用总开关：official=GitHub Actions 唯一正式写者；shadow=只写 *_shadow.json 与独立状态；
+    # readonly=仅诊断，不发信、不写 official 状态。未设置时默认 readonly，避免本机误发邮件。
+    "execution_mode": os.environ.get("PC_EXECUTION_MODE", "readonly").strip().lower() or "readonly",
+    "official_owner": os.environ.get("PC_OFFICIAL_OWNER", "").strip(),
+    "owner_lease_seconds": _env_int("PC_OWNER_LEASE_SECONDS", 600),
+
+    # strict/hybrid 流水线。初始不得默认 hybrid。
+    "pipeline_mode": os.environ.get("PC_PIPELINE_MODE", "strict").strip().lower() or "strict",
+    "bulk_admitted": _env_bool("PC_BULK_ADMITTED", False),
+
+    # bulk 粗筛经验参数（不是安全边界；正式准入阈值由 M5 的 5 日 shadow 数据分布产生）
+    "coarse_buy_ratio": _env_float("PC_COARSE_BUY_RATIO", 40.0),
+    "coarse_sell_ratio": _env_float("PC_COARSE_SELL_RATIO", -24.0),
+    # r0_net / r0_ratio 极值头部数量（每侧），初值仅供 shadow 观察，不承诺不漏
+    "coarse_r0_head": _env_int("PC_COARSE_R0_HEAD", 50),
+
+    # 精算阶段
+    "refine_workers": _env_int("PC_REFINE_WORKERS", 20),
+    "refine_qps": _env_int("PC_REFINE_QPS", 20),
+    "round_deadline_seconds": _env_int("PC_ROUND_DEADLINE_SECONDS", 90),
+    "refine_timeout_seconds": _env_float("PC_REFINE_TIMEOUT_SECONDS", 8.0),
+
+    # hybrid 持续审计
+    "complement_audit_size": _env_int("PC_COMPLEMENT_AUDIT_SIZE", 300),
+    "sentinel_times": _env_csv("PC_SENTINEL_TIMES", "09:35,10:30,13:30,14:30,14:50"),
+
+    # 质量门控：默认禁止未知源时间的 direct 通知
+    "allow_provisional_notify": _env_bool("PC_ALLOW_PROVISIONAL_NOTIFY", False),
+
+    # 同源日内特征
+    "intraday_max_points": _env_int("PC_INTRADAY_MAX_POINTS", 24),
+
+    # 摘要 finalizer
+    "summary_max_age_min": _env_int("PC_SUMMARY_MAX_AGE_MIN", 15),
+
     # 邮件 SMTP（与主项目共享相同环境变量名以复用 secrets）
     "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
     "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
@@ -50,10 +109,17 @@ CONFIG = {
 
 # 文件路径（插件独立命名空间）
 REPORT_FILE = REPORT_DIR / "principal_capital_latest.json"
+SHADOW_REPORT_FILE = REPORT_DIR / "principal_capital_shadow.json"
 HISTORY_FILE = REPORT_DIR / "principal_capital_history.json"
 SOURCE_HEALTH_FILE = DATA_DIR / "principal_capital_source_health.json"
 CACHE_FILE = DATA_DIR / "principal_capital_cache.parquet"
 SINA_CODES_CACHE_FILE = DATA_DIR / "principal_capital_sina_codes.json"
+INTRADAY_STATE_FILE = DATA_DIR / "principal_capital_intraday_state.json"
+SHADOW_STATE_FILE = DATA_DIR / "principal_capital_intraday_state_shadow.json"
+MANUAL_STATUS_FILE = DATA_DIR / "principal_capital_manual_status.json"
+OWNER_CONFLICT_FILE = DATA_DIR / "principal_capital_owner_conflict.json"
+M5_AUDIT_FILE = DATA_DIR / "principal_capital_m5_audit.json"
+M5_AUDIT_MAX_RECORDS = 1000
 
 # data-snapshots 分支的 GitHub raw 前缀。
 # 用途：Render Web Service 自身取不到东财数据（IP 被封），读接口在本地报告为空时
@@ -68,3 +134,61 @@ SNAPSHOT_RAW_BASE = os.environ.get(
 def notified_file(today_iso: str, direction: str) -> Path:
     """当日去重文件路径。"""
     return DATA_DIR / f"principal_capital_{direction}_notified_{today_iso}.json"
+
+
+def resolve_execution_mode(explicit: str = None) -> str:
+    """解析执行模式。非法值抛 ValueError；默认 readonly（避免本机误发邮件）。"""
+    mode = (explicit or CONFIG["execution_mode"]).strip().lower()
+    if mode not in {"official", "shadow", "readonly"}:
+        raise ValueError(f"非法 PC_EXECUTION_MODE: {mode!r}")
+    return mode
+
+
+def resolve_pipeline_mode(explicit: str = None) -> str:
+    """解析流水线模式。M6 完成前 hybrid 未实现，代码层拒绝，effective 只能是 strict。"""
+    requested = (explicit or CONFIG["pipeline_mode"]).strip().lower()
+    if requested not in {"strict", "hybrid"}:
+        raise ValueError(f"非法 PC_PIPELINE_MODE: {requested!r}")
+    if requested == "hybrid":
+        # P0-3：不得出现“报告为 hybrid、实际跑 strict”的假执行状态
+        raise RuntimeError(
+            "hybrid 尚未完成生产实现（M6 未完成），禁止启用；本阶段 effective 只能为 strict"
+        )
+    return "strict"
+
+def atomic_write_json(path, payload) -> None:
+    """统一 JSON 原子写（临时文件 + os.replace），供 official/shadow/manual 报告共用。"""
+    import json as _json
+    import math as _math
+    import os as _os
+    import uuid as _uuid
+
+    def _safe(value):
+        if isinstance(value, float):
+            return value if _math.isfinite(value) else None
+        if isinstance(value, dict):
+            return {k: _safe(v) for k, v in value.items()}
+        if isinstance(value, list):
+            return [_safe(v) for v in value]
+        return value
+
+    target = Path(path)
+    target.parent.mkdir(parents=True, exist_ok=True)
+    tmp = target.with_suffix(target.suffix + f".tmp.{_os.getpid()}.{_uuid.uuid4().hex[:8]}")
+    tmp.write_text(
+        _json.dumps(_safe(payload), ensure_ascii=False, indent=2, default=str),
+        encoding="utf-8",
+    )
+    _os.replace(tmp, target)
+
+
+def config_fingerprint() -> str:
+    """M5 审计用配置指纹：粗筛/阈值/票池等影响候选集合的参数变更会改变指纹。"""
+    import hashlib as _hashlib
+
+    keys = (
+        "buy_threshold_ratio", "sell_threshold_ratio", "exclude_star", "min_amount_yuan",
+        "coarse_buy_ratio", "coarse_sell_ratio", "coarse_r0_head",
+    )
+    text = "|".join(f"{key}={CONFIG.get(key)}" for key in keys)
+    return _hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
```

## backend/plugins/principal_capital/notifier.py

```diff
diff --git a/backend/plugins/principal_capital/notifier.py b/backend/plugins/principal_capital/notifier.py
index 2de02e9..6e66696 100644
--- a/backend/plugins/principal_capital/notifier.py
+++ b/backend/plugins/principal_capital/notifier.py
@@ -3,6 +3,7 @@
 设计原则：插件自带 SMTP 实现，仅读取 SMTP_* 环境变量。这样插件迁移到新项目时
 无需关心原项目的邮件模块。
 """
+import html
 import logging
 import os
 import smtplib
@@ -73,3 +74,47 @@ def send_email(subject: str, text_content: str, html_content: str,
     except (OSError, ssl.SSLError) as exc:
         logger.warning("SMTP 网络/SSL 错误: %s", exc)
         return False, f"NetworkError: {exc}"
+
+def build_summary_payload(buy_today: list, session: str, now) -> tuple:
+    """构造午间/收盘「日内汇总」邮件（标题固定带“日内汇总”，不复用实时卖出参考模板）。"""
+    label = "上午" if session == "am" else "全天"
+    stamp = now.strftime("%Y-%m-%d %H:%M")
+    rows = [row for row in (buy_today or []) if isinstance(row, dict)]
+    count = len(rows)
+    subject = f"[日内汇总] 主力吸筹候选 {count} 只（{label}） - {stamp}"
+
+    def _line(row: dict) -> str:
+        code = str(row.get("code") or "").zfill(6)
+        name = str(row.get("name") or "")
+        ratio = row.get("main_inflow_ratio")
+        net = row.get("main_net_inflow")
+        ratio_text = "--" if ratio is None else f"{float(ratio):+.2f}%"
+        net_text = "--" if net is None else f"{float(net) / 1e8:.2f}亿"
+        return f"{code} {name} 占比{ratio_text} 主力{net_text}"
+
+    if rows:
+        text = f"主力资金日内汇总（{label}，共 {count} 只吸筹候选）\n\n" + "\n".join(_line(row) for row in rows) + "\n"
+    else:
+        text = f"主力资金日内汇总（{label}）\n\n暂无吸筹候选\n"
+
+    def _html_row(row: dict) -> str:
+        code = html.escape(str(row.get("code") or "").zfill(6))
+        name = html.escape(str(row.get("name") or ""))
+        ratio = row.get("main_inflow_ratio")
+        net = row.get("main_net_inflow")
+        ratio_html = html.escape("--" if ratio is None else "{:+.2f}%".format(float(ratio)))
+        net_html = html.escape("--" if net is None else "{:.2f}亿".format(float(net) / 1e8))
+        return (
+            "<tr><td>" + code + "</td><td>" + name + "</td><td>" + ratio_html +
+            "</td><td>" + net_html + "</td></tr>"
+        )
+
+    html_rows = "".join(_html_row(row) for row in rows) or '<tr><td colspan="4">暂无吸筹候选</td></tr>'
+    html_content = (
+        "<!DOCTYPE html><html><body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:20px;color:#111827'>"
+        f"<h2>主力资金日内汇总（{label}）</h2>"
+        "<table style='width:100%;border-collapse:collapse' border='1' cellspacing='0' cellpadding='8'>"
+        "<tr style='background:#fef2f2'><th>代码</th><th>名称</th><th>占比</th><th>主力净流入</th></tr>"
+        f"{html_rows}</table></body></html>"
+    )
+    return subject, text, html_content
```

## backend/plugins/principal_capital/router.py

```diff
diff --git a/backend/plugins/principal_capital/router.py b/backend/plugins/principal_capital/router.py
index 6b9e788..d20b908 100644
--- a/backend/plugins/principal_capital/router.py
+++ b/backend/plugins/principal_capital/router.py
@@ -3,6 +3,8 @@
 挂载点: /api/v1/principal-capital (由 backend.main 注入)
 """
 import logging
+import time
+from datetime import datetime, timedelta, timezone
 
 from fastapi import APIRouter, BackgroundTasks, Query
 
@@ -12,24 +14,47 @@ from .service import (
     read_report_resilient,
     read_source_health_resilient,
     run_principal_capital_scan,
-    write_report,
+    write_manual_status,
 )
+from .config import SHADOW_REPORT_FILE
 
 router = APIRouter()
 logger = logging.getLogger(__name__)
 
+BEIJING_TZ = timezone(timedelta(hours=8))
+_MANUAL_LOCK = False
+_LAST_MANUAL_TRIGGER_AT = 0.0
+_MANUAL_MIN_INTERVAL_SECONDS = 60
+
 
 def _run_scan_task(**kwargs):
+    global _MANUAL_LOCK
+    started_at = datetime.now(BEIJING_TZ).isoformat()
+    execution_mode = kwargs.get("execution_mode", "shadow")
     try:
-        run_principal_capital_scan(**kwargs)
+        result = run_principal_capital_scan(**kwargs)
+        # P1-R8：manual status 写入真实终态，不长期停留在 running
+        write_manual_status({
+            "status": result.get("status") or "completed",
+            "batch_id": result.get("batch_id"),
+            "execution_mode": execution_mode,
+            "started_at": started_at,
+            "finished_at": datetime.now(BEIJING_TZ).isoformat(),
+            "reason": result.get("reason") or "",
+            "shadow_report_path": str(SHADOW_REPORT_FILE) if execution_mode == "shadow" else None,
+        })
     except Exception as exc:
         logger.exception("主力资金后台任务异常: %s", exc)
-        write_report({
+        write_manual_status({
             "status": "error",
-            "error": str(exc),
-            "buy_triggered": [],
-            "sell_triggered": [],
+            "execution_mode": execution_mode,
+            "started_at": started_at,
+            "finished_at": datetime.now(BEIJING_TZ).isoformat(),
+            "reason": str(exc),
+            "shadow_report_path": None,
         })
+    finally:
+        _MANUAL_LOCK = False
 
 
 @router.post("/trigger")
@@ -41,13 +66,25 @@ async def trigger_principal_capital(
     dry_run: bool = Query(False),
     force: bool = Query(False),
     enable_verify: bool = Query(False),
+    execution_mode: str = Query("shadow"),
 ):
+    global _MANUAL_LOCK, _LAST_MANUAL_TRIGGER_AT
     try:
-        write_report({
+        # P0-6：API 手工触发默认 shadow/readonly；禁止路由写 official latest。
+        if execution_mode not in ("shadow", "readonly"):
+            return {"status": "error", "error": "manual trigger 仅允许 shadow/readonly"}
+        now_monotonic = time.monotonic()
+        if _MANUAL_LOCK:
+            return {"status": "busy", "error": "已有手动扫描在执行中"}
+        if _LAST_MANUAL_TRIGGER_AT and (now_monotonic - _LAST_MANUAL_TRIGGER_AT) < _MANUAL_MIN_INTERVAL_SECONDS:
+            return {"status": "rate_limited", "error": "手动扫描频率过高，请稍后再试"}
+        _MANUAL_LOCK = True
+        _LAST_MANUAL_TRIGGER_AT = now_monotonic
+        write_manual_status({
             "status": "running",
             "message": "主力资金扫描任务已启动",
-            "buy_triggered": [],
-            "sell_triggered": [],
+            "execution_mode": execution_mode,
+            "started_at": datetime.now(BEIJING_TZ).isoformat(),
         })
         background_tasks.add_task(
             _run_scan_task,
@@ -57,9 +94,12 @@ async def trigger_principal_capital(
             dry_run=dry_run,
             force=force,
             enable_verify=enable_verify,
+            execution_mode=execution_mode,
+            owner_id=None,
         )
-        return {"status": "started", "message": "主力资金扫描任务已启动"}
+        return {"status": "started", "message": "主力资金扫描任务已启动（shadow/readonly）"}
     except Exception as exc:
+        _MANUAL_LOCK = False
         return {"status": "error", "error": str(exc)}
```

## backend/plugins/principal_capital/service.py

```diff
diff --git a/backend/plugins/principal_capital/service.py b/backend/plugins/principal_capital/service.py
index 2b42169..b5f3699 100644
--- a/backend/plugins/principal_capital/service.py
+++ b/backend/plugins/principal_capital/service.py
@@ -8,6 +8,8 @@ import html
 import json
 import logging
 import math
+import time
+import uuid
 from datetime import date, datetime, timedelta, timezone
 from pathlib import Path
 from typing import Dict, Optional, Tuple
@@ -19,16 +21,29 @@ import pandas as pd
 from backend.api.router_system import trading_session_status
 
 # === plugin 内部依赖 ===
+from . import intraday_state as intraday
+from . import pipeline as pipeline_mod
 from .config import (
     CONFIG,
     DATA_DIR,
     HISTORY_FILE,
+    INTRADAY_STATE_FILE,
+    M5_AUDIT_FILE,
+    M5_AUDIT_MAX_RECORDS,
+    MANUAL_STATUS_FILE,
+    OWNER_CONFLICT_FILE,
     REPORT_DIR,
     REPORT_FILE,
+    SHADOW_REPORT_FILE,
+    SHADOW_STATE_FILE,
     SNAPSHOT_RAW_BASE,
     SOURCE_HEALTH_FILE,
+    atomic_write_json,
+    config_fingerprint,
+    resolve_execution_mode,
+    resolve_pipeline_mode,
 )
-from .notifier import get_smtp_config, send_email
+from .notifier import build_summary_payload, get_smtp_config, send_email
 from .sources.multi_source import fetch_market_fund_flow_resilient
 
 logger = logging.getLogger(__name__)
@@ -109,12 +124,8 @@ def load_notified_map(today: date, direction: str) -> Dict[str, list]:
 
 
 def save_notified_map(today: date, direction: str, notified_map: Dict[str, list]) -> None:
-    DATA_DIR.mkdir(parents=True, exist_ok=True)
     payload = {"date": today.isoformat(), "notified": notified_map}
-    _notified_file(today, direction).write_text(
-        json.dumps(payload, ensure_ascii=False, indent=2),
-        encoding="utf-8",
-    )
+    atomic_write_json(_notified_file(today, direction), payload)
 
 
 def cleanup_old_notified(today: date, keep_days: int = 7) -> None:
@@ -156,15 +167,17 @@ def _base_filter(df: pd.DataFrame, exclude_star: bool = True, min_amount: float
     result["name"] = result["name"].fillna("").astype(str)
     result["main_inflow_ratio"] = pd.to_numeric(result["main_inflow_ratio"], errors="coerce")
     result["total_amount"] = pd.to_numeric(result["total_amount"], errors="coerce")
-    result = result[result["code"].map(_is_main_board)]
-    result = result[~result["code"].map(_is_excluded_market)]
+    # 合并为单次布尔掩码，避免空 DataFrame 上逐次布尔过滤触发 pandas 空掩码取列误判。
+    # 过滤条件（板块/ST/成交额/阈值）与之前完全一致，仅改变应用方式。
+    keep = result["code"].map(_is_main_board)
+    keep &= ~result["code"].map(_is_excluded_market)
     if exclude_star:
-        result = result[~result["code"].map(_is_star_market)]
-    result = result[~result["name"].map(_is_st_name)]
-    result = result[result["main_inflow_ratio"].notna()]
+        keep &= ~result["code"].map(_is_star_market)
+    keep &= ~result["name"].map(_is_st_name)
+    keep &= result["main_inflow_ratio"].notna()
     amount_mask = result["total_amount"].isna() | (result["total_amount"] >= float(min_amount))
-    result = result[amount_mask]
-    return result.reset_index(drop=True)
+    keep &= amount_mask
+    return result[keep].reset_index(drop=True)
 
 
 def filter_buy_candidates(df, buy_threshold=50.0, exclude_star=True, min_amount=1e7) -> pd.DataFrame:
@@ -360,11 +373,7 @@ def build_email_payload(
 # ---- 报告落盘 ----
 
 def write_report(payload: dict) -> None:
-    REPORT_DIR.mkdir(parents=True, exist_ok=True)
-    REPORT_FILE.write_text(
-        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str),
-        encoding="utf-8",
-    )
+    atomic_write_json(REPORT_FILE, payload)
 
 
 def read_report() -> dict:
@@ -418,11 +427,44 @@ def _normalize_report_payload(payload: Optional[dict]) -> dict:
     return normalized
 
 
+def _check_report_consistency(report: dict) -> dict:
+    """P1-7：读取组合数据时核验 state.last_batch_id == report.batch_id。"""
+    # P1-R4：所有带 batch_id 的 official 报告都执行一致性检查
+    if not report or not report.get("batch_id"):
+        return report
+    try:
+        state = intraday.load_state(trade_date=report.get("trade_date"))
+        problems = []
+        if state.get("schema_version") != 2:
+            problems.append("state_schema_incompatible")
+        if state.get("_source_unavailable") or state.get("warming_reason") == "corrupt_state_recovered":
+            problems.append("state_unavailable_or_recovering")
+        last_batch_id = state.get("last_batch_id")
+        if not last_batch_id:
+            problems.append("state_last_batch_id_empty")
+        elif last_batch_id != report.get("batch_id"):
+            problems.append("state_report_batch_mismatch")
+        if report.get("trade_date") and state.get("trade_date") and report.get("trade_date") != state.get("trade_date"):
+            problems.append("state_report_trade_date_mismatch")
+        if problems:
+            report = dict(report)
+            report["consistency_error"] = True
+            report["consistency_problems"] = problems
+            report["status"] = "degraded"
+            report.setdefault("reason", "state_report_inconsistent")
+    except Exception:
+        pass
+    return report
+
+
 def read_report_resilient() -> dict:
-    """读最新报告：本地完成态优先，否则返回远程快照的真实状态。"""
+    """读最新报告：本地带 batch_id 的报告优先（含一致性核验），否则回退远程快照。
+
+    远程快照不与本地 state 组合做一致性判定（避免把无关 runner 的 state 与快照混合）。
+    """
     local = _normalize_report_payload(read_report())
-    if local.get("status") == "completed":
-        return local
+    if local.get("batch_id"):
+        return _check_report_consistency(local)
     remote = _fetch_snapshot_json("principal_capital_latest.json")
     if remote and remote.get("status"):
         remote = _normalize_report_payload(remote)
@@ -487,10 +529,7 @@ def append_history(result: dict, max_records: int = 1000) -> None:
             "amount": item.get("total_amount"),
             "change_pct": item.get("change_pct"),
         })
-    HISTORY_FILE.write_text(
-        json.dumps({"records": records[-max_records:]}, ensure_ascii=False, indent=2, default=str),
-        encoding="utf-8",
-    )
+    atomic_write_json(HISTORY_FILE, {"records": records[-max_records:]})
 
 
 # ---- 主流程 ----
@@ -517,11 +556,28 @@ def _update_notified_map(notified_map: Dict[str, list], df: pd.DataFrame,
 
 
 def _empty_result(status: str, reason: str, now: datetime, buy_threshold: float,
-                  sell_threshold: float, source_status: Optional[dict] = None) -> dict:
+                  sell_threshold: float, source_status: Optional[dict] = None,
+                  execution_mode: str = "readonly", pipeline_mode: str = "strict",
+                  owner_id: Optional[str] = None) -> dict:
     return {
+        "schema_version": 2,
         "status": status,
         "reason": reason,
+        "trade_date": now.date().isoformat(),
         "now": now.isoformat(),
+        "batch_id": None,
+        "owner_id": owner_id,
+        "execution_mode": execution_mode,
+        "pipeline_mode": pipeline_mode,
+        "deadline_met": None,
+        "quality": {"status": "provisional", "notify_eligible": False, "degraded_reasons": []},
+        "universe": {"source": "none", "count": 0, "stale": False},
+        "bulk": {"status": "not_run", "rows": 0, "main_board_rows": 0, "admitted": False,
+                 "candidate_count": 0, "latency_ms": None},
+        "refine": {"status": "not_run", "requested_count": 0, "received_count": 0,
+                   "missing_codes": [], "coverage_ratio": None, "latency_ms": None},
+        "audit": {"kind": "shadow_truth", "truth_buy_count": 0, "truth_sell_count": 0,
+                  "false_negative_buy": [], "false_negative_sell": []},
         "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
         "source_status": source_status or {"active_source": "none"},
         "scanned": 0,
@@ -529,7 +585,232 @@ def _empty_result(status: str, reason: str, now: datetime, buy_threshold: float,
         "buy_fresh_count": 0, "sell_fresh_count": 0,
         "email_sent": False, "email_error": None,
         "buy_triggered": [], "sell_triggered": [],
+        "buy_candidates_current": [], "sell_candidates_current": [],
+        "buy_candidates_today": [], "sell_candidates_today": [],
+    }
+
+
+def _evaluate_quality(source_status: dict, coverage_ratio, has_source_time: bool) -> dict:
+    """行级质量门控（P1-6）：按覆盖率 + 源时间契约判定，不按供应商名称放行。"""
+    active = (source_status or {}).get("active_source", "none")
+    stale = bool((source_status or {}).get("is_stale", False))
+    if active == "cache" or stale:
+        return {"status": "degraded", "notify_eligible": False, "degraded_reasons": ["stale_cache"]}
+    if coverage_ratio is not None and coverage_ratio < 1.0:
+        return {"status": "degraded", "notify_eligible": False, "degraded_reasons": ["incomplete_coverage"]}
+    if has_source_time:
+        return {"status": "accepted", "notify_eligible": True, "degraded_reasons": []}
+    return {
+        "status": "provisional",
+        "notify_eligible": bool(CONFIG["allow_provisional_notify"]),
+        "degraded_reasons": ["fund_source_time_unavailable"],
+    }
+
+
+def _determine_status(coverage_ratio, quality: dict, source_status: dict) -> str:
+    """P0-2：completed 只允许完整覆盖且质量门禁通过；否则 partial/degraded。"""
+    if (source_status or {}).get("active_source") == "cache" or (source_status or {}).get("is_stale"):
+        return "degraded"
+    if quality.get("status") == "degraded":
+        return "degraded"
+    if coverage_ratio is None or coverage_ratio < 1.0:
+        return "partial"
+    return "completed"
+
+
+def _radar_pool_cfg() -> dict:
+    """读取 smart_money_radar 的池参数（懒导入，避免循环依赖）。"""
+    try:
+        from backend.plugins.smart_money_radar.config import CONFIG as RADAR_CONFIG
+        keys = (
+            "radar_pool_max", "radar_pool_min_dwell_min", "radar_pool_protected_cap",
+            "radar_pool_rotation_seats", "radar_pool_max_stale_min",
+        )
+        return {key: RADAR_CONFIG[key] for key in keys}
+    except Exception:
+        return {}
+
+
+def write_shadow_report(payload: dict) -> None:
+    atomic_write_json(SHADOW_REPORT_FILE, payload)
+
+
+def write_manual_status(payload: dict) -> None:
+    atomic_write_json(MANUAL_STATUS_FILE, payload)
+
+
+def write_owner_conflict_diagnostic(payload: dict) -> None:
+    atomic_write_json(OWNER_CONFLICT_FILE, payload)
+
+
+def read_intraday_state() -> dict:
+    """读取日内状态（供雷达/前端/诊断复用）。"""
+    return intraday.load_state()
+
+
+def _fetch_strict_truth(now) -> tuple:
+    """P0-1：strict 真值固定为完整 sina_full 同批数据。
+
+    抓取前生成权威主板 universe（requested_codes），返回后计算 received/missing/coverage。
+    返回 (df, source_status, truth_meta)。
+    """
+    from .sources.sina import fetch_codes_fund_flow_sina_detailed
+    from .sources.sina_market import fetch_main_board_universe, get_last_codes_stale_date
+
+    universe = fetch_main_board_universe()
+    requested = [str(code).zfill(6) for code in universe.get("codes") or []]
+    stale_date = get_last_codes_stale_date()
+    rows, rejected = fetch_codes_fund_flow_sina_detailed(
+        requested,
+        max_workers=int(CONFIG["sina_max_workers"]),
+        timeout=int(CONFIG["refine_timeout_seconds"]),
+        batch_timeout=float(CONFIG["round_deadline_seconds"]),
+        now=now,
+    )
+    received_codes = sorted({str(row.get("code") or "").zfill(6) for row in rows if row.get("code")})
+    missing = sorted(set(requested) - set(received_codes))
+    coverage = round(len(received_codes) / len(requested), 6) if requested else 0.0
+    df = pd.DataFrame(rows) if rows else pd.DataFrame()
+    universe_verified = bool(universe.get("verified"))
+    source_status = {
+        "active_source": "sina_full",
+        "is_stale": False,
+        "codes_stale_date": stale_date,
+        "invalid_rows": len(rejected),
+        "universe_verified": universe_verified,
+    }
+    truth = {
+        "source": "sina_full",
+        "requested_codes": requested,
+        "received_codes": received_codes,
+        "missing_codes": missing,
+        "coverage_ratio": coverage,
+        "rejected_rows": rejected,
+        "has_source_time": False,  # 新浪单股无 source_time
+        "universe_verified": universe_verified,
+        "universe_meta": universe,
+        "valid_for_admission": bool(rows and not missing and not rejected and universe_verified),
+    }
+    return df, source_status, truth
+
+
+def _fetch_truth_with_fallback(now, enable_verify) -> tuple:
+    """strict 主路径；sina_full 失败才走备用源并标 strict_fallback（不计入 M5）。"""
+    df, source_status, truth = _fetch_strict_truth(now)
+    if df is not None and not df.empty:
+        return df, source_status, truth, "strict"
+    df, source_status = fetch_market_fund_flow_resilient(enable_verify=enable_verify)
+    truth = {
+        "source": (source_status or {}).get("active_source", "none"),
+        "requested_codes": [],
+        "received_codes": [str(code).zfill(6) for code in df["code"].tolist()] if not df.empty else [],
+        "missing_codes": [],
+        "coverage_ratio": None,
+        "rejected_rows": {},
+        "has_source_time": False,
+        "valid_for_admission": False,
     }
+    return df, source_status, truth, "strict_fallback"
+
+
+def _record_m5_audit(result: dict, truth: dict, elapsed_seconds: float) -> None:
+    """P1-3：保存有界的每日 M5 审计记录（可逐轮回放；只有 valid_for_admission 计入验收）。"""
+    records = []
+    if M5_AUDIT_FILE.exists():
+        try:
+            payload = json.loads(M5_AUDIT_FILE.read_text(encoding="utf-8"))
+            records = payload.get("records") or []
+        except (json.JSONDecodeError, OSError):
+            records = []
+    audit = result.get("audit") or {}
+    record = {
+        "batch_id": result.get("batch_id"),
+        "trade_date": result.get("trade_date"),
+        "session": result.get("session"),
+        "now": result.get("now"),
+        "truth_source": truth.get("source"),
+        "universe_count": len(truth.get("requested_codes") or []),
+        "requested_count": len(truth.get("requested_codes") or []),
+        "received_count": len(truth.get("received_codes") or []),
+        "coverage_ratio": truth.get("coverage_ratio"),
+        "config_fingerprint": config_fingerprint(),
+        "thresholds": result.get("thresholds"),
+        "candidate_summary": {
+            "buy": result.get("buy_candidates"),
+            "sell": result.get("sell_candidates"),
+        },
+        "false_negative_buy": audit.get("false_negative_buy", []),
+        "false_negative_sell": audit.get("false_negative_sell", []),
+        "latency_ms": int(elapsed_seconds * 1000),
+        "deadline_met": result.get("deadline_met"),
+        "valid_for_admission": bool(result.get("round_valid")),
+    }
+    records.append(record)
+    atomic_write_json(M5_AUDIT_FILE, {"records": records[-M5_AUDIT_MAX_RECORDS:]})
+
+
+def _run_bulk_shadow(now, df, universe_codes, state, buy_threshold, sell_threshold, exclude_star):
+    """strict 全量结果作为真值，对 bulk 漏斗做零额外精算请求的影子比较。
+
+    返回 (bulk_meta, audit, next_audit_cursor)。
+    """
+    bulk_meta = {
+        "status": "shadow_only", "rows": 0, "main_board_rows": 0, "admitted": bool(CONFIG["bulk_admitted"]),
+        "candidate_count": 0, "latency_ms": None, "validation": None, "error": None,
+    }
+    audit = {
+        "kind": "shadow_truth", "truth_buy_count": 0, "truth_sell_count": 0,
+        "false_negative_buy": [], "false_negative_sell": [],
+        "false_positive_buy": [], "false_positive_sell": [],
+        "valid_for_admission": False,
+    }
+    next_cursor = int((state or {}).get("audit_cursor", 0))
+    try:
+        from .sources.sina_market import fetch_bulk_fund_flow, parse_bulk_rows, validate_bulk_rows
+        payload, latency_ms = fetch_bulk_fund_flow()
+        rows = parse_bulk_rows(payload, now)
+        universe = {_stock_code(code) for code in (universe_codes or [])}
+        validation = validate_bulk_rows(rows, universe)
+        bulk_meta["rows"] = len(rows)
+        bulk_meta["main_board_rows"] = len([row for row in rows if row["code"] in universe])
+        bulk_meta["latency_ms"] = latency_ms
+        bulk_meta["validation"] = validation
+
+        candidates = (state or {}).get("candidates") or {}
+        previous_current = [key.split(":", 1)[1] for key, entry in candidates.items()
+                            if ":" in key and entry.get("is_current")]
+        dwell_codes = list((state or {}).get("pool_entries") or {})
+        # 补集审计只从「universe - 粗筛(不含 audit)」取样
+        coarse_without = pipeline_mod.build_coarse_union(
+            rows, previous_current, dwell_codes, [], CONFIG
+        )["codes"]
+        audit_codes, next_cursor = pipeline_mod.complement_audit_codes(
+            universe, coarse_without, (state or {}).get("audit_cursor", 0), CONFIG
+        )
+        coarse = pipeline_mod.build_coarse_union(rows, previous_current, dwell_codes, audit_codes, CONFIG)
+        bulk_meta["candidate_count"] = len(coarse["codes"])
+        bulk_meta["audit_codes"] = audit_codes
+        audit = pipeline_mod.compare_with_truth(coarse["codes"], df, {
+            "buy": lambda frame: filter_buy_candidates(frame, buy_threshold=buy_threshold, exclude_star=exclude_star),
+            "sell": lambda frame: filter_sell_candidates(frame, sell_threshold=sell_threshold, exclude_star=exclude_star),
+        })
+    except Exception as exc:  # noqa: BLE001 bulk shadow 失败不影响 strict 正式结果
+        logger.info("bulk shadow 比较失败: %s", exc)
+        bulk_meta["status"] = "shadow_error"
+        bulk_meta["error"] = f"{type(exc).__name__}: {exc}"
+    return bulk_meta, audit, next_cursor
+
+
+def _status_reason(status: str, coverage_ratio, deadline_met: bool) -> str:
+    if status == "completed":
+        return ""
+    if status == "partial":
+        if not deadline_met:
+            return "deadline_exceeded"
+        return f"coverage_incomplete:{coverage_ratio}"
+    if status == "degraded":
+        return "degraded_quality"
+    return status
 
 
 def run_principal_capital_scan(
@@ -541,63 +822,250 @@ def run_principal_capital_scan(
     enable_verify: bool = False,
     dry_run: bool = False,
     force: bool = False,
+    execution_mode: Optional[str] = None,
+    pipeline_mode: Optional[str] = None,
+    owner_id: Optional[str] = None,
+    enable_shadow: Optional[bool] = None,
+    batch_id: Optional[str] = None,
 ) -> dict:
-    """执行主力资金双向扫描。"""
+    """执行主力资金双向扫描（23 v2：strict=sina_full 同批真值 + bulk shadow 对照）。
+
+    - execution_mode 默认 readonly；official 是唯一写者。
+    - hybrid 在 M6 前代码层拒绝（resolve_pipeline_mode 抛 RuntimeError）。
+    """
     now = now or datetime.now(BEIJING_TZ)
-    if now.tzinfo is None:
-        now = now.replace(tzinfo=BEIJING_TZ)
+    now = intraday._ensure_aware(now)  # naive datetime 直接抛 ValueError（P1-1）
+    execution_mode = resolve_execution_mode(execution_mode)
+    requested_pipeline_mode = (pipeline_mode or CONFIG["pipeline_mode"]).strip().lower()
+    effective_pipeline_mode = resolve_pipeline_mode(requested_pipeline_mode)  # hybrid -> RuntimeError
+    owner_id = owner_id or CONFIG["official_owner"]
+
+    if execution_mode == "official" and not owner_id:
+        result = _empty_result("owner_conflict", "PC_OFFICIAL_OWNER 未配置", now,
+                               buy_threshold, sell_threshold, execution_mode=execution_mode,
+                               pipeline_mode=effective_pipeline_mode)
+        write_owner_conflict_diagnostic(result)  # P1-4：冲突方不得写 official 报告
+        return result
+
     session = trading_session_status(now)
     if not force and not session.get("is_trading_hours"):
-        result = _empty_result(
-            "skipped", session.get("market_status_text", "非交易时段"),
-            now, buy_threshold, sell_threshold,
-        )
-        write_report(result)
+        result = _empty_result("skipped", session.get("market_status_text", "非交易时段"),
+                               now, buy_threshold, sell_threshold, execution_mode=execution_mode,
+                               pipeline_mode=effective_pipeline_mode, owner_id=owner_id)
+        if execution_mode == "official":
+            write_report(result)
         return result
 
     today = now.date()
-    cleanup_old_notified(today)
-    buy_map = load_notified_map(today, DIRECTION_BUY)
-    sell_map = load_notified_map(today, DIRECTION_SELL)
+    # P1-2：shadow 使用独立状态；readonly 不读旧 official 状态，基于本轮结果自洽
+    if execution_mode == "official":
+        state = intraday.load_state(trade_date=today.isoformat(), now=now)
+    elif execution_mode == "shadow":
+        state = intraday.load_state(path=SHADOW_STATE_FILE, trade_date=today.isoformat(), now=now)
+    else:
+        state = intraday.empty_state(today.isoformat(), pipeline_mode=effective_pipeline_mode)
 
-    df, source_status = fetch_market_fund_flow_resilient(enable_verify=enable_verify)
+    round_ctx = pipeline_mod.build_round_context(
+        owner_id=owner_id, execution_mode=execution_mode, pipeline_mode=effective_pipeline_mode,
+        trade_date=today.isoformat(), started_at=now,
+        deadline_at=now + timedelta(seconds=int(CONFIG["round_deadline_seconds"])),
+    )
+    if batch_id:
+        round_ctx["batch_id"] = batch_id
+    batch_id = round_ctx["batch_id"]
+    started_monotonic = time.monotonic()
+    session_label = "am" if now.hour < 12 else "pm"
+
+    if execution_mode == "official":
+        ok, state, conflict = intraday.acquire_owner_atomic(
+            INTRADAY_STATE_FILE, owner_id, int(CONFIG["owner_lease_seconds"]), now
+        )
+        if not ok:
+            result = _empty_result("owner_conflict", conflict or "owner_conflict", now,
+                                   buy_threshold, sell_threshold, execution_mode=execution_mode,
+                                   pipeline_mode=effective_pipeline_mode, owner_id=owner_id)
+            write_owner_conflict_diagnostic(result)
+            return result
+        cleanup_old_notified(today)
+        buy_map = load_notified_map(today, DIRECTION_BUY)
+        sell_map = load_notified_map(today, DIRECTION_SELL)
+    else:
+        buy_map, sell_map = {}, {}
+
+    is_replay = (state.get("last_batch_id") == batch_id)
+
+    df, source_status, truth, effective_pipeline_mode = _fetch_truth_with_fallback(now, enable_verify)
     if df.empty:
-        result = _empty_result("no_data", "", now, buy_threshold, sell_threshold, source_status)
-        write_report(result)
+        result = _empty_result("no_data", "", now, buy_threshold, sell_threshold, source_status,
+                               execution_mode=execution_mode, pipeline_mode=effective_pipeline_mode,
+                               owner_id=owner_id)
+        if execution_mode == "official":
+            write_report(result)
         return result
 
+    requested = truth.get("requested_codes") or []
+    received = truth.get("received_codes") or []
+    missing = truth.get("missing_codes") or []
+    coverage_ratio = truth.get("coverage_ratio")
+    rejected_rows = truth.get("rejected_rows") or {}
+    has_source_time = bool(truth.get("has_source_time", False))
+    quality = _evaluate_quality(source_status, coverage_ratio, has_source_time)
+
     buy_cands = filter_buy_candidates(df, buy_threshold=buy_threshold, exclude_star=exclude_star)
     sell_cands = filter_sell_candidates(df, sell_threshold=sell_threshold, exclude_star=exclude_star)
+
+    truth_latency_ms = int((time.monotonic() - started_monotonic) * 1000)
+    if enable_shadow is None:
+        enable_shadow = execution_mode in ("official", "shadow")
+    bulk_meta, audit, next_audit_cursor = None, None, None
+    if enable_shadow:
+        bulk_meta, audit, next_audit_cursor = _run_bulk_shadow(
+            now, df, requested, state, buy_threshold, sell_threshold, exclude_star
+        )
+    bulk_latency_ms = int((time.monotonic() - started_monotonic) * 1000) - truth_latency_ms
+    # P1-R1：deadline 在 truth + bulk 结束后计算（含 bulk 耗时），并统一用于 status/fallback/M5
+    elapsed_seconds = time.monotonic() - started_monotonic
+    deadline_met = elapsed_seconds <= float(CONFIG["round_deadline_seconds"])
+
     buy_fresh = _fresh_rows(buy_cands, DIRECTION_BUY, now, buy_map, CONFIG["buy_dedup_minutes"])
     sell_fresh = _fresh_rows(sell_cands, DIRECTION_SELL, now, sell_map, sell_cooldown_minutes)
 
     email_sent = False
     email_error = None
-    # 降级：买入区不再单独触发邮件（仅作雷达数据源写入 latest.json），
-    # 仅当有卖出/派发信号时才发邮件，且邮件正文不含买入区（include_buy=False）
-    if not sell_fresh.empty and not dry_run:
+    notify_eligible = execution_mode == "official" and quality["notify_eligible"]
+    if notify_eligible and not sell_fresh.empty and not dry_run:
         subject, text, html_content = build_email_payload(
             buy_fresh, sell_fresh, now, source_status, include_buy=False
         )
         email_sent, email_error = send_email(subject, text, html_content, get_smtp_config())
 
-    # 买入去重独立于邮件：买入不再发邮件但仍写入 latest.json 供雷达消费，
-    # 当日去重需照常保存，否则同一只票每轮都会重复进 buy_triggered。
-    if dry_run or not buy_fresh.empty:
-        buy_map = _update_notified_map(buy_map, buy_fresh, now)
-        save_notified_map(today, DIRECTION_BUY, buy_map)
-    # 卖出去重绑定邮件发送成功（保持原冷却语义：发出去才算已通知）
-    if dry_run or email_sent:
-        sell_map = _update_notified_map(sell_map, sell_fresh, now)
-        save_notified_map(today, DIRECTION_SELL, sell_map)
+    status = _determine_status(coverage_ratio, quality, source_status)
+    batch_is_partial = status != "completed"
+
+    buy_records = buy_cands.to_dict("records")
+    sell_records = sell_cands.to_dict("records")
+    batch_meta = {
+        "batch_id": batch_id, "now": now.isoformat(),
+        "is_partial": batch_is_partial, "pipeline_mode": effective_pipeline_mode,
+    }
+
+    auto_fallback = None
+    features = {}
+    sentinel_label = None
+    if execution_mode in ("official", "shadow"):
+        state["candidates"] = intraday.merge_candidate_state(state, buy_records, sell_records, batch_meta)
+        state["last_batch_id"] = batch_id
+        state["pipeline_mode"] = effective_pipeline_mode
+        if next_audit_cursor is not None and not is_replay:
+            state["audit_cursor"] = next_audit_cursor
+
+        # P1-R5：sentinel 接入主路径（strict 全量即完整真值；标记 done 防重复）
+        sentinel_label = intraday.should_run_sentinel(state, now, CONFIG["sentinel_times"])
+        if sentinel_label:
+            state = intraday.mark_sentinel_done(state, sentinel_label)
+
+        pool_codes = set((state.get("pool_entries") or {}).keys())
+        fund_codes = {_stock_code(row.get("code")) for row in buy_records + sell_records} | pool_codes
+        fund_batch_meta = {
+            "batch_id": batch_id, "observed_at": now.isoformat(),
+            "source_segment": f"{source_status.get('active_source', 'sina_single')}:v1:{today.isoformat()}",
+            "trade_date": today.isoformat(), "is_partial": batch_is_partial,
+            "is_stale": bool(source_status.get("is_stale", False)),
+            "is_cache": source_status.get("active_source") == "cache",
+        }
+        refined_by_code = {_stock_code(row["code"]): row for _, row in df.iterrows()}
+        for code in fund_codes:
+            row = refined_by_code.get(code)
+            if row is None:
+                continue
+            state["fund_series"] = intraday.append_fund_observation(
+                state.get("fund_series") or {}, row, fund_batch_meta, CONFIG
+            )
+            features[code] = intraday.compute_intraday_features(
+                state["fund_series"].get(code), now, CONFIG
+            )
+        state["features"] = features
+        for _key, entry in state.get("candidates", {}).items():
+            code = _key.split(":", 1)[1] if ":" in _key else ""
+            if code in features:
+                entry.setdefault("latest_metrics", {})["features"] = features[code]
+
+        state["pool_entries"] = intraday.select_radar_pool(
+            state.get("pool_entries"), buy_records, now, _radar_pool_cfg()
+        )
+        # P1-R2：选池后统一回填 features，protected 与新成员都保留
+        for code, entry in (state.get("pool_entries") or {}).items():
+            if code in features:
+                entry.setdefault("latest_metrics", {})["features"] = features[code]
+
+        # P1-R5：auto_fallback 在 save_state 之前赋值并持久化
+        bulk_validation = bulk_meta.get("validation") if bulk_meta else None
+        fallback_decision = pipeline_mod.evaluate_auto_fallback(audit, bulk_validation, coverage_ratio, deadline_met)
+        auto_fallback = fallback_decision["reasons"] if fallback_decision["should_fallback"] else None
+        if auto_fallback:
+            state["auto_fallback"] = "strict_auto_fallback"
+
+        intraday.save_state(state)
+
+        if dry_run or not buy_fresh.empty:
+            buy_map = _update_notified_map(buy_map, buy_fresh, now)
+            save_notified_map(today, DIRECTION_BUY, buy_map)
+        if dry_run or email_sent:
+            sell_map = _update_notified_map(sell_map, sell_fresh, now)
+            save_notified_map(today, DIRECTION_SELL, sell_map)
+    else:
+        # readonly：基于本轮结果构造自洽列表，不持久化
+        state["candidates"] = intraday.merge_candidate_state({"candidates": {}}, buy_records, sell_records, batch_meta)
+
+    processing_latency_ms = int((time.monotonic() - started_monotonic) * 1000) - truth_latency_ms - bulk_latency_ms
+
+    # P0-R1：M5 单轮准入由完整条件计算，不得复制 truth 标记
+    round_valid = pipeline_mod.compute_round_valid(truth, bulk_meta, audit, deadline_met, truth.get("universe_verified"))
+    if audit is not None:
+        audit["valid_for_admission"] = round_valid
+
+    buy_current, sell_current, buy_today, sell_today = intraday.current_candidate_lists(state)
 
     result = {
-        "status": "completed",
-        "reason": "",
+        "schema_version": 2,
+        "status": status,
+        "reason": _status_reason(status, coverage_ratio, deadline_met),
+        "trade_date": today.isoformat(),
+        "session": session_label,
+        "round_valid": round_valid,
+        "sentinel_label": sentinel_label,
+        "latencies": {"truth_ms": truth_latency_ms, "bulk_ms": bulk_latency_ms, "processing_ms": processing_latency_ms},
+        "batch_id": batch_id,
+        "owner_id": owner_id,
+        "execution_mode": execution_mode,
+        "pipeline_mode": effective_pipeline_mode,
+        "requested_pipeline_mode": requested_pipeline_mode,
         "now": now.isoformat(),
+        "deadline_met": deadline_met,
+        "quality": quality,
+        "universe": {"source": truth.get("source", "none"), "count": len(requested),
+                     "stale": bool(source_status.get("is_stale") or source_status.get("codes_stale_date")),
+                     "verified": bool(truth.get("universe_verified")),
+                     "meta": truth.get("universe_meta")},
+        "bulk": bulk_meta or {"status": "not_run", "rows": 0, "main_board_rows": 0,
+                              "admitted": False, "candidate_count": 0, "latency_ms": None},
+        "refine": {
+            "status": "complete" if coverage_ratio == 1.0 else "partial",
+            "requested_count": len(requested),
+            "received_count": len(received),
+            "missing_codes": missing[:100],
+            "missing_count": len(missing),
+            "coverage_ratio": coverage_ratio,
+            "latency_ms": int(elapsed_seconds * 1000),
+            "invalid_rows": len(rejected_rows),
+            "rejected_rows": dict(list(rejected_rows.items())[:100]),
+        },
+        "audit": audit or {"kind": "shadow_truth", "truth_buy_count": 0, "truth_sell_count": 0,
+                           "false_negative_buy": [], "false_negative_sell": [],
+                           "valid_for_admission": False},
         "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
         "source_status": source_status,
-        "scanned": int(len(df)),
+        "scanned": len(received),
         "buy_candidates": int(len(buy_cands)),
         "sell_candidates": int(len(sell_cands)),
         "buy_fresh_count": int(len(buy_fresh)),
@@ -606,9 +1074,112 @@ def run_principal_capital_scan(
         "email_error": email_error,
         "buy_triggered": buy_fresh.to_dict("records"),
         "sell_triggered": sell_fresh.to_dict("records"),
+        "buy_candidates_current": buy_current,
+        "sell_candidates_current": sell_current,
+        "buy_candidates_today": buy_today,
+        "sell_candidates_today": sell_today,
+        "features": state.get("features", {}),
+        "auto_fallback": auto_fallback,
+    }
+    if execution_mode == "official":
+        write_report(result)
+        append_history(result)
+        if not is_replay:
+            _record_m5_audit(result, truth, elapsed_seconds)
+    elif execution_mode == "shadow":
+        write_shadow_report(result)
+    return result
+
+
+def finalize_principal_capital_session(
+    session: str,
+    now: Optional[datetime] = None,
+    execution_mode: Optional[str] = None,
+    owner_id: Optional[str] = None,
+    manual_retry: bool = False,
+    retry_operator: Optional[str] = None,
+) -> dict:
+    """午间/收盘摘要 finalizer（P0-R3）。
+
+    - 状态机：not_attempted / pending / sent / explicit_failed / delivery_unknown。
+    - pending+attempt_id 重启后解释为 delivery_unknown，禁止自动重发。
+    - explicit_failed 仅允许显式 manual_retry 重试（带操作者审计）。
+    """
+    now = now or datetime.now(BEIJING_TZ)
+    now = intraday._ensure_aware(now)
+    execution_mode = resolve_execution_mode(execution_mode)
+    if session not in ("am", "pm"):
+        raise ValueError(f"非法 session: {session!r}")
+    state = intraday.load_state(trade_date=now.date().isoformat(), now=now)
+    latest = read_report()
+
+    result = {
+        "status": "skipped", "session": session, "now": now.isoformat(),
+        "reason": "", "skipped_reason": None, "email_sent": False, "email_error": None,
     }
-    write_report(result)
-    append_history(result)
+
+    if execution_mode == "official":
+        owner_id = owner_id or CONFIG["official_owner"]
+        if not owner_id or (state.get("owner_id") or "") != owner_id:
+            result.update({"status": "owner_conflict", "reason": "owner_mismatch"})
+            return result
+        expires = intraday._parse_dt(state.get("owner_lease_expires_at"))
+        if expires is None or expires <= now:
+            result.update({"status": "owner_conflict", "reason": "owner_lease_expired"})
+            return result
+
+    decision = intraday.should_finalize_session(state, session, latest, now, CONFIG)
+    result["reason"] = decision["reason"]
+    result["skipped_reason"] = decision.get("skipped_reason")
+
+    # P0-R3：explicit_failed 仅允许显式手工重试
+    allow_send = decision["should_send"]
+    if decision["reason"] == "explicit_failed" and manual_retry:
+        allow_send = True
+        result["manual_retry"] = True
+        result["retry_operator"] = retry_operator or "manual"
+
+    if not allow_send:
+        if execution_mode == "official":
+            state = intraday.release_owner(state)
+            intraday.save_state(state)
+        return result
+
+    _buy_current, _sell_current, buy_today, _sell_today = intraday.current_candidate_lists(state)
+    subject, text, html_content = build_summary_payload(buy_today, session, now)
+    if execution_mode != "official":
+        result.update({"status": "constructed", "subject": subject, "text": text})
+        return result
+
+    previous_entry = ((state.get("summary_state") or {}).get(session) or {})
+    attempt_id = uuid.uuid4().hex
+    state = intraday.mark_summary_pending(state, session, now.isoformat(), attempt_id)
+    if previous_entry.get("attempt_id"):
+        # 记录原 attempt_id 便于审计（手工重试场景）
+        entry = dict(state["summary_state"].get(session) or {})
+        entry["previous_attempt_id"] = previous_entry.get("attempt_id")
+        entry["retry_operator"] = result.get("retry_operator")
+        state["summary_state"][session] = entry
+    intraday.save_state(state)
+    try:
+        ok, error = send_email(subject, text, html_content, get_smtp_config())
+    except Exception as exc:  # noqa: BLE001 结果不明 -> delivery_unknown，禁止自动重发
+        state = intraday.mark_summary_delivery_unknown(state, session, now.isoformat())
+        state = intraday.release_owner(state)
+        intraday.save_state(state)
+        result.update({"status": "delivery_unknown", "email_error": f"{type(exc).__name__}: {exc}"})
+        return result
+
+    result["email_sent"] = bool(ok)
+    result["email_error"] = error
+    if ok:
+        state = intraday.mark_summary_sent(state, session, now.isoformat())
+        result["status"] = "sent"
+    else:
+        state = intraday.mark_summary_explicit_failed(state, session, now.isoformat())
+        result["status"] = "send_failed"
+    state = intraday.release_owner(state)
+    intraday.save_state(state)
     return result
```

## backend/plugins/principal_capital/sources/sina.py

```diff
diff --git a/backend/plugins/principal_capital/sources/sina.py b/backend/plugins/principal_capital/sources/sina.py
index 147eb19..7b7901c 100644
--- a/backend/plugins/principal_capital/sources/sina.py
+++ b/backend/plugins/principal_capital/sources/sina.py
@@ -1,46 +1,110 @@
-"""新浪单股主力资金流，用于抽样核验或小批量查询。"""
+"""新浪单股主力资金流（23 v2：数据契约校验 + 真实墙钟预算）。
+
+P0-7：必需字段缺失/非法时拒绝该行并记录原因，不得归零伪装有效零流入。
+P0-8：batch_timeout 是真实墙钟上限；超时取消未完成任务并 shutdown(wait=False, cancel_futures=True)。
+"""
 import logging
-from concurrent.futures import (
-    ThreadPoolExecutor,
-    TimeoutError as FuturesTimeoutError,
-    as_completed,
-)
-from typing import List, Optional
+import math
+import time
+from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
+from datetime import datetime, timedelta, timezone
+from typing import List, Optional, Tuple
 
 import requests
 
 logger = logging.getLogger(__name__)
 
+BEIJING_TZ = timezone(timedelta(hours=8))
+
 SINA_URL = (
     "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
     "MoneyFlow.ssi_ssfx_flzjtj"
 )
+SINA_ENDPOINT = "MoneyFlow.ssi_ssfx_flzjtj"
 SINA_HEADERS = {
     "User-Agent": "Mozilla/5.0",
     "Referer": "https://finance.sina.com.cn/",
 }
 
+# 必需资金字段：任一缺失/非法即拒绝该行（total_amount 与 main_net 依赖全部 8 个字段）
+_REQUIRED_FIELDS = ("r0_in", "r0_out", "r1_in", "r1_out", "r2_in", "r2_out", "r3_in", "r3_out")
+
+
+def _now_beijing(now: Optional[datetime] = None) -> datetime:
+    if now is None:
+        return datetime.now(BEIJING_TZ)
+    if now.tzinfo is None:
+        return now.replace(tzinfo=BEIJING_TZ)
+    return now
+
 
 def _prefix(code: str) -> str:
     return "sh" if str(code).startswith(("6", "9")) else "sz"
 
 
 def _to_float(value):
+    """非法输入（None/空串/-/NaN/Inf）返回 None，不得归零。"""
     if value is None or value == "" or value == "-":
-        return 0.0
+        return None
     try:
-        return float(str(value).replace(",", ""))
+        number = float(str(value).replace(",", ""))
     except (TypeError, ValueError):
-        return 0.0
+        return None
+    return number if math.isfinite(number) else None
 
 
-def fetch_single_stock_fund_flow_sina(
-    code: str,
-    timeout: int = 8,
-    session: Optional[requests.Session] = None,
-) -> Optional[dict]:
-    """查询单只股票的新浪主力资金流。"""
-    http = session or requests.Session()
+def _parse_sina_flow_item(item, code: str, fetched_at: datetime):
+    """解析单股响应为数据契约行。返回 (row, rejection_reason)。"""
+    if not isinstance(item, dict):
+        return None, "non_dict_response"
+    values = {}
+    missing = []
+    for field in _REQUIRED_FIELDS:
+        value = _to_float(item.get(field))
+        if value is None:
+            missing.append(field)
+        else:
+            values[field] = value
+    if missing:
+        return None, "missing_required_field:" + ",".join(sorted(missing))
+
+    super_net = values["r0_in"] - values["r0_out"]
+    big_net = values["r1_in"] - values["r1_out"]
+    mid_net = values["r2_in"] - values["r2_out"]
+    small_net = values["r3_in"] - values["r3_out"]
+    main_net = super_net + big_net
+    total_amount = sum(values[field] for field in _REQUIRED_FIELDS)
+    ratio = (main_net / total_amount * 100) if total_amount > 0 else None
+
+    price = _to_float(item.get("trade"))
+    changeratio = _to_float(item.get("changeratio"))
+    change_pct = round(changeratio * 100, 4) if changeratio is not None else None
+
+    row = {
+        "code": str(code).zfill(6),
+        "name": str(item.get("name") or "").strip(),
+        "price": price,
+        "change_pct": change_pct,
+        "total_amount": round(total_amount, 2),
+        "main_net_inflow": round(main_net, 2),
+        "main_inflow_ratio": None if ratio is None else round(ratio, 4),
+        "super_net": round(super_net, 2),
+        "big_net": round(big_net, 2),
+        "mid_net": round(mid_net, 2),
+        "small_net": round(small_net, 2),
+        "source": "sina_single",
+        "endpoint": SINA_ENDPOINT,
+        "fetched_at": fetched_at.isoformat(),
+        "source_time": None,
+        "freshness_basis": "observation_time",
+        "quality_status": "provisional",
+    }
+    return row, None
+
+
+def _fetch_checked(code: str, timeout: int, now: datetime, session=None) -> Tuple[Optional[dict], Optional[str]]:
+    """单股查询，返回 (row, rejection_reason)。网络/解析/字段缺失统一给结构化原因。"""
+    http = session or requests
     try:
         response = http.get(
             SINA_URL,
@@ -50,48 +114,102 @@ def fetch_single_stock_fund_flow_sina(
         )
         response.raise_for_status()
         data = response.json()
-        # 新浪单股查询返回 dict，逗号批量查询返回 list；两种都要接受。
-        # （历史 bug：旧代码只认 list，导致单股查询恒返回 None）
         if isinstance(data, list):
-            item = (data[0] if data else None)
+            item = data[0] if data else None
         elif isinstance(data, dict):
             item = data
         else:
             item = None
         if not item:
+            return None, "empty_response"
+        return _parse_sina_flow_item(item, code, now)
+    except Exception as exc:  # noqa: BLE001 单股失败原因结构化返回
+        return None, f"{type(exc).__name__}"
+
+
+def fetch_single_stock_fund_flow_sina(
+    code: str,
+    timeout: int = 8,
+    session: Optional[requests.Session] = None,
+    now: Optional[datetime] = None,
+) -> Optional[dict]:
+    """查询单只股票新浪主力资金流。返回 dict 或 None（失败/必需字段缺失）。"""
+    row, _reason = _fetch_checked(code, timeout, _now_beijing(now), session=session)
+    return row
+
+
+def _run_fetch_batch(codes, max_workers, timeout, batch_timeout, now, worker_fn):
+    """有界提交 + 单调 deadline 的并发抓取。返回 (rows, rejected)。"""
+    rows: List[dict] = []
+    rejected: dict = {}
+    if not codes:
+        return rows, rejected
+
+    fetched_at = _now_beijing(now)
+    deadline = time.monotonic() + float(batch_timeout)
+    executor = ThreadPoolExecutor(max_workers=max_workers)
+    pending = {}
+    codes_iter = iter(codes)
+    window = max(1, max_workers * 2)
+
+    def _submit(code):
+        remaining = deadline - time.monotonic()
+        if remaining <= 0:
+            rejected[code] = "batch_timeout"
             return None
-        r0_in = _to_float(item.get("r0_in"))
-        r0_out = _to_float(item.get("r0_out"))
-        r1_in = _to_float(item.get("r1_in"))
-        r1_out = _to_float(item.get("r1_out"))
-        r2_in = _to_float(item.get("r2_in"))
-        r2_out = _to_float(item.get("r2_out"))
-        r3_in = _to_float(item.get("r3_in"))
-        r3_out = _to_float(item.get("r3_out"))
-        super_net = r0_in - r0_out
-        big_net = r1_in - r1_out
-        mid_net = r2_in - r2_out
-        small_net = r3_in - r3_out
-        main_net = super_net + big_net
-        total_amount = r0_in + r0_out + r1_in + r1_out + r2_in + r2_out + r3_in + r3_out
-        ratio = (main_net / total_amount * 100) if total_amount > 0 else None
-        return {
-            "code": str(code).zfill(6),
-            "name": str(item.get("name", "")).strip(),
-            "price": _to_float(item.get("trade")),
-            "change_pct": round(_to_float(item.get("changeratio")) * 100, 4),
-            "total_amount": round(total_amount, 2),
-            "main_net_inflow": round(main_net, 2),
-            "main_inflow_ratio": None if ratio is None else round(ratio, 4),
-            "super_net": round(super_net, 2),
-            "big_net": round(big_net, 2),
-            "mid_net": round(mid_net, 2),
-            "small_net": round(small_net, 2),
-            "source": "sina",
-        }
-    except Exception as exc:
-        logger.debug("新浪单股主力资金失败 %s: %s", code, exc)
-        return None
+        single_timeout = min(float(timeout), max(0.1, remaining))
+        future = executor.submit(worker_fn, code, single_timeout, fetched_at)
+        pending[future] = code
+        return future
+
+    try:
+        for _ in range(window):
+            code = next(codes_iter, None)
+            if code is None:
+                break
+            _submit(code)
+
+        while pending:
+            remaining = deadline - time.monotonic()
+            if remaining <= 0:
+                for future, code in list(pending.items()):
+                    rejected.setdefault(code, "batch_timeout")
+                    future.cancel()
+                break
+            done, _not_done = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
+            if not done:
+                for future, code in list(pending.items()):
+                    rejected.setdefault(code, "batch_timeout")
+                    future.cancel()
+                break
+            for future in done:
+                code = pending.pop(future)
+                try:
+                    row, reason = future.result()
+                except Exception as exc:  # noqa: BLE001
+                    row, reason = None, f"{type(exc).__name__}"
+                if row is not None:
+                    rows.append(row)
+                else:
+                    rejected[code] = reason or "unknown_error"
+                # 补一个待提交槽位（有界窗口）
+                next_code = next(codes_iter, None)
+                if next_code is not None:
+                    _submit(next_code)
+    finally:
+        executor.shutdown(wait=False, cancel_futures=True)
+    return rows, rejected
+
+
+def fetch_codes_fund_flow_sina_detailed(
+    codes: List[str],
+    max_workers: int = 10,
+    timeout: int = 8,
+    batch_timeout: float = 30.0,
+    now: Optional[datetime] = None,
+) -> Tuple[List[dict], dict]:
+    """并发查询，返回 (rows, rejected)。rejected 为 {code: reason}。"""
+    return _run_fetch_batch(codes, max_workers, timeout, batch_timeout, now, _fetch_checked)
 
 
 def fetch_codes_fund_flow_sina(
@@ -99,31 +217,13 @@ def fetch_codes_fund_flow_sina(
     max_workers: int = 10,
     timeout: int = 8,
     batch_timeout: float = 30.0,
+    now: Optional[datetime] = None,
 ) -> List[dict]:
-    """并发查询多只股票的新浪主力资金。
+    """兼容入口：并发查询多只股票，返回成功行列表。"""
 
-    batch_timeout: 整批抓取的墙钟上限，超时则返回已拿到的部分并放弃
-    剩余（新浪本就是抽样核验用途），避免个别慢股把整批 join 拖到分钟级。
-    """
-    results: List[dict] = []
-    if not codes:
-        return results
-    with ThreadPoolExecutor(max_workers=max_workers) as executor:
-        futures = {
-            executor.submit(fetch_single_stock_fund_flow_sina, code, timeout): code
-            for code in codes
-        }
-        try:
-            for future in as_completed(futures, timeout=batch_timeout):
-                try:
-                    item = future.result()
-                except Exception:
-                    continue
-                if item:
-                    results.append(item)
-        except FuturesTimeoutError:
-            logger.warning(
-                "新浪主力资金抽样超出墙钟上限 %ss，返回已拿到的 %d 条",
-                batch_timeout, len(results),
-            )
-    return results
+    def _worker(code, single_timeout, fetched_at):
+        row = fetch_single_stock_fund_flow_sina(code, timeout=single_timeout, now=fetched_at)
+        return row, None
+
+    rows, _rejected = _run_fetch_batch(codes, max_workers, timeout, batch_timeout, now, _worker)
+    return rows
```

## backend/plugins/principal_capital/sources/sina_market.py

```diff
diff --git a/backend/plugins/principal_capital/sources/sina_market.py b/backend/plugins/principal_capital/sources/sina_market.py
index 8f65d3b..f96f19f 100644
--- a/backend/plugins/principal_capital/sources/sina_market.py
+++ b/backend/plugins/principal_capital/sources/sina_market.py
@@ -7,8 +7,10 @@
   - 资金流：MoneyFlow 单股接口并发查询（单股返回可用 code 直接对应，天然不错位；
     逗号批量接口会乱序且不带 code，故不用批量）
 """
+import collections
 import json
 import logging
+import math
 import time
 from datetime import datetime, timedelta, timezone
 from typing import List, Optional, Tuple
@@ -46,6 +48,8 @@ _COLUMNS = [
     "code", "name", "price", "change_pct", "total_amount",
     "main_net_inflow", "main_inflow_ratio", "super_net", "big_net",
     "mid_net", "small_net", "source",
+    # 23 v2 数据契约：单股结果补齐元数据，供质量门控与同源差分复用
+    "endpoint", "fetched_at", "source_time", "freshness_basis", "quality_status",
 ]
 
 
@@ -53,18 +57,18 @@ def _is_main_board_code(code: str) -> bool:
     return str(code or "").zfill(6).startswith(MAIN_BOARD_PREFIXES)
 
 
-def _fetch_main_board_codes_remote(max_pages: int = 80, timeout: int = 18) -> List[str]:
+def _fetch_main_board_codes_remote(max_pages: int = 80, timeout: int = 18):
     """从新浪全A榜单分页翻取沪深主板代码清单（纯网络，无缓存）。
 
-    榜单按涨跌幅排序，主板股散落各页，必须翻完所有页才能取全，
-    因此 max_pages 需覆盖全A股数量（约 5400 只 / 80 每页 ≈ 68 页）。
-
-    跨太平洋抖动下单页偶发超时属常态，因此单页失败重试 1 次；仍失败则
-    跳过该页继续翻（已翻到的页照常累积），避免一页拖垮整份清单。只有全部
-    页都失败（codes 为空）才视为拉取失败，交由上层降级到旧缓存。
+    返回 (codes, meta)。meta 记录 failed_pages / pages_fetched / terminal_page_seen /
+    duplicate_count / verified。终止页之前出现任何缺页 -> verified=false。
     """
     codes: List[str] = []
     seen = set()
+    duplicates = 0
+    failed_pages: List[int] = []
+    pages_fetched = 0
+    terminal_page_seen = False
     session = requests.Session()
     empty_streak = 0
     for page in range(1, max_pages + 1):
@@ -93,22 +97,39 @@ def _fetch_main_board_codes_remote(max_pages: int = 80, timeout: int = 18) -> Li
                 logger.warning("主板清单第 %d 页拉取失败（已重试）：%s", page, exc)
                 data = None
         if data is None:
-            continue  # 该页放弃，继续下一页
+            failed_pages.append(page)
+            continue
+        pages_fetched += 1
         if not data:
             empty_streak += 1
             if empty_streak >= 2:  # 连续空页视为翻到末尾
+                terminal_page_seen = True
                 break
             continue
         empty_streak = 0
         for item in data:
             code = str(item.get("code") or "").zfill(6)
-            if not code or code in seen or not _is_main_board_code(code):
+            if not code:
+                continue
+            if code in seen:
+                duplicates += 1
+                continue
+            if not _is_main_board_code(code):
                 continue
-            codes.append(code)
             seen.add(code)
+            codes.append(code)
         if len(data) < 80:
+            terminal_page_seen = True
             break
-    return codes
+    verified = bool(codes and not failed_pages and terminal_page_seen)
+    meta = {
+        "failed_pages": failed_pages,
+        "pages_fetched": pages_fetched,
+        "terminal_page_seen": terminal_page_seen,
+        "duplicate_count": duplicates,
+        "verified": verified,
+    }
+    return codes, meta
 
 
 def _read_codes_cache(
@@ -138,58 +159,94 @@ def _read_codes_cache(
     return codes, cached_at
 
 
-def _write_codes_cache(codes: List[str]) -> None:
-    DATA_DIR.mkdir(parents=True, exist_ok=True)
-    payload = {"cached_at": datetime.now(BEIJING_TZ).isoformat(), "codes": codes}
-    SINA_CODES_CACHE_FILE.write_text(
-        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
-    )
-
+def _write_codes_cache(codes: List[str], verified: bool = True, cache_version: Optional[str] = None) -> None:
+    from ..config import atomic_write_json
 
-def get_last_codes_stale_date() -> Optional[str]:
-    """返回最近一次 fetch_main_board_codes 若走了过期缓存降级时的清单日期。
+    payload = {
+        "cached_at": datetime.now(BEIJING_TZ).isoformat(),
+        "codes": codes,
+        "verified": bool(verified),
+        "cache_version": cache_version or datetime.now(BEIJING_TZ).strftime("%Y%m%dT%H%M%S"),
+    }
+    atomic_write_json(SINA_CODES_CACHE_FILE, payload)
 
-    命中新鲜缓存或实时拉取成功时为 None。格式 MM-DD，供上层标注清单滞后。
-    """
-    return _last_codes_stale_date
 
+def _read_codes_cache_payload() -> Optional[dict]:
+    if not SINA_CODES_CACHE_FILE.exists():
+        return None
+    try:
+        payload = json.loads(SINA_CODES_CACHE_FILE.read_text(encoding="utf-8"))
+        return payload if isinstance(payload, dict) else None
+    except (json.JSONDecodeError, OSError):
+        return None
 
-def fetch_main_board_codes(
-    max_pages: int = 80, timeout: int = 18, use_cache: bool = True
-) -> List[str]:
-    """取沪深主板代码清单，默认带缓存。
 
-    主板成分变动极慢（仅新股上市增量），缓存 TTL 内直接复用，省去每轮翻页
-    约 25s（美国 IP 实测）。缓存未命中时翻页拉取并落盘。
-    use_cache=False 时强制走网络（供连通性验证等场景）。
+def fetch_main_board_universe(max_pages: int = 80, timeout: int = 18, use_cache: bool = True) -> dict:
+    """P0-R5：取主板 universe，返回 codes + 分页完整性元数据。
 
-    网络拉取返回空时降级读过期缓存（allow_stale）——只要曾成功过一次，就不会
-    因榜单单次抖动而让整条新浪源判空；此时记录缓存日期供上层标注滞后。
+    只有 verified=true 才能写为新鲜权威缓存；中间页失败/未确认终止页必须 verified=false，
+    只能回退上一次经过验证的缓存并记录 cache_version/cached_at。
     """
     global _last_codes_stale_date
     _last_codes_stale_date = None
     ttl = int(CONFIG.get("sina_codes_cache_ttl_seconds", 259200))
+    now = datetime.now(BEIJING_TZ)
+
     if use_cache:
-        cached, _ = _read_codes_cache(ttl)
+        cached, cached_at = _read_codes_cache(ttl)
         if cached:
+            payload = _read_codes_cache_payload() or {}
             logger.info("主板代码清单命中缓存：%d 只", len(cached))
-            return cached
-    codes = _fetch_main_board_codes_remote(max_pages=max_pages, timeout=timeout)
-    if codes:
+            return {
+                "codes": cached,
+                "failed_pages": [],
+                "pages_fetched": None,
+                "terminal_page_seen": True,
+                "duplicate_count": 0,
+                "verified": bool(payload.get("verified", False)),
+                "cache_version": payload.get("cache_version"),
+                "cached_at": cached_at.isoformat() if cached_at else None,
+                "from_cache": True,
+            }
+
+    codes, meta = _fetch_main_board_codes_remote(max_pages=max_pages, timeout=timeout)
+    if codes and meta["verified"]:
         if use_cache:
-            _write_codes_cache(codes)
-        return codes
-    # 实时拉取失败：降级到过期缓存（stale better than none）
+            _write_codes_cache(codes, verified=True)
+        return {**meta, "codes": codes, "cache_version": None, "cached_at": now.isoformat()}
+
+    # 实时拉取未验证：回退上一次经过验证的缓存（stale better than none）
     if use_cache:
         stale_codes, cached_at = _read_codes_cache(ttl, allow_stale=True)
         if stale_codes:
+            payload = _read_codes_cache_payload() or {}
             _last_codes_stale_date = cached_at.strftime("%m-%d") if cached_at else None
-            logger.warning(
-                "主板清单实时拉取失败，降级使用 %s 的旧缓存：%d 只",
-                _last_codes_stale_date, len(stale_codes),
-            )
-            return stale_codes
-    return codes
+            logger.warning("主板清单实时拉取未验证，回退旧缓存：%d 只", len(stale_codes))
+            return {
+                **meta,
+                "codes": stale_codes,
+                "verified": False,
+                "cache_version": payload.get("cache_version"),
+                "cached_at": cached_at.isoformat() if cached_at else None,
+                "fallback_to_cache": True,
+            }
+    return {**meta, "codes": codes, "cache_version": None, "cached_at": None}
+
+
+def get_last_codes_stale_date() -> Optional[str]:
+    """返回最近一次 fetch_main_board_codes 若走了过期缓存降级时的清单日期。
+
+    命中新鲜缓存或实时拉取成功时为 None。格式 MM-DD，供上层标注清单滞后。
+    """
+    return _last_codes_stale_date
+
+
+def fetch_main_board_codes(
+    max_pages: int = 80, timeout: int = 18, use_cache: bool = True
+) -> List[str]:
+    """取沪深主板代码清单（向后兼容：返回 codes 列表）。"""
+    universe = fetch_main_board_universe(max_pages=max_pages, timeout=timeout, use_cache=use_cache)
+    return universe["codes"]
 
 
 def verify_sina_connectivity(list_pages: int = 2, sample_size: int = 30) -> dict:
@@ -315,3 +372,137 @@ def fetch_market_fund_flow_via_sina(
     df.attrs["codes_stale_date"] = codes_stale_date
     logger.info("新浪全主板主力资金流：请求 %d 只，成功 %d 只", len(codes), len(df))
     return df
+
+# --------------------------------------------------------------------------- #
+# 23 v2 bulk 适配器：MoneyFlow.ssl_bkzj_ssggzj 单请求全市场粗筛
+# --------------------------------------------------------------------------- #
+
+SINA_BULK_URL = (
+    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
+    "MoneyFlow.ssl_bkzj_ssggzj"
+)
+SINA_BULK_ENDPOINT = "MoneyFlow.ssl_bkzj_ssggzj"
+
+# bulk 原始字段 -> 标准化后字段。ratioamount / r0_ratio 是「总净占比分数」，
+# 必须在适配器内一次性 *100 标准化成百分数，任何下游不得再二次 *100。
+_BULK_KEY_FIELDS = ("total_amount", "super_net", "super_ratio", "netamount", "ratioamount")
+
+
+def _bulk_float(value):
+    """bulk 数值解析：None/空串/-/NaN/Inf 一律 None，不得变成合法零。"""
+    if value is None or value == "" or value == "-":
+        return None
+    try:
+        number = float(value)
+    except (TypeError, ValueError):
+        return None
+    return number if math.isfinite(number) else None
+
+
+def parse_bulk_rows(payload, fetched_at) -> List[dict]:
+    """把新浪 bulk 响应解析为 BulkRow 列表（无网络）。
+
+    - 只接受 sh/sz 前缀的 symbol，code 唯一标准化。
+    - ratioamount/r0_ratio 标准化为百分数；缺失/非法数值标 degraded，不进粗筛候选。
+    """
+    if not isinstance(payload, list):
+        raise ValueError("bulk 响应必须是列表")
+    rows: List[dict] = []
+    for item in payload:
+        if not isinstance(item, dict):
+            continue
+        symbol = str(item.get("symbol") or "")
+        if symbol[:2] not in ("sh", "sz"):
+            continue
+        code = symbol[2:].zfill(6)
+        if len(code) != 6 or not code.isdigit():
+            continue
+        changeratio = _bulk_float(item.get("changeratio"))
+        change_pct = round(changeratio * 100, 4) if changeratio is not None else None
+        super_ratio_raw = _bulk_float(item.get("r0_ratio"))
+        ratioamount_raw = _bulk_float(item.get("ratioamount"))
+        row = {
+            "code": code,
+            "name": str(item.get("name") or "").strip(),
+            "price": _bulk_float(item.get("trade")),
+            "change_pct": change_pct,
+            "total_amount": _bulk_float(item.get("amount")),
+            "super_net": _bulk_float(item.get("r0_net")),
+            "super_ratio": round(super_ratio_raw * 100, 4) if super_ratio_raw is not None else None,
+            "netamount": _bulk_float(item.get("netamount")),
+            "ratioamount": round(ratioamount_raw * 100, 4) if ratioamount_raw is not None else None,
+            "source": "sina_bulk",
+            "endpoint": SINA_BULK_ENDPOINT,
+            "fetched_at": fetched_at.isoformat() if isinstance(fetched_at, datetime) else str(fetched_at),
+            "source_time": None,
+            "eligible_for": [],
+            "degraded_reasons": ["source_time_unavailable"],
+        }
+        non_finite = [field for field in _BULK_KEY_FIELDS if row[field] is None]
+        if non_finite:
+            row["degraded_reasons"].append(f"non_finite:{','.join(sorted(non_finite))}")
+        else:
+            row["eligible_for"].append("coarse_candidate")
+        rows.append(row)
+    return rows
+
+
+def validate_bulk_rows(rows, universe, known_non_trading=None) -> dict:
+    """整表确定性校验（无网络）。universe 为当日有效主板代码集合。"""
+    known = {str(code).zfill(6): reason for code, reason in (known_non_trading or {}).items()}
+    universe_set = {str(code).zfill(6) for code in (universe or [])}
+    row_codes = [str(row.get("code") or "").zfill(6) for row in rows]
+    unique_codes = set(row_codes)
+    counts = collections.Counter(row_codes)
+    duplicates = sorted(code for code, count in counts.items() if count > 1)
+    missing = sorted(universe_set - unique_codes)
+    unexplained_missing = [code for code in missing if code not in known]
+    extra = sorted(unique_codes - universe_set)
+    non_finite = sorted({
+        row.get("code") for row in rows
+        if any(row.get(field) is None for field in _BULK_KEY_FIELDS)
+    })
+    received_in_universe = sorted(unique_codes & universe_set)
+    reasons = []
+    if duplicates:
+        reasons.append(f"duplicate_code:{len(duplicates)}")
+    if unexplained_missing:
+        reasons.append(f"missing_codes:{len(unexplained_missing)}")
+    if non_finite:
+        reasons.append(f"non_finite:{len(non_finite)}")
+    valid = not duplicates and not unexplained_missing and not non_finite
+    coverage_ratio = round(len(received_in_universe) / len(universe_set), 6) if universe_set else 0.0
+    return {
+        "valid": valid,
+        "universe_count": len(universe_set),
+        "received_count": len(received_in_universe),
+        "coverage_ratio": coverage_ratio,
+        "missing_codes": missing,
+        "missing_reasons": {code: known.get(code, "unexplained") for code in missing},
+        "extra_codes": extra,
+        "duplicate_codes": duplicates,
+        "non_finite_codes": non_finite,
+        "reasons": reasons,
+    }
+
+
+def fetch_bulk_fund_flow(
+    num: int = 8000,
+    timeout: int = 20,
+    session: Optional[requests.Session] = None,
+) -> Tuple[List[dict], int]:
+    """单请求拉取新浪全市场资金流榜单。返回 (原始列表, 延迟毫秒)。"""
+    http = session or requests.Session()
+    start = time.perf_counter()
+    resp = http.get(
+        SINA_BULK_URL,
+        params={"num": str(num)},
+        headers=SINA_NODE_HEADERS,
+        timeout=timeout,
+    )
+    latency_ms = int((time.perf_counter() - start) * 1000)
+    resp.raise_for_status()
+    data = resp.json()
+    if not isinstance(data, list):
+        raise FundFlowFetchError(f"bulk 非列表返回: {str(data)[:120]}")
+    return data, latency_ms
```

## backend/plugins/principal_capital/sources/multi_source.py

```diff
diff --git a/backend/plugins/principal_capital/sources/multi_source.py b/backend/plugins/principal_capital/sources/multi_source.py
index 173dcc2..8477784 100644
--- a/backend/plugins/principal_capital/sources/multi_source.py
+++ b/backend/plugins/principal_capital/sources/multi_source.py
@@ -72,13 +72,11 @@ def _load_health() -> dict:
 
 
 def _save_health() -> None:
-    DATA_DIR.mkdir(parents=True, exist_ok=True)
+    from ..config import atomic_write_json
+
     health = _load_health()
     health["updated_at"] = _now().isoformat()
-    SOURCE_HEALTH_FILE.write_text(
-        json.dumps(health, ensure_ascii=False, indent=2),
-        encoding="utf-8",
-    )
+    atomic_write_json(SOURCE_HEALTH_FILE, health)
 
 
 def _is_blocked(source: str, now: datetime) -> bool:
@@ -120,10 +118,9 @@ def _write_cache(df: pd.DataFrame, fetched_at: datetime) -> None:
         "fetched_at": fetched_at.isoformat(),
         "records": df.to_dict("records"),
     }
-    _CACHE_JSON_FILE.write_text(
-        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
-        encoding="utf-8",
-    )
+    from ..config import atomic_write_json
+
+    atomic_write_json(_CACHE_JSON_FILE, payload)
     try:
         df.assign(_cached_at=fetched_at.isoformat()).to_parquet(CACHE_FILE, index=False)
     except Exception:
```

## backend/plugins/principal_capital/tests/test_service.py

```diff
diff --git a/backend/plugins/principal_capital/tests/test_service.py b/backend/plugins/principal_capital/tests/test_service.py
index ba645a5..9e13f9b 100644
--- a/backend/plugins/principal_capital/tests/test_service.py
+++ b/backend/plugins/principal_capital/tests/test_service.py
@@ -23,6 +23,25 @@ def _row(code, name, ratio, amount=2e8, change_pct=3.0):
     }
 
 
+def _truth(df, source="eastmoney", coverage=1.0, has_source_time=False):
+    codes = list(df["code"].tolist()) if df is not None and not df.empty else []
+    return (
+        df,
+        {"active_source": source, "is_stale": False},
+        {
+            "source": source,
+            "requested_codes": codes,
+            "received_codes": codes,
+            "missing_codes": [],
+            "coverage_ratio": coverage,
+            "rejected_rows": {},
+            "has_source_time": has_source_time,
+            "valid_for_admission": bool(codes and coverage == 1.0),
+        },
+        "strict",
+    )
+
+
 class PrincipalCapitalServiceTest(unittest.TestCase):
 
     def test_read_report_resilient_returns_remote_non_completed_statuses(self):
@@ -43,8 +62,12 @@ class PrincipalCapitalServiceTest(unittest.TestCase):
             self.assertIn("source_status", result)
 
     def test_read_report_resilient_keeps_local_completed_report_first(self):
-        local = {"status": "completed", "now": "2026-08-11T09:40:00+08:00"}
+        local = {"status": "completed", "now": "2026-08-11T09:40:00+08:00",
+                 "batch_id": "b1", "trade_date": "2026-08-11"}
+        state = pcs.intraday.empty_state("2026-08-11")
+        state["last_batch_id"] = "b1"
         with patch.object(pcs, "read_report", return_value=local), \
+             patch.object(pcs.intraday, "load_state", return_value=state), \
              patch.object(pcs, "_fetch_snapshot_json") as fetch_snapshot:
             result = pcs.read_report_resilient()
 
@@ -126,8 +149,12 @@ class PrincipalCapitalServiceTest(unittest.TestCase):
                  patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
-                 patch.object(pcs, "fetch_market_fund_flow_resilient",
-                              return_value=(pd.DataFrame(), {"active_source": "none"})):
+                 patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=(pd.DataFrame(), {"active_source": "none"},
+                                            {"source": "none", "requested_codes": [], "received_codes": [],
+                                             "missing_codes": [], "coverage_ratio": None, "rejected_rows": {},
+                                             "has_source_time": False, "valid_for_admission": False},
+                                            "strict_fallback")):
                 result = pcs.run_principal_capital_scan(
                     now=datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ), force=True)
         self.assertEqual(result["status"], "no_data")
@@ -139,12 +166,20 @@ class PrincipalCapitalServiceTest(unittest.TestCase):
                  patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
-                 patch.object(pcs, "fetch_market_fund_flow_resilient",
-                              return_value=(df, {"active_source": "eastmoney"})), \
-                 patch.object(pcs, "send_email", return_value=(True, None)):
+                 patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5_audit.json"), \
+                 patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=_truth(df)), \
+                 patch.object(pcs, "send_email", return_value=(True, None)), \
+                 patch.object(pcs.intraday, "acquire_owner_atomic",
+                              return_value=(True, pcs.intraday.empty_state("2026-06-29"), None)), \
+                 patch.object(pcs.intraday, "save_state", return_value=None):
                 now = datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ)
-                first = pcs.run_principal_capital_scan(now=now, force=True)
-                second = pcs.run_principal_capital_scan(now=now + timedelta(minutes=5), force=True)
+                first = pcs.run_principal_capital_scan(now=now, force=True,
+                                                       execution_mode="official", owner_id="github_actions",
+                                                       enable_shadow=False)
+                second = pcs.run_principal_capital_scan(now=now + timedelta(minutes=5), force=True,
+                                                        execution_mode="official", owner_id="github_actions",
+                                                        enable_shadow=False)
         self.assertEqual(first["buy_fresh_count"], 2)
         self.assertEqual(second["buy_fresh_count"], 0)
 
@@ -155,15 +190,23 @@ class PrincipalCapitalServiceTest(unittest.TestCase):
                  patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
-                 patch.object(pcs, "fetch_market_fund_flow_resilient",
-                              return_value=(df, {"active_source": "eastmoney"})), \
-                 patch.object(pcs, "send_email", return_value=(True, None)):
+                 patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5_audit.json"), \
+                 patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=_truth(df)), \
+                 patch.dict(pcs.CONFIG, {"allow_provisional_notify": True}), \
+                 patch.object(pcs, "send_email", return_value=(True, None)), \
+                 patch.object(pcs.intraday, "acquire_owner_atomic",
+                              return_value=(True, pcs.intraday.empty_state("2026-06-29"), None)), \
+                 patch.object(pcs.intraday, "save_state", return_value=None):
                 t1 = pcs.run_principal_capital_scan(
-                    now=datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ), force=True)
+                    now=datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ), force=True,
+                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
                 t2 = pcs.run_principal_capital_scan(
-                    now=datetime(2026, 6, 29, 10, 30, tzinfo=BEIJING_TZ), force=True)
+                    now=datetime(2026, 6, 29, 10, 30, tzinfo=BEIJING_TZ), force=True,
+                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
                 t3 = pcs.run_principal_capital_scan(
-                    now=datetime(2026, 6, 29, 11, 5, tzinfo=BEIJING_TZ), force=True)
+                    now=datetime(2026, 6, 29, 11, 5, tzinfo=BEIJING_TZ), force=True,
+                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
         self.assertEqual(t1["sell_fresh_count"], 1)
         self.assertEqual(t2["sell_fresh_count"], 0)
         self.assertEqual(t3["sell_fresh_count"], 1)
```

## backend/plugins/principal_capital/tests/test_sina.py

```diff
diff --git a/backend/plugins/principal_capital/tests/test_sina.py b/backend/plugins/principal_capital/tests/test_sina.py
index ccff0a4..206f48d 100644
--- a/backend/plugins/principal_capital/tests/test_sina.py
+++ b/backend/plugins/principal_capital/tests/test_sina.py
@@ -57,8 +57,8 @@ class SinaFundFlowTest(unittest.TestCase):
         self.assertIsNone(fetch_single_stock_fund_flow_sina("600001", session=session))
 
     def test_batch_query_skips_failed_rows(self):
-        def fake_fetch(code, timeout=8):
-            del timeout
+        def fake_fetch(code, timeout=8, session=None, now=None):
+            del timeout, session, now
             if code in {"600002", "600004"}:
                 return None
             return {"code": code, "name": f"股票{code}", "price": 10,
```

## backend/plugins/principal_capital/tests/test_sina_market.py

```diff
diff --git a/backend/plugins/principal_capital/tests/test_sina_market.py b/backend/plugins/principal_capital/tests/test_sina_market.py
index 6b7146b..c94c910 100644
--- a/backend/plugins/principal_capital/tests/test_sina_market.py
+++ b/backend/plugins/principal_capital/tests/test_sina_market.py
@@ -61,7 +61,9 @@ class SinaMarketTest(unittest.TestCase):
             with patch.object(sm, "SINA_CODES_CACHE_FILE", cache_file), \
                  patch.object(sm, "DATA_DIR", Path(tmp)), \
                  patch.object(sm, "_fetch_main_board_codes_remote",
-                              return_value=["600000", "002415", "000001"]) as remote:
+                              return_value=(["600000", "002415", "000001"],
+                                            {"verified": True, "failed_pages": [], "pages_fetched": 1,
+                                             "terminal_page_seen": True, "duplicate_count": 0})) as remote:
                 codes = sm.fetch_main_board_codes()
             remote.assert_called_once()
             self.assertEqual(codes, ["600000", "002415", "000001"])
@@ -84,8 +86,10 @@ class SinaMarketTest(unittest.TestCase):
                 sm.requests.exceptions.ReadTimeout("t1-retry"),
                 good_resp, good_resp,
             ]
-            codes = sm._fetch_main_board_codes_remote(max_pages=5)
+            codes, meta = sm._fetch_main_board_codes_remote(max_pages=5)
         self.assertEqual(codes, ["000001"])
+        self.assertEqual(meta["failed_pages"], [1])
+        self.assertFalse(meta["verified"])
 
     def test_remote_empty_falls_back_to_stale_cache(self):
         """实时拉取返回空 → 降级用过期缓存，并记录清单日期供上层标注。"""
@@ -96,7 +100,10 @@ class SinaMarketTest(unittest.TestCase):
                 "cached_at": stale.isoformat(), "codes": ["600000", "000001"],
             }), encoding="utf-8")
             with patch.object(sm, "SINA_CODES_CACHE_FILE", cache_file), \
-                 patch.object(sm, "_fetch_main_board_codes_remote", return_value=[]):
+                 patch.object(sm, "_fetch_main_board_codes_remote",
+                              return_value=([], {"verified": False, "failed_pages": [1],
+                                               "pages_fetched": 0, "terminal_page_seen": False,
+                                               "duplicate_count": 0})):
                 codes = sm.fetch_main_board_codes()
             self.assertEqual(codes, ["600000", "000001"])
             self.assertEqual(sm.get_last_codes_stale_date(), stale.strftime("%m-%d"))
@@ -107,7 +114,9 @@ class SinaMarketTest(unittest.TestCase):
             with patch.object(sm, "SINA_CODES_CACHE_FILE", Path(tmp) / "c.json"), \
                  patch.object(sm, "DATA_DIR", Path(tmp)), \
                  patch.object(sm, "_fetch_main_board_codes_remote",
-                              return_value=["600000"]):
+                              return_value=(["600000"], {"verified": True, "failed_pages": [],
+                                             "pages_fetched": 1, "terminal_page_seen": True,
+                                             "duplicate_count": 0})):
                 sm.fetch_main_board_codes()
             self.assertIsNone(sm.get_last_codes_stale_date())
```

## backend/plugins/smart_money_radar/config.py

```diff
diff --git a/backend/plugins/smart_money_radar/config.py b/backend/plugins/smart_money_radar/config.py
index 09a2bd5..5b70112 100644
--- a/backend/plugins/smart_money_radar/config.py
+++ b/backend/plugins/smart_money_radar/config.py
@@ -62,6 +62,12 @@ CONFIG = {
     "pool_keys": _env_csv("POOL_KEYS", "buy_candidates,sell_candidates"),
     "pool_max": _env_int("POOL_MAX", 40),
     "pool_refresh_min": _env_int("POOL_REFRESH_MIN", 10),
+    # 23 v2 雷达池驻留/保护席/轮换席/陈旧期限（与 principal_capital 日内状态池选择共用）
+    "radar_pool_max": _env_int("POOL_MAX", 40),
+    "radar_pool_min_dwell_min": _env_int("POOL_MIN_DWELL_MIN", 30),
+    "radar_pool_protected_cap": _env_int("POOL_PROTECTED_CAP", 30),
+    "radar_pool_rotation_seats": _env_int("POOL_ROTATION_SEATS", 10),
+    "radar_pool_max_stale_min": _env_int("POOL_MAX_STALE_MIN", 15),
     "exclude_gem": _env_bool("EXCLUDE_GEM", True),
     "exclude_star": _env_bool("EXCLUDE_STAR", True),
     "poll_interval_s": _env_int("POLL_INTERVAL_S", 4),
```

## backend/plugins/smart_money_radar/service.py

```diff
diff --git a/backend/plugins/smart_money_radar/service.py b/backend/plugins/smart_money_radar/service.py
index de66d23..511c9ca 100644
--- a/backend/plugins/smart_money_radar/service.py
+++ b/backend/plugins/smart_money_radar/service.py
@@ -181,13 +181,19 @@ def cleanup_old_notified(today: date, keep_days: int = 7) -> None:
 
 
 def _candidate_lists(payload: dict) -> list:
+    """23 v2：雷达池只消费买侧 current 列表；卖侧不混入吸筹观察池。
+
+    P1-5：key 存在且为 list 时必须尊重空列表（权威空）；只有 key 缺失才 legacy 回退。
+    """
+    if "buy_candidates_current" in payload:
+        value = payload.get("buy_candidates_current")
+        return list(value) if isinstance(value, list) else []
     items = []
     for key in CONFIG.get("pool_keys", []):
         value = payload.get(key)
         if isinstance(value, list):
             items.extend(value)
-    # 兼容既有 principal_capital_latest.json：buy_candidates/sell_candidates
-    # 是数量，实际可用的股票条目位于 buy_triggered/sell_triggered。
+    # 兼容旧报告：buy_candidates/sell_candidates 是数量，实际条目在 buy_triggered/sell_triggered
     if not items:
         for key in ("buy_triggered", "sell_triggered"):
             value = payload.get(key)
@@ -196,6 +202,32 @@ def _candidate_lists(payload: dict) -> list:
     return items
 
 
+def _pool_from_intraday_state(now: datetime):
+    """读取 principal_capital 日内状态的池选择结果。
+
+    P1-5：返回 None 表示来源不可用；返回 [] 表示权威空池。
+    """
+    try:
+        from backend.plugins.principal_capital.intraday_state import load_state
+        state = load_state(now=now)
+        if state.get("_source_unavailable") or state.get("warming_reason") == "corrupt_state_recovered":
+            return None
+        if "pool_entries" not in state:
+            return None
+        entries = state.get("pool_entries") or {}
+        items = []
+        for code, entry in entries.items():
+            metrics = dict(entry.get("latest_metrics") or {})
+            item = {"code": _stock_code(code), **metrics}
+            item["_pool_entry"] = entry
+            items.append(item)
+        items.sort(key=lambda item: str(item.get("code") or ""))
+        return items[: int(CONFIG["pool_max"])]
+    except Exception as exc:  # noqa: BLE001 来源不可用
+        logger.info("load_watch_pool: 日内状态池不可用，回退旧字段: %s", exc)
+        return None
+
+
 def _valid_pool_item(item: dict) -> bool:
     code = _stock_code(item.get("code") or item.get("代码"))
     name = str(item.get("name") or item.get("名称") or "")
@@ -216,6 +248,18 @@ def load_watch_pool(force: bool = False, now: Optional[datetime] = None) -> list
     source_file = str(CONFIG["pool_source_file"])
     if not force and expires_at and now < expires_at and _POOL_CACHE.get("source_file") == source_file:
         return list(_POOL_CACHE["items"])
+
+    pool = _pool_from_intraday_state(now)
+    if pool is not None:
+        # 权威空池（[]）也必须尊重，不得回退旧数据
+        items = pool
+        _POOL_CACHE.update({
+            "expires_at": now + timedelta(minutes=CONFIG["pool_refresh_min"]),
+            "items": items,
+            "source_file": source_file,
+        })
+        return items
+
     path = Path(source_file)
     payload = {}
     if path.exists():
```

## frontend/js/plugins/principal_capital.js

```diff
diff --git a/frontend/js/plugins/principal_capital.js b/frontend/js/plugins/principal_capital.js
index 87c3f8c..ac84a6a 100644
--- a/frontend/js/plugins/principal_capital.js
+++ b/frontend/js/plugins/principal_capital.js
@@ -30,8 +30,11 @@ function _pcResolveApiBase() {
     return '/api/v1';
 }
 
-// _pcFetch: 走绝对 URL，避免与主项目 apiFetch 的 API_BASE 双重拼接 /api/v1
+// _pcFetch: 优先复用项目统一 apiFetch()，不可用时回退绝对 URL
 function _pcFetch(path, opts = {}) {
+    if (typeof apiFetch === 'function') {
+        return apiFetch(PC_PATH + path, opts);
+    }
     const url = _pcResolveApiBase() + PC_PATH + path;
     const ms = opts.timeout || 30000;
     const o = { ...opts };
@@ -83,15 +86,23 @@ function _pcFmtTime(iso) {
 
 const PC_STATUS_LABEL = {
     completed: '已扫描',
+    partial: '部分覆盖',
+    degraded: '降级',
     no_data: '数据源失败/无数据',
     skipped: '非交易时段',
     error: '运行异常',
     empty: '未运行',
     running: '运行中...',
+    owner_conflict: '写者冲突',
+    strict_fallback: 'strict 自动回退',
 };
 
 const PC_SOURCE_LABEL = {
-    eastmoney: '东方财富(主)',
+    sina_full: '新浪全量(strict)',
+    sina_single: '新浪单股',
+    sina: '新浪',
+    strict_fallback: 'strict 备用源(降级)',
+    eastmoney: '东方财富(备用)',
     eastmoney_backup: '东方财富(备)',
     akshare: 'akshare',
     cache: '本地缓存(降级)',
@@ -362,8 +373,16 @@ function _pcRenderReport(data) {
     const srcRaw = (data.source_status && data.source_status.active_source) || 'none';
     const srcLabel = (PC_SOURCE_LABEL[srcRaw] || srcRaw) + (stale ? ' (缓存降级)' : '');
     const statusLabel = PC_STATUS_LABEL[data.status] || data.status || '--';
+    const mode = (data.execution_mode || '') + (data.pipeline_mode ? '/' + data.pipeline_mode : '');
+    const triggerNote = ' · 列表为本轮新触发';
+    const quality = data.quality && data.quality.status ? data.quality.status : '';
+    const refine = data.refine || {};
+    const covText = (refine.coverage_ratio === null || refine.coverage_ratio === undefined)
+        ? ''
+        : ` · 覆盖 ${(refine.coverage_ratio * 100).toFixed(1)}%`;
+    const fallback = data.auto_fallback ? ` · 回退:${data.auto_fallback}` : '';
     if (meta) {
-        meta.textContent = `${_pcFmtTime(data.now)} · 扫描 ${data.scanned || 0} 只 · 数据源: ${srcLabel} · 状态: ${statusLabel}`;
+        meta.textContent = `${_pcFmtTime(data.now)} · 扫描 ${data.scanned || 0} 只 · 数据源: ${srcLabel} · 状态: ${statusLabel}${covText}${mode ? ' · ' + mode : ''}${quality ? ' · ' + quality : ''}${fallback}${triggerNote}`;
     }
     document.getElementById('pcBuyCount').textContent = String(buy.length);
     document.getElementById('pcSellCount').textContent = String(sell.length);
```

## scripts/commit_screening_data.sh

```diff
diff --git a/scripts/commit_screening_data.sh b/scripts/commit_screening_data.sh
index 320062a..0ef751c 100755
--- a/scripts/commit_screening_data.sh
+++ b/scripts/commit_screening_data.sh
@@ -51,12 +51,19 @@ trap cleanup EXIT
 
 mkdir -p "${snapshot_dir}/data" "${snapshot_dir}/reports"
 
-for file in data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json; do
+for file in data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/principal_capital_intraday_state.json data/principal_capital_intraday_state_shadow.json data/principal_capital_owner_conflict.json data/principal_capital_m5_audit.json; do
   if [[ -f "${file}" ]]; then
     cp "${file}" "${snapshot_dir}/${file}"
   fi
 done
 
+# 23 v2：通知去重文件按日期落盘，一并进入快照（避免双写/重复通知）
+for nfile in data/principal_capital_*_notified_*.json; do
+  if [[ -f "${nfile}" ]]; then
+    cp "${nfile}" "${snapshot_dir}/${nfile}"
+  fi
+done
+
 if [[ -d reports ]]; then
   cp -R reports/. "${snapshot_dir}/reports/"
   rm -rf "${snapshot_dir}/reports/.cache"  # 22 方案 §2.4 磁盘 TTL 缓存不进 git
@@ -99,7 +106,8 @@ git -C "${data_worktree_dir}" config user.email "github-actions[bot]@users.norep
     fi
   done
 
-  rm -rf data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/snapshot_manifest.json reports
+  rm -rf data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/principal_capital_intraday_state.json data/principal_capital_intraday_state_shadow.json data/principal_capital_owner_conflict.json data/principal_capital_m5_audit.json data/snapshot_manifest.json reports
+  rm -rf data/principal_capital_*_notified_*.json
   mkdir -p data reports
   cp -R "${snapshot_dir}/data/." data/
   cp -R "${snapshot_dir}/reports/." reports/
@@ -108,11 +116,14 @@ git -C "${data_worktree_dir}" config user.email "github-actions[bot]@users.norep
   python3 "${SCRIPT_DIR}/merge_picker_snapshots.py" "${picker_keep_dir}" "$(pwd)" "${PICKER_FILES[@]}"
 
   add_paths=(data/snapshot_manifest.json)
-  for file in data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json reports; do
+  for file in data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/principal_capital_intraday_state.json data/principal_capital_intraday_state_shadow.json data/principal_capital_owner_conflict.json data/principal_capital_m5_audit.json reports; do
     if [[ -e "${file}" ]]; then
       add_paths+=("${file}")
     fi
   done
+  if compgen -G "data/principal_capital_*_notified_*.json" > /dev/null; then
+    add_paths+=(data/principal_capital_*_notified_*.json)
+  fi
   git add -f "${add_paths[@]}"
 
   if git diff --cached --quiet; then
```

## scripts/restore_screening_data.sh

```diff
diff --git a/scripts/restore_screening_data.sh b/scripts/restore_screening_data.sh
index 9a00e59..e1ec88d 100644
--- a/scripts/restore_screening_data.sh
+++ b/scripts/restore_screening_data.sh
@@ -46,3 +46,12 @@ restore_file "reports/data_backend/smart_picker_latest.json"
 restore_file "reports/data_backend/smart_picker_charts_latest.json"
 restore_file "data/principal_capital_source_health.json"
 restore_file "data/principal_capital_sina_codes.json"
+# 23 v2：日内状态（候选/池/同源序列/摘要标记）、bulk shadow 报告、当日通知去重
+today="$(TZ=Asia/Shanghai date +%F)"
+restore_file "data/principal_capital_intraday_state.json"
+restore_file "data/principal_capital_intraday_state_shadow.json"
+restore_file "data/principal_capital_owner_conflict.json"
+restore_file "data/principal_capital_m5_audit.json"
+restore_file "reports/principal_capital_shadow.json"
+restore_file "data/principal_capital_buy_notified_${today}.json"
+restore_file "data/principal_capital_sell_notified_${today}.json"
```

## scripts/verify_fund_flow_leaderboard.py

```diff
diff --git a/scripts/verify_fund_flow_leaderboard.py b/scripts/verify_fund_flow_leaderboard.py
index 7723bf6..5d9b608 100644
--- a/scripts/verify_fund_flow_leaderboard.py
+++ b/scripts/verify_fund_flow_leaderboard.py
@@ -180,8 +180,9 @@ def main():
             continue
         if ratio is not None and ratio <= -SELL_MAIN_RATIO:
             sell_violators.append((code, round(ratio, 2)))
-    report("卖侧圈定线外反证(≤-30% 越界=0)", not sell_violators,
-           f"圈定外随机精算 {sample_n} 只，越界 {len(sell_violators)}: {sell_violators[:5]}")
+    # 23 v2：随机抽样不能证明“全市场不漏”，此处仅作经验烟测（WARN，不作准入证据）
+    report("抽样烟测·卖侧(非准入)", not sell_violators,
+           f"圈定外随机精算 {sample_n} 只，越界 {len(sell_violators)}: {sell_violators[:5]}", hard=False)
     # 反证：圈定外（ratioamount < 粗筛线）是否可能 main≥50%
     outside = [r for r in rows[:3000]
                if str(r.get("symbol") or "")[:2] in ("sh", "sz")
@@ -196,13 +197,14 @@ def main():
                 violators.append((code, ratio))
         except Exception:
             continue
-    report("粗筛不漏(圈定外无 main≥50%)", not violators,
-           f"抽样圈定外 {len(outside)} 只精算，越界 {len(violators)}: {violators[:5]}")
+    report("抽样烟测·买侧(非准入)", not violators,
+           f"抽样圈定外 {len(outside)} 只精算，越界 {len(violators)}: {violators[:5]}", hard=False)
     saved = 1 - (1 + refine_total) / 3195
     report("请求削减", saved > 0.8, f"3195 → 1(bulk) + {refine_total} 精算 = 削减 {saved*100:.0f}%")
 
-    print(f"\n结论: {'全部 PASS' if not any(r[3] and not r[1] for r in RESULTS) else '存在硬性 FAIL'}")
-    return 0 if not any(r[3] and not r[1] for r in RESULTS) else 1
+    hard_failures = [r for r in RESULTS if r[3] and not r[1]]
+    print(f"\n结论: {'无硬性 FAIL（随机抽样仅烟测，不作为准入证据）' if not hard_failures else '存在硬性 FAIL'}")
+    return 0 if not hard_failures else 1
 
 
 if __name__ == "__main__":
```

## scripts/principal_capital_watchdog.py

```diff
diff --git a/scripts/principal_capital_watchdog.py b/scripts/principal_capital_watchdog.py
index 3baf6e0..fe72789 100644
--- a/scripts/principal_capital_watchdog.py
+++ b/scripts/principal_capital_watchdog.py
@@ -17,7 +17,18 @@ from backend.plugins.principal_capital.service import BEIJING_TZ, _fetch_snapsho
 
 ALERT_NOT_STARTED = "not_started"
 ALERT_SOURCE_FAILURE = "source_failure"
+ALERT_OWNER_CONFLICT = "owner_conflict"
+ALERT_DEGRADED = "degraded"
+ALERT_FINALIZER_MISSING = "finalizer_missing"
+ALERT_PENDING_STUCK = "pending_stuck"
+ALERT_DELIVERY_UNKNOWN = "delivery_unknown"
+ALERT_CONSISTENCY_ERROR = "consistency_error"
+ALERT_M5_GAP = "m5_gap"
+ALERT_M5_DAY_INVALID = "m5_day_invalid"
 STATE_FILE = REPORT_DIR / "principal_capital_watchdog_state.json"
+OWNER_CONFLICT_FILE = Path(__file__).resolve().parents[1] / "data" / "principal_capital_owner_conflict.json"
+INTRADAY_STATE_FILE = Path(__file__).resolve().parents[1] / "data" / "principal_capital_intraday_state.json"
+M5_AUDIT_FILE = Path(__file__).resolve().parents[1] / "data" / "principal_capital_m5_audit.json"
 
 
 def _as_beijing_time(value: Any) -> Optional[datetime]:
@@ -48,8 +59,34 @@ def _attempts_text(snapshot: Dict[str, Any]) -> str:
     return "\n".join(lines)
 
 
-def evaluate_snapshot(snapshot: Optional[Dict[str, Any]], now: datetime) -> Optional[Dict[str, str]]:
-    """根据当天快照判断是否需要发送未启动或数据源失败告警。"""
+def _summary_alert(intraday_state: Dict[str, Any], now: datetime) -> Optional[Dict[str, str]]:
+    """根据 summary_state 检查 finalizer 未完成 / pending 卡死 / delivery_unknown。"""
+    summary_state = (intraday_state or {}).get("summary_state") or {}
+    now_hm = now.hour * 100 + now.minute
+    for session, boundary in (("am", 1130), ("pm", 1500)):
+        entry = summary_state.get(session) or {}
+        status = entry.get("status")
+        if now_hm < boundary:
+            continue
+        if status == "delivery_unknown":
+            return {"kind": ALERT_DELIVERY_UNKNOWN, "message": f"{session} 摘要投递结果不明（delivery_unknown），需人工确认。"}
+        if status == "pending":
+            updated = _as_beijing_time(entry.get("updated_at"))
+            if updated is None or (now - updated).total_seconds() > 30 * 60:
+                return {"kind": ALERT_PENDING_STUCK, "message": f"{session} 摘要停留在 pending 超过 30 分钟，疑似卡死。"}
+        if status not in ("sent", "explicit_failed"):
+            return {"kind": ALERT_FINALIZER_MISSING, "message": f"{session} 摘要 finalizer 尚未完成（状态 {status or 'not_attempted'}）。"}
+    return None
+
+
+def evaluate_snapshot(
+    snapshot: Optional[Dict[str, Any]],
+    now: datetime,
+    owner_conflict: Optional[Dict[str, Any]] = None,
+    intraday_state: Optional[Dict[str, Any]] = None,
+    m5_audit: Optional[Dict[str, Any]] = None,
+) -> Optional[Dict[str, str]]:
+    """根据快照 + 独立诊断 + 日内状态 + M5 审计判断是否需要告警。"""
     now = _as_beijing_time(now) or datetime.now(BEIJING_TZ)
     snapshot = snapshot or {}
     snapshot_time = _as_beijing_time(snapshot.get("now"))
@@ -68,6 +105,30 @@ def evaluate_snapshot(snapshot: Optional[Dict[str, Any]], now: datetime) -> Opti
             "message": "主力资金已运行但数据源失败/无数据。\n" + _attempts_text(snapshot),
         }
 
+    if status == "owner_conflict" or (owner_conflict and owner_conflict.get("status") == "owner_conflict"):
+        return {
+            "kind": ALERT_OWNER_CONFLICT,
+            "message": "主力资金出现唯一写者冲突（owner_conflict），正式任务未能写入。",
+        }
+
+    if status in {"partial", "degraded", "consistency_error"}:
+        return {
+            "kind": ALERT_DEGRADED,
+            "message": f"主力资金本轮状态为 {status}（覆盖不足/质量降级/批次不一致），详见最新报告。",
+        }
+
+    # M5 当日审计检查
+    records = (m5_audit or {}).get("records") or []
+    today_records = [r for r in records if r.get("trade_date") == now.date().isoformat()]
+    if today_records:
+        if not any(r.get("valid_for_admission") for r in today_records):
+            return {"kind": ALERT_M5_DAY_INVALID, "message": "今日 M5 审计轮次均无效，本日不计入连续五日。"}
+
+    # finalizer / summary 检查
+    summary_alert = _summary_alert(intraday_state, now)
+    if summary_alert is not None:
+        return summary_alert
+
     if status in {"completed", "skipped"}:
         return None
 
@@ -123,6 +184,16 @@ def _build_email(alert: Dict[str, str], snapshot: Dict[str, Any], now: datetime)
     return subject, text, html_content
 
 
+def _read_local_json(path: Path) -> Optional[Dict[str, Any]]:
+    if not path.exists():
+        return None
+    try:
+        payload = json.loads(path.read_text(encoding="utf-8"))
+        return payload if isinstance(payload, dict) else None
+    except (OSError, json.JSONDecodeError):
+        return None
+
+
 def run_watchdog(
     now: Optional[datetime] = None,
     snapshot_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
@@ -138,7 +209,10 @@ def run_watchdog(
     state_path = Path(state_path or STATE_FILE)
 
     snapshot = snapshot_fetcher("principal_capital_latest.json") or {}
-    alert = evaluate_snapshot(snapshot, now)
+    owner_conflict = _read_local_json(OWNER_CONFLICT_FILE)
+    intraday_state = _read_local_json(INTRADAY_STATE_FILE)
+    m5_audit = _read_local_json(M5_AUDIT_FILE)
+    alert = evaluate_snapshot(snapshot, now, owner_conflict, intraday_state, m5_audit)
     if alert is None:
         return {"status": "ok", "alert_type": None, "email_sent": False}
```

## backend/plugins/principal_capital/intraday_state.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/intraday_state.py b/backend/plugins/principal_capital/intraday_state.py
new file mode 100644
index 0000000..adc9c04
--- /dev/null
+++ b/backend/plugins/principal_capital/intraday_state.py
@@ -0,0 +1,682 @@
+"""23 v2 日内状态模型与纯函数（无网络 / 无文件 I/O 的核心部分）。
+
+状态文件: data/principal_capital_intraday_state.json（唯一临时文件 + os.replace 原子写）。
+文件按交易日重置，只保留当前交易日的候选、池、同源累计序列、摘要标记与审计游标。
+
+本模块拆成两层：
+  - 纯函数（可独立单测）：merge_candidate_state / append_fund_observation /
+    compute_intraday_features / select_radar_pool / should_run_sentinel /
+    should_finalize_session / acquire_owner / reset_state_for_trade_date。
+  - 薄 I/O 层：load_state / save_state。
+"""
+import fcntl
+import json
+import math
+import os
+import uuid
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+from typing import Optional
+
+from .config import CONFIG, INTRADAY_STATE_FILE
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+SCHEMA_VERSION = 2
+
+_DIRECTION_BUY = "buy"
+_DIRECTION_SELL = "sell"
+
+# 候选最新指标中保留的字段（稳定、可 JSON 序列化）。
+_METRIC_KEYS = (
+    "name", "price", "change_pct", "total_amount", "main_net_inflow",
+    "main_inflow_ratio", "super_net", "big_net", "mid_net", "small_net", "source",
+)
+
+
+def _json_safe(value):
+    if isinstance(value, float):
+        return value if math.isfinite(value) else None
+    if isinstance(value, dict):
+        return {k: _json_safe(v) for k, v in value.items()}
+    if isinstance(value, list):
+        return [_json_safe(v) for v in value]
+    return value
+
+
+def _finite(value):
+    if value is None or value == "" or value == "-":
+        return None
+    try:
+        number = float(value)
+    except (TypeError, ValueError):
+        return None
+    return number if math.isfinite(number) else None
+
+
+def _ensure_aware(value: datetime) -> datetime:
+    """v2 契约：naive datetime 直接抛 ValueError，不得自动补时区掩盖调用错误。"""
+    if getattr(value, "tzinfo", None) is None:
+        raise ValueError("datetime 必须带时区（请显式传北京时区）")
+    return value
+
+
+def _parse_dt(value) -> Optional[datetime]:
+    if not value:
+        return None
+    if isinstance(value, datetime):
+        return _ensure_aware(value)
+    try:
+        parsed = datetime.fromisoformat(str(value))
+    except (TypeError, ValueError):
+        return None
+    return _ensure_aware(parsed)
+
+
+def _to_iso(value) -> str:
+    if isinstance(value, datetime):
+        return _ensure_aware(value).isoformat()
+    return str(value)
+
+
+def normalize_code(value) -> str:
+    code = str(value or "").strip().zfill(6)
+    return code if code.isdigit() and len(code) == 6 else ""
+
+
+def empty_state(
+    trade_date: str,
+    owner_id: Optional[str] = None,
+    owner_lease_expires_at: Optional[str] = None,
+    pipeline_mode: str = "strict",
+) -> dict:
+    """返回全新日内状态骨架。"""
+    return {
+        "schema_version": SCHEMA_VERSION,
+        "trade_date": trade_date,
+        "owner_id": owner_id,
+        "owner_lease_expires_at": owner_lease_expires_at,
+        "last_batch_id": None,
+        "pipeline_mode": pipeline_mode,
+        "auto_fallback": None,
+        "summary_state": {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}},
+        "summary_attempted_at": {},
+        "summary_sent_at": {},
+        "audit_cursor": 0,
+        "sentinel_done": [],
+        "candidates": {},
+        "pool_entries": {},
+        "fund_series": {},
+    }
+
+
+def reset_state_for_trade_date(state: Optional[dict], trade_date: str) -> dict:
+    """跨交易日清空候选、序列、摘要标记和审计游标。输入不原地修改。"""
+    if not isinstance(state, dict) or state.get("trade_date") != trade_date:
+        return empty_state(
+            trade_date,
+            pipeline_mode=(state or {}).get("pipeline_mode", "strict"),
+        )
+    result = dict(state)
+    result.setdefault("schema_version", SCHEMA_VERSION)
+    result.setdefault("summary_state", {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}})
+    result.setdefault("summary_attempted_at", {})
+    result.setdefault("summary_sent_at", {})
+    result.setdefault("audit_cursor", 0)
+    result.setdefault("sentinel_done", [])
+    result.setdefault("candidates", {})
+    result.setdefault("pool_entries", {})
+    result.setdefault("fund_series", {})
+    result.setdefault("pipeline_mode", state.get("pipeline_mode", "strict"))
+    result.setdefault("auto_fallback", None)
+    return result
+
+
+# --------------------------------------------------------------------------- #
+# 唯一写者
+# --------------------------------------------------------------------------- #
+
+def acquire_owner(state: dict, owner_id: str, lease_seconds: int, now: datetime) -> tuple:
+    """official 启动时抢占/续租 owner。返回 (ok, new_state, reason)。
+
+    - 无 owner 或同 owner：续租成功。
+    - 已有不同 owner 且租约未过期：owner_conflict，拒绝写入。
+    """
+    now = _ensure_aware(now)
+    result = dict(state)
+    existing = result.get("owner_id")
+    if existing and existing != owner_id:
+        expires = _parse_dt(result.get("owner_lease_expires_at"))
+        if expires and expires > now:
+            return False, result, f"owner_conflict: {existing}"
+    result["owner_id"] = owner_id
+    result["owner_lease_expires_at"] = (now + timedelta(seconds=int(lease_seconds))).isoformat()
+    return True, result, None
+
+
+def release_owner(state: dict) -> dict:
+    """session finalizer 完成后主动释放租约（保留 owner_id 便于审计）。"""
+    result = dict(state)
+    result["owner_lease_expires_at"] = None
+    return result
+
+
+def acquire_owner_atomic(path, owner_id: str, lease_seconds: int, now: datetime) -> tuple:
+    """P0-R4：在文件锁临界区内完成 load → 检查 lease → 写入新 lease。
+
+    返回 (ok, state, reason)。lease 持久化完成后才返回，调用方随后才能开始网络请求。
+    """
+    now = _ensure_aware(now)
+    file_path = _state_path(path)
+    file_path.parent.mkdir(parents=True, exist_ok=True)
+    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
+    with open(lock_path, "a+") as lock_file:
+        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
+        try:
+            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
+            ok, state, reason = acquire_owner(state, owner_id, lease_seconds, now)
+            if ok:
+                save_state(state, path=file_path)
+            return ok, state, reason
+        finally:
+            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
+
+
+def mark_sentinel_done(state: dict, label: str) -> dict:
+    result = dict(state)
+    done = list(result.get("sentinel_done") or [])
+    if label not in done:
+        done.append(label)
+    result["sentinel_done"] = done
+    return result
+
+
+def _set_summary_state(state: dict, session: str, status: str, at_iso: str = None, attempt_id: str = None) -> dict:
+    result = dict(state)
+    default = {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}}
+    summary_state = {s: dict(v) for s, v in (result.get("summary_state") or default).items()}
+    entry = summary_state.setdefault(session, {"status": "pending"})
+    entry["status"] = status
+    if at_iso is not None:
+        entry["updated_at"] = at_iso
+    if attempt_id is not None:
+        entry["attempt_id"] = attempt_id
+    result["summary_state"] = summary_state
+    return result
+
+
+def mark_summary_pending(state: dict, session: str, at_iso: str, attempt_id: str) -> dict:
+    """SMTP 前原子落盘 pending + attempt_id（P0-5 投递状态机）。"""
+    result = _set_summary_state(state, session, "pending", at_iso, attempt_id)
+    attempted = dict(result.get("summary_attempted_at") or {})
+    attempted[session] = at_iso
+    result["summary_attempted_at"] = attempted
+    return result
+
+
+def mark_summary_sent(state: dict, session: str, at_iso: str) -> dict:
+    result = _set_summary_state(state, session, "sent", at_iso)
+    sent_at = dict(result.get("summary_sent_at") or {})
+    sent_at[session] = at_iso
+    result["summary_sent_at"] = sent_at
+    return result
+
+
+def mark_summary_explicit_failed(state: dict, session: str, at_iso: str) -> dict:
+    return _set_summary_state(state, session, "explicit_failed", at_iso)
+
+
+def mark_summary_delivery_unknown(state: dict, session: str, at_iso: str) -> dict:
+    return _set_summary_state(state, session, "delivery_unknown", at_iso)
+
+
+# --------------------------------------------------------------------------- #
+# 纯函数：候选状态合并
+# --------------------------------------------------------------------------- #
+
+def _candidate_metrics(row: dict) -> dict:
+    return _json_safe({key: row.get(key) for key in _METRIC_KEYS})
+
+
+def merge_candidate_state(
+    previous: Optional[dict],
+    current_buy: list,
+    current_sell: list,
+    batch_meta: dict,
+) -> dict:
+    """合并本批候选到当日候选状态。返回新的 candidates 字典（不原地修改）。
+
+    - 首次出现的键 fresh=true；已存在 fresh=false。
+    - 完整批次未命中的键 is_current=false；partial 批次不据此驱逐。
+    - 同 batch_id 重放幂等：不重复增加 seen_rounds。
+    """
+    previous_candidates = (previous or {}).get("candidates") if isinstance(previous, dict) else (previous or {})
+    candidates = {key: dict(entry) for key, entry in (previous_candidates or {}).items()}
+    now_iso = _to_iso(batch_meta.get("now"))
+    batch_id = str(batch_meta.get("batch_id") or uuid.uuid4().hex)
+    is_partial = bool(batch_meta.get("is_partial", False))
+
+    seen = set()
+    for direction, rows in ((_DIRECTION_BUY, current_buy), (_DIRECTION_SELL, current_sell)):
+        for row in rows or []:
+            code = normalize_code(row.get("code"))
+            if not code:
+                continue
+            key = f"{direction}:{code}"
+            seen.add(key)
+            entry = candidates.get(key)
+            if entry is None:
+                candidates[key] = {
+                    "first_seen_at": now_iso,
+                    "last_seen_at": now_iso,
+                    "last_batch_id": batch_id,
+                    "seen_rounds": 1,
+                    "is_current": True,
+                    "latest_metrics": _candidate_metrics(row),
+                    "quality_status": row.get("quality_status", "provisional"),
+                    "fresh": True,
+                }
+                continue
+            # 同 batch 重放幂等
+            if entry.get("last_batch_id") == batch_id:
+                candidates[key] = entry
+                continue
+            entry["last_seen_at"] = now_iso
+            entry["last_batch_id"] = batch_id
+            entry["seen_rounds"] = int(entry.get("seen_rounds", 0)) + 1
+            entry["is_current"] = True
+            entry["latest_metrics"] = _candidate_metrics(row)
+            entry["quality_status"] = row.get("quality_status", entry.get("quality_status", "provisional"))
+            entry["fresh"] = False
+            candidates[key] = entry
+
+    if not is_partial:
+        for key, entry in list(candidates.items()):
+            if key not in seen and entry.get("is_current"):
+                entry["is_current"] = False
+                candidates[key] = entry
+    return candidates
+
+
+def current_candidate_lists(state: Optional[dict]) -> tuple:
+    """从日内状态导出报告用的 current/today 列表（稳定排序）。"""
+    candidates = (state or {}).get("candidates") or {}
+    buy_current, sell_current, buy_today, sell_today = [], [], [], []
+    for key, entry in candidates.items():
+        if ":" not in key:
+            continue
+        direction, code = key.split(":", 1)
+        metrics = dict(entry.get("latest_metrics") or {})
+        row = {"code": code, **metrics}
+        if direction == _DIRECTION_BUY:
+            buy_today.append(dict(row))
+            if entry.get("is_current"):
+                buy_current.append(dict(row))
+        else:
+            sell_today.append(dict(row))
+            if entry.get("is_current"):
+                sell_current.append(dict(row))
+    for lst in (buy_current, sell_current, buy_today, sell_today):
+        lst.sort(key=lambda item: str(item.get("code")))
+    return buy_current, sell_current, buy_today, sell_today
+
+
+# --------------------------------------------------------------------------- #
+# 纯函数：同源日内资金特征
+# --------------------------------------------------------------------------- #
+
+def _obs_eligible(obs: dict) -> bool:
+    if obs.get("partial") or obs.get("stale") or obs.get("cache"):
+        return False
+    return _finite(obs.get("main_net_inflow")) is not None and _finite(obs.get("total_amount")) is not None
+
+
+def append_fund_observation(series: dict, row: dict, batch_meta: dict, cfg: dict) -> dict:
+    """给某 code 追加一条同源累计快照。返回新的 fund_series 字典（不原地修改）。
+
+    按 code + batch_id 幂等：同 batch 重放不重复追加。
+    """
+    result = {code: list(items) for code, items in (series or {}).items()}
+    code = normalize_code(row.get("code"))
+    if not code:
+        return result
+    max_points = int((cfg or {}).get("intraday_max_points", CONFIG["intraday_max_points"]))
+    observation = {
+        "observed_at": _to_iso(batch_meta.get("observed_at") or batch_meta.get("now")),
+        "source_segment": batch_meta.get("source_segment") or f"{row.get('source', 'sina_single')}:v1:{batch_meta.get('trade_date', '')}",
+        "main_net_inflow": _finite(row.get("main_net_inflow")),
+        "total_amount": _finite(row.get("total_amount")),
+        "batch_id": str(batch_meta.get("batch_id") or ""),
+        "partial": bool(batch_meta.get("is_partial", False)),
+        "stale": bool(batch_meta.get("is_stale", False)),
+        "cache": bool(batch_meta.get("is_cache", False)),
+    }
+    items = result.get(code, [])
+    if items and items[-1].get("batch_id") == observation["batch_id"]:
+        return result  # 同 batch 幂等
+    items.append(observation)
+    result[code] = items[-max_points:]
+    return result
+
+
+def _warm(reason: str) -> dict:
+    return {
+        "roll_net_30m": None,
+        "acc_win": 0,
+        "inc_ratio_5m": None,
+        "interval_seconds": None,
+        "warming": True,
+        "warming_reason": reason,
+    }
+
+
+def compute_intraday_features(series: list, now: datetime, cfg: dict = None) -> dict:
+    """对同一 code 的累计快照序列计算 5 分钟增量 / 30 分钟滚动特征。
+
+    只对同源、同交易日、连续、且 total_amount 不回退的相邻快照差分。
+    间隔不在 [2,10] 分钟、切源、累计值回退、跨日均开新段并 warming。
+    """
+    del cfg
+    now = _ensure_aware(now)
+    if not series:
+        return _warm("first_observation")
+    points = []
+    for obs in series:
+        ts = _parse_dt(obs.get("observed_at"))
+        if ts is None or ts.date() != now.date():
+            continue
+        points.append((ts, obs))
+    points.sort(key=lambda item: item[0])
+    if len(points) < 2:
+        return _warm("first_observation")
+
+    pairs = []
+    for index in range(len(points) - 2, -1, -1):
+        prev_ts, prev = points[index]
+        cur_ts, cur = points[index + 1]
+        # invalid/partial/cache/stale 必须中断连续段（不能先过滤后跨越该点配对）
+        if not _obs_eligible(prev) or not _obs_eligible(cur):
+            break
+        if prev.get("source_segment") != cur.get("source_segment"):
+            break  # 切源：开新段，之前的历史不再与本段拼接
+        delta_seconds = (cur_ts - prev_ts).total_seconds()
+        if delta_seconds < 120 or delta_seconds > 600:
+            break
+        prev_total = _finite(prev.get("total_amount"))
+        cur_total = _finite(cur.get("total_amount"))
+        if prev_total is None or cur_total is None or cur_total < prev_total:
+            break  # 累计值回退：开新段
+        pairs.append({
+            "delta_main_net": _finite(cur.get("main_net_inflow")) - _finite(prev.get("main_net_inflow")),
+            "delta_total_amount": cur_total - prev_total,
+            "interval_seconds": delta_seconds,
+            "prev_at": prev_ts,
+            "at": cur_ts,
+        })
+    pairs.reverse()
+    if not pairs:
+        # 无法构成任何有效相邻差分；给出最近一次导致开新段的原因
+        prev_ts, prev = points[-2]
+        cur_ts, cur = points[-1]
+        if not _obs_eligible(prev) or not _obs_eligible(cur):
+            return _warm("batch_partial")
+        if prev.get("source_segment") != cur.get("source_segment"):
+            return _warm("source_changed")
+        delta_seconds = (cur_ts - prev_ts).total_seconds()
+        if delta_seconds > 600:
+            return _warm("gap_too_large")
+        if delta_seconds < 120:
+            return _warm("interval_too_small")
+        if _finite(cur.get("total_amount")) is not None and _finite(prev.get("total_amount")) is not None \
+                and _finite(cur.get("total_amount")) < _finite(prev.get("total_amount")):
+            return _warm("counter_reset")
+        return _warm("first_observation")
+
+    result = {
+        "roll_net_30m": None,
+        "acc_win": 0,
+        "inc_ratio_5m": None,
+        "interval_seconds": None,
+        "warming": False,
+        "warming_reason": None,
+    }
+
+    # acc_win：从最新值向前数连续 delta_main_net > 0 的有效窗口数
+    acc = 0
+    for pair in reversed(pairs):
+        if pair["delta_main_net"] > 0:
+            acc += 1
+        else:
+            break
+    result["acc_win"] = acc
+
+    latest = pairs[-1]
+    if latest["delta_total_amount"] > 0:
+        result["inc_ratio_5m"] = round(latest["delta_main_net"] / latest["delta_total_amount"] * 100, 4)
+        result["interval_seconds"] = int(latest["interval_seconds"])
+
+    cutoff = now - timedelta(minutes=30)
+    recent = [pair for pair in pairs if pair["at"] >= cutoff]
+    if not recent:
+        result["roll_net_30m"] = None
+        result["warming"] = True
+        result["warming_reason"] = "insufficient_coverage"
+        return result
+
+    recent_times = sorted({pair["prev_at"] for pair in recent} | {recent[-1]["at"]})
+    span_seconds = (recent_times[-1] - recent_times[0]).total_seconds()
+    max_gap = max(
+        (recent_times[i + 1] - recent_times[i]).total_seconds()
+        for i in range(len(recent_times) - 1)
+    ) if len(recent_times) > 1 else 0.0
+    if span_seconds < 25 * 60 or max_gap > 600:
+        result["roll_net_30m"] = None
+        result["warming"] = True
+        result["warming_reason"] = "insufficient_coverage"
+        return result
+
+    result["roll_net_30m"] = round(sum(pair["delta_main_net"] for pair in recent), 2)
+    return result
+
+
+# --------------------------------------------------------------------------- #
+# 纯函数：雷达池选择
+# --------------------------------------------------------------------------- #
+
+def _pool_score(entry: dict) -> tuple:
+    metrics = entry.get("latest_metrics") or {}
+    ratio = _finite(metrics.get("main_inflow_ratio")) or 0.0
+    net = _finite(metrics.get("main_net_inflow")) or 0.0
+    return (-ratio, -net, str(entry.get("code") or ""))
+
+
+def _fresh_score(row: dict) -> tuple:
+    ratio = _finite(row.get("main_inflow_ratio")) or 0.0
+    net = _finite(row.get("main_net_inflow")) or 0.0
+    return (-ratio, -net, str(normalize_code(row.get("code")) or ""))
+
+
+def _pool_stale(entry: dict, now: datetime, max_stale_min: int) -> bool:
+    last_seen = _parse_dt(entry.get("last_seen_at"))
+    if last_seen is None:
+        return True
+    return (now - last_seen).total_seconds() > int(max_stale_min) * 60
+
+
+def _pool_in_dwell(entry: dict, now: datetime, min_dwell_min: int) -> bool:
+    dwell_until = _parse_dt(entry.get("dwell_until"))
+    return dwell_until is not None and dwell_until > now
+
+
+def select_radar_pool(previous_pool: Optional[dict], current_buy: list, now: datetime, cfg: dict) -> dict:
+    """从本轮完整 buy_candidates_current 选择吸筹观察池。
+
+    - current_buy=None 表示 partial 批次：不驱逐旧成员，只移除超过最大陈旧期限者。
+    - 完整批次：保留驻留期内的旧成员（上限 protected_cap，且必须给轮换席让路），
+      剩余席位按 fresh 候选稳定排序填入。
+    """
+    now = _ensure_aware(now)
+    cfg = cfg or {}
+    previous = previous_pool or {}
+    max_n = int(cfg.get("radar_pool_max", 40))
+    min_dwell = int(cfg.get("radar_pool_min_dwell_min", 30))
+    protected_cap = int(cfg.get("radar_pool_protected_cap", 30))
+    rotation = int(cfg.get("radar_pool_rotation_seats", 10))
+    max_stale = int(cfg.get("radar_pool_max_stale_min", 15))
+
+    if current_buy is None:
+        return {
+            code: dict(entry)
+            for code, entry in previous.items()
+            if not _pool_stale(entry, now, max_stale)
+        }
+
+    buy_map = {normalize_code(row.get("code")): row for row in current_buy if normalize_code(row.get("code"))}
+    protected = []
+    for code, entry in previous.items():
+        if not _pool_stale(entry, now, max_stale) and _pool_in_dwell(entry, now, min_dwell):
+            protected.append((code, entry))
+    protected.sort(key=lambda item: _pool_score(item[1]))
+
+    keep_protected = min(len(protected), protected_cap, max(0, max_n - rotation))
+    kept = protected[:keep_protected]
+    kept_codes = {code for code, _ in kept}
+
+    fresh = []
+    for code, row in buy_map.items():
+        if code in kept_codes:
+            continue
+        fresh.append((code, row))
+    fresh.sort(key=lambda item: _fresh_score(item[1]))
+
+    remaining = max_n - len(kept)
+    result = {}
+    for code, entry in kept:
+        item = dict(entry)
+        item["selection_reason"] = "protected_dwell"
+        # 驻留成员若本轮仍在候选里，刷新其最新指标供雷达评分
+        if code in buy_map:
+            item["latest_metrics"] = _candidate_metrics(buy_map[code])
+        result[code] = item
+    for code, row in fresh[:remaining]:
+        previous_entry = previous.get(code)
+        result[code] = {
+            "code": code,
+            "entered_at": previous_entry.get("entered_at") if previous_entry else now.isoformat(),
+            "last_seen_at": now.isoformat(),
+            "dwell_until": (now + timedelta(minutes=min_dwell)).isoformat(),
+            "selection_reason": "rotation" if previous_entry else "fresh",
+            "source_batch_id": row.get("_batch_id") or row.get("batch_id"),
+            "latest_metrics": _candidate_metrics(row),
+        }
+    return result
+
+
+# --------------------------------------------------------------------------- #
+# 纯函数：哨兵与 finalizer
+# --------------------------------------------------------------------------- #
+
+def should_run_sentinel(state: dict, now: datetime, sentinel_times: list) -> Optional[str]:
+    """返回本轮应当执行的最晚一个未执行的哨兵时点；无则 None。"""
+    now = _ensure_aware(now)
+    done = set((state or {}).get("sentinel_done") or [])
+    now_hm = now.astimezone(BEIJING_TZ).strftime("%H:%M")
+    due = [item for item in (sentinel_times or []) if item <= now_hm and item not in done]
+    if not due:
+        return None
+    return max(due)
+
+
+def should_finalize_session(state: dict, session: str, latest_batch: dict, now: datetime, cfg: dict) -> dict:
+    """判断午间/收盘摘要是否应发送。返回 {should_send, reason, skipped_reason}。
+
+    P0-5：除 completed/年龄外，还校验投递状态机、quality.notify_eligible、
+    trade_date 与 batch_id 一致性；delivery_unknown / explicit_failed 不自动重发。
+    """
+    now = _ensure_aware(now)
+    cfg = cfg or {}
+    summary_entry = ((state or {}).get("summary_state") or {}).get(session) or {}
+    status = summary_entry.get("status", "not_attempted")
+    if status == "sent":
+        return {"should_send": False, "reason": "already_sent", "skipped_reason": None}
+    if status == "delivery_unknown":
+        return {"should_send": False, "reason": "delivery_unknown", "skipped_reason": "delivery_unknown"}
+    if status == "pending" and summary_entry.get("attempt_id"):
+        # P0-R3：pending + attempt_id 已落盘 -> 上次投递结果不明，禁止自动重发
+        return {"should_send": False, "reason": "delivery_unknown", "skipped_reason": "pending_attempt_recovered"}
+    if status == "explicit_failed":
+        # 明确失败允许显式手工重试；自动 finalizer 不自动重发
+        return {"should_send": False, "reason": "explicit_failed", "skipped_reason": "explicit_failed"}
+    if not latest_batch or latest_batch.get("status") != "completed":
+        return {"should_send": False, "reason": "no_complete_batch", "skipped_reason": "no_complete_batch"}
+    quality = latest_batch.get("quality") or {}
+    if not quality.get("notify_eligible"):
+        return {"should_send": False, "reason": "notify_not_eligible", "skipped_reason": "notify_not_eligible"}
+    if latest_batch.get("trade_date") and latest_batch.get("trade_date") != now.date().isoformat():
+        return {"should_send": False, "reason": "trade_date_mismatch", "skipped_reason": "trade_date_mismatch"}
+    if (state or {}).get("last_batch_id") and latest_batch.get("batch_id") != (state or {}).get("last_batch_id"):
+        return {"should_send": False, "reason": "batch_mismatch", "skipped_reason": "batch_mismatch"}
+    ts = _parse_dt(latest_batch.get("now"))
+    max_age_min = int(cfg.get("summary_max_age_min", CONFIG["summary_max_age_min"]))
+    if ts is None or (now - ts).total_seconds() > max_age_min * 60:
+        return {"should_send": False, "reason": "latest_batch_stale", "skipped_reason": "latest_batch_stale"}
+    return {"should_send": True, "reason": "ready", "skipped_reason": None}
+
+
+# --------------------------------------------------------------------------- #
+# 薄 I/O 层
+# --------------------------------------------------------------------------- #
+
+def _state_path(path=None) -> Path:
+    return Path(path) if path is not None else INTRADAY_STATE_FILE
+
+
+def load_state(path=None, trade_date: Optional[str] = None, now: Optional[datetime] = None) -> dict:
+    """读取日内状态；跨交易日自动重置；损坏时备份损坏文件并从空状态 warming。"""
+    now = _ensure_aware(now) if now else datetime.now(BEIJING_TZ)
+    trade_date = trade_date or now.date().isoformat()
+    file_path = _state_path(path)
+    state = None
+    valid = False
+    source_unavailable = not file_path.exists()
+    if file_path.exists():
+        try:
+            state = json.loads(file_path.read_text(encoding="utf-8"))
+            valid = isinstance(state, dict) and state.get("schema_version") == SCHEMA_VERSION
+        except (json.JSONDecodeError, OSError):
+            state = None
+            valid = False
+        if not valid:
+            # P2-5：损坏状态备份后从空状态 warming，不静默丢弃
+            try:
+                stamp = datetime.now(BEIJING_TZ).strftime("%Y%m%dT%H%M%S")
+                file_path.replace(file_path.with_suffix(file_path.suffix + f".corrupt.{stamp}"))
+            except OSError:
+                pass
+    if not valid:
+        state = empty_state(trade_date, pipeline_mode=CONFIG["pipeline_mode"])
+        if source_unavailable:
+            state["_source_unavailable"] = True
+        else:
+            state["warming_reason"] = "corrupt_state_recovered"
+    return reset_state_for_trade_date(state, trade_date)
+
+
+def clean_state_for_save(state: dict) -> dict:
+    """P1-R3：保存前 schema 清理，只保留正式字段，剔除 _source_unavailable / warming_reason。"""
+    return {
+        key: value for key, value in (state or {}).items()
+        if not key.startswith("_") and key != "warming_reason"
+    }
+
+
+def save_state(state: dict, path=None) -> None:
+    """唯一临时文件 + os.replace 原子写；自动剔除临时诊断标记。"""
+    file_path = _state_path(path)
+    file_path.parent.mkdir(parents=True, exist_ok=True)
+    tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
+    payload = _json_safe(clean_state_for_save(state))
+    tmp_path.write_text(
+        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
+        encoding="utf-8",
+    )
+    os.replace(tmp_path, file_path)
```

## backend/plugins/principal_capital/pipeline.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/pipeline.py b/backend/plugins/principal_capital/pipeline.py
new file mode 100644
index 0000000..96fd244
--- /dev/null
+++ b/backend/plugins/principal_capital/pipeline.py
@@ -0,0 +1,239 @@
+"""23 v2 流水线编排：RoundContext、候选并集、shadow 真值对照、哨兵回退判定。
+
+所有集合函数按 code 稳定排序，输入 dict 不原地修改，不读取全局时间。
+"""
+import math
+import uuid
+from datetime import timedelta, timezone
+
+from .config import CONFIG
+from .intraday_state import _finite, normalize_code
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+
+
+def build_round_context(
+    owner_id: str,
+    execution_mode: str,
+    pipeline_mode: str,
+    trade_date: str,
+    started_at,
+    deadline_at,
+) -> dict:
+    """一次扫描唯一 batch。started_at/deadline_at 必须为 aware datetime。"""
+    for name, value in (("started_at", started_at), ("deadline_at", deadline_at)):
+        if getattr(value, "tzinfo", None) is None:
+            raise ValueError(f"{name} 必须带时区")
+    return {
+        "batch_id": uuid.uuid4().hex,
+        "owner_id": owner_id,
+        "execution_mode": execution_mode,
+        "pipeline_mode": pipeline_mode,
+        "trade_date": trade_date,
+        "started_at": started_at.isoformat(),
+        "deadline_at": deadline_at.isoformat(),
+    }
+
+
+def build_coarse_union(rows, previous_codes, dwell_codes, audit_codes, cfg) -> dict:
+    """hybrid 粗筛候选并集（多条件 ∪ 上一轮 current ∪ 驻留 ∪ 补集审计分片）。
+
+    ratioamount/r0_ratio 已是百分数（parse_bulk_rows 只标准化一次），此处不再二次 *100。
+    """
+    cfg = cfg or {}
+    buy_ratio = float(cfg.get("coarse_buy_ratio", CONFIG["coarse_buy_ratio"]))
+    sell_ratio = float(cfg.get("coarse_sell_ratio", CONFIG["coarse_sell_ratio"]))
+    r0_head = int(cfg.get("coarse_r0_head", CONFIG["coarse_r0_head"]))
+
+    reasons = {}
+
+    def add(code, reason):
+        code = normalize_code(code)
+        if not code:
+            return
+        reasons.setdefault(code, set()).add(reason)
+
+    eligible = [
+        row for row in (rows or [])
+        if isinstance(row, dict) and "coarse_candidate" in (row.get("eligible_for") or [])
+    ]
+
+    for row in eligible:
+        code = normalize_code(row.get("code"))
+        ratio = _finite(row.get("ratioamount"))
+        if ratio is not None and ratio >= buy_ratio:
+            add(code, "ratioamount_buy")
+        if ratio is not None and ratio <= sell_ratio:
+            add(code, "ratioamount_sell")
+
+    by_net = sorted(
+        [row for row in eligible if _finite(row.get("super_net")) is not None],
+        key=lambda row: _finite(row["super_net"]),
+        reverse=True,
+    )
+    by_ratio = sorted(
+        [row for row in eligible if _finite(row.get("super_ratio")) is not None],
+        key=lambda row: _finite(row["super_ratio"]),
+        reverse=True,
+    )
+    for row in by_net[:r0_head]:
+        add(row.get("code"), "r0_net_top")
+    for row in by_net[-r0_head:] if r0_head else []:
+        add(row.get("code"), "r0_net_bottom")
+    for row in by_ratio[:r0_head]:
+        add(row.get("code"), "r0_ratio_top")
+    for row in by_ratio[-r0_head:] if r0_head else []:
+        add(row.get("code"), "r0_ratio_bottom")
+
+    for code in (previous_codes or []):
+        add(code, "previous_current")
+    for code in (dwell_codes or []):
+        add(code, "dwell")
+    for code in (audit_codes or []):
+        add(code, "complement_audit")
+
+    codes = sorted(reasons.keys())
+    return {"codes": codes, "reasons": {code: sorted(reason_set) for code, reason_set in reasons.items()}}
+
+
+def _df_codes(df) -> set:
+    if df is None or getattr(df, "empty", True) or "code" not in getattr(df, "columns", []):
+        return set()
+    return {normalize_code(code) for code in df["code"].tolist()}
+
+
+def compare_with_truth(coarse_codes, full_refined_df, filters) -> dict:
+    """同一批逐票真值上做 bulk 漏斗集合 diff（禁止跨 batch 比较）。"""
+    coarse = set(normalize_code(code) for code in (coarse_codes or []))
+    truth_buy = _df_codes(filters["buy"](full_refined_df))
+    truth_sell = _df_codes(filters["sell"](full_refined_df))
+    if full_refined_df is None or full_refined_df.empty:
+        shadow_buy, shadow_sell = set(), set()
+    else:
+        shadow_df = full_refined_df[full_refined_df["code"].map(lambda code: normalize_code(code) in coarse)]
+        shadow_buy = _df_codes(filters["buy"](shadow_df))
+        shadow_sell = _df_codes(filters["sell"](shadow_df))
+    return {
+        "kind": "shadow_truth",
+        "truth_buy_count": len(truth_buy),
+        "truth_sell_count": len(truth_sell),
+        "false_negative_buy": sorted(truth_buy - shadow_buy),
+        "false_negative_sell": sorted(truth_sell - shadow_sell),
+        "false_positive_buy": sorted(shadow_buy - truth_buy),
+        "false_positive_sell": sorted(shadow_sell - truth_sell),
+    }
+
+
+def evaluate_auto_fallback(audit, bulk_validation, refine_coverage_ratio, deadline_met) -> dict:
+    """任一哨兵/覆盖/死线条件满足即触发 strict 自动回退。"""
+    reasons = []
+    if audit and (audit.get("false_negative_buy") or audit.get("false_negative_sell")):
+        reasons.append("sentinel_false_negative")
+    if bulk_validation is not None and not bulk_validation.get("valid"):
+        reasons.append("bulk_coverage")
+    if refine_coverage_ratio is not None and refine_coverage_ratio < 1.0:
+        reasons.append("refine_coverage")
+    if deadline_met is False:
+        reasons.append("deadline_exceeded")
+    return {"should_fallback": bool(reasons), "reasons": reasons}
+
+
+def complement_audit_codes(universe_codes, coarse_without_audit_codes, cursor, cfg):
+    """确定性补集审计：只从真正补集取样，环形切片精确返回 min(size, len(complement))。
+
+    返回 (codes, next_cursor)。使用 SHA-256 稳定排序，禁止用进程内不稳定的 hash()。
+    """
+    import hashlib
+
+    cfg = cfg or {}
+    size = int(cfg.get("complement_audit_size", CONFIG["complement_audit_size"]))
+    cursor = int(cursor or 0)
+    universe = {normalize_code(code) for code in (universe_codes or [])}
+    coarse = {normalize_code(code) for code in (coarse_without_audit_codes or [])}
+    complement = sorted(universe - coarse)
+    if not complement:
+        return [], cursor
+    ordered = sorted(
+        complement,
+        key=lambda code: hashlib.sha256(code.encode("utf-8")).hexdigest(),
+    )
+    take = min(size, len(ordered))
+    start = cursor % len(ordered)
+    codes = [ordered[(start + i) % len(ordered)] for i in range(take)]
+    next_cursor = (start + take) % len(ordered)
+    return codes, next_cursor
+
+def compute_round_valid(truth, bulk_meta, audit, deadline_met, universe_verified) -> bool:
+    """P0-R1：M5 单轮准入必须由完整条件计算，不能复制 truth 自带标记。"""
+    if not truth or truth.get("source") != "sina_full":
+        return False
+    if universe_verified is not True:
+        return False
+    if truth.get("coverage_ratio") != 1.0:
+        return False
+    if truth.get("rejected_rows"):
+        return False
+    bulk = bulk_meta or {}
+    if bulk.get("status") != "shadow_only":
+        return False
+    validation = bulk.get("validation") or {}
+    if validation.get("valid") is not True:
+        return False
+    if not audit:
+        return False  # 空 audit 不得冒充零漏检
+    if audit.get("false_negative_buy") or audit.get("false_negative_sell"):
+        return False
+    if deadline_met is not True:
+        return False
+    return True
+
+
+def compute_m5_streak(records, today) -> int:
+    """M5 连续有效交易日（P0-R1）：按交易日聚合；任何无效轮次使当日无效。
+
+    - 同一交易日内所有记录必须 valid_for_admission 且配置指纹唯一。
+    - 必须覆盖上午 + 下午两个时段。
+    - 使用项目交易日历跳过周末与法定休市日（周末不打断连续性）。
+    """
+    import datetime as _dt
+
+    from backend.services.trading_calendar import is_trading_day, prev_trading_day
+
+    today = _dt.date.fromisoformat(today) if isinstance(today, str) else today
+    by_day = {}
+    for record in records or []:
+        day = record.get("trade_date")
+        if not day:
+            continue
+        by_day.setdefault(day, []).append(record)
+
+    def _day_valid(day_records) -> bool:
+        if not day_records:
+            return False
+        if not all(record.get("valid_for_admission") for record in day_records):
+            return False
+        if len({record.get("config_fingerprint") for record in day_records}) != 1:
+            return False
+        sessions = {record.get("session") for record in day_records}
+        if not ({"am", "pm"} <= sessions):
+            return False
+        return True
+
+    streak = 0
+    cursor = today
+    for _ in range(3650):
+        if not is_trading_day(cursor):
+            prev = prev_trading_day(cursor, 1)
+            if prev is None:
+                break
+            cursor = prev
+            continue
+        if not _day_valid(by_day.get(cursor.isoformat())):
+            break
+        streak += 1
+        prev = prev_trading_day(cursor, 1)
+        if prev is None:
+            break
+        cursor = prev
+    return streak
+
```

## backend/plugins/principal_capital/tests/test_acceptance_v2.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/tests/test_acceptance_v2.py b/backend/plugins/principal_capital/tests/test_acceptance_v2.py
new file mode 100644
index 0000000..e2cdc46
--- /dev/null
+++ b/backend/plugins/principal_capital/tests/test_acceptance_v2.py
@@ -0,0 +1,198 @@
+"""23 v2 修复清单 §7 验收用例 A01–A16（离线，patch 外部源）。"""
+import asyncio
+import json
+import time
+import unittest
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+from tempfile import TemporaryDirectory
+from unittest.mock import patch
+
+import pandas as pd
+
+from backend.plugins.principal_capital import pipeline as pl
+from backend.plugins.principal_capital import service as pcs
+from backend.plugins.principal_capital.sources.sina import _parse_sina_flow_item, _run_fetch_batch
+from backend.plugins.smart_money_radar import service as radar
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)
+
+
+def _truth(source="sina_full", requested=3200, received=1, coverage=None, valid=True):
+    codes = [f"{100000 + i:06d}" for i in range(requested)]
+    recv = codes[:received]
+    coverage = (received / requested) if coverage is None else coverage
+    df = pd.DataFrame([{"code": recv[0], "name": "A", "price": 10.0, "change_pct": 1.0,
+                        "total_amount": 2e8, "main_net_inflow": 6e7, "main_inflow_ratio": 30.0,
+                        "super_net": 3e7, "big_net": 3e7, "mid_net": 0, "small_net": -6e7,
+                        "source": "sina_single"}] if recv else [])
+    return (
+        df,
+        {"active_source": source, "is_stale": False},
+        {
+            "source": source, "requested_codes": codes, "received_codes": recv,
+            "missing_codes": sorted(set(codes) - set(recv)), "coverage_ratio": coverage,
+            "rejected_rows": {}, "has_source_time": False, "valid_for_admission": valid,
+        },
+        "strict",
+    )
+
+
+def _df(ratio=-35.0, code="600001"):
+    net = 2e8 * ratio / 100
+    return pd.DataFrame([{"code": code, "name": "主板股", "price": 10.0, "change_pct": 2.0,
+                          "total_amount": 2e8, "main_net_inflow": net, "main_inflow_ratio": ratio,
+                          "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
+                          "source": "eastmoney"}])
+
+
+class AcceptanceTest(unittest.TestCase):
+    def test_a01_partial_coverage_not_completed(self):
+        # A01：universe 3200、只返回 1 行 -> partial，coverage=1/3200，missing=3199
+        with TemporaryDirectory() as tmp:
+            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
+                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
+                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
+                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
+                 patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"), \
+                 patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=_truth(requested=3200, received=1)):
+                result = pcs.run_principal_capital_scan(
+                    now=NOW, force=True, execution_mode="readonly", enable_shadow=False)
+        self.assertIn(result["status"], {"partial", "degraded"})
+        self.assertEqual(result["refine"]["requested_count"], 3200)
+        self.assertEqual(result["refine"]["received_count"], 1)
+        self.assertEqual(result["refine"]["missing_count"], 3199)
+        self.assertAlmostEqual(result["refine"]["coverage_ratio"], 1 / 3200)
+
+    def test_a02_truth_not_sina_full_not_admission(self):
+        # A02：truth 非 sina_full -> audit.valid_for_admission=false
+        with TemporaryDirectory() as tmp:
+            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
+                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
+                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
+                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
+                 patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"), \
+                 patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"), \
+                 patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=_truth(source="eastmoney", valid=False)), \
+                 patch.object(pcs, "_run_bulk_shadow",
+                              return_value=({"status": "shadow_only"}, {"kind": "shadow_truth"}, 0)), \
+                 patch.object(pcs, "send_email", return_value=(True, None)), \
+                 patch.object(pcs.intraday, "acquire_owner_atomic",
+                              return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)), \
+                 patch.object(pcs.intraday, "save_state", return_value=None):
+                result = pcs.run_principal_capital_scan(
+                    now=NOW, force=True, execution_mode="official", owner_id="github_actions",
+                    enable_shadow=True)
+        self.assertFalse(result["audit"]["valid_for_admission"])
+
+    def test_a09_api_trigger_does_not_write_official(self):
+        # A09：API /trigger 前后 official latest 字节不变
+        from backend.plugins.principal_capital import router
+
+        async def _call():
+            from starlette.background import BackgroundTasks
+            bg = BackgroundTasks()
+            return await router.trigger_principal_capital(
+                background_tasks=bg, buy_threshold=50, sell_threshold=30,
+                exclude_star=True, dry_run=False, force=True, enable_verify=False,
+                execution_mode="shadow",
+            )
+
+        with TemporaryDirectory() as tmp:
+            report_file = Path(tmp) / "latest.json"
+            report_file.write_text('{"status":"completed","batch_id":"keep"}', encoding="utf-8")
+            before = report_file.read_bytes()
+            with patch.object(router, "write_manual_status") as wms, \
+                 patch.object(pcs, "REPORT_FILE", report_file):
+                result = asyncio.run(_call())
+            after = report_file.read_bytes()
+        self.assertEqual(result["status"], "started")
+        self.assertEqual(before, after)
+        wms.assert_called_once()
+
+    def test_a10_sina_required_field_invalid_rejected(self):
+        # A10：r0/r1 必需字段缺失 -> 行被拒绝并记录原因
+        row, reason = _parse_sina_flow_item({"r0_in": "100", "r0_out": None}, "600001", NOW)
+        self.assertIsNone(row)
+        self.assertIn("missing_required_field", reason)
+
+    def test_a11_batch_timeout_is_real_wall_clock(self):
+        # A11：250ms 任务、batch timeout 30ms -> 不等待全部完成
+        def slow(code, single_timeout, fetched_at):
+            time.sleep(0.25)
+            return ({"code": code}, None)
+
+        t0 = time.monotonic()
+        _rows, rejected = _run_fetch_batch(
+            ["600001", "600002", "600003", "600004"], 2, 8, 0.03, NOW, slow)
+        elapsed = time.monotonic() - t0
+        self.assertLess(elapsed, 0.2)
+        self.assertGreaterEqual(len(rejected), 1)
+
+    def test_a13_authoritative_empty_current(self):
+        # A13：buy_candidates_current=[] 且 legacy 有旧数据 -> 权威空，不回退
+        items = radar._candidate_lists({
+            "buy_candidates_current": [],
+            "buy_triggered": [{"code": "600001"}],
+        })
+        self.assertEqual(items, [])
+
+    def test_a14_state_report_batch_mismatch(self):
+        # A14：state/report batch_id 不一致 -> degraded + consistency_error
+        report = {"status": "completed", "batch_id": "b2", "trade_date": "2026-09-15"}
+        with patch.object(pcs.intraday, "load_state", return_value={"last_batch_id": "b1"}):
+            out = pcs._check_report_consistency(report)
+        self.assertEqual(out["status"], "degraded")
+        self.assertTrue(out["consistency_error"])
+
+    def test_a15_conflict_does_not_write_official(self):
+        # A15：不同 owner 并发 -> 拒绝方不改 official 文件
+        now = datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ)
+        conflicting = pcs.intraday.empty_state(
+            "2026-09-15", owner_id="run_a",
+            owner_lease_expires_at=(now + timedelta(minutes=5)).isoformat())
+        with TemporaryDirectory() as tmp:
+            report_file = Path(tmp) / "latest.json"
+            report_file.write_text('{"status":"completed","batch_id":"keep"}', encoding="utf-8")
+            before = report_file.read_bytes()
+            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
+                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
+                 patch.object(pcs, "REPORT_FILE", report_file), \
+                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
+                 patch.object(pcs, "OWNER_CONFLICT_FILE", Path(tmp) / "conflict.json"), \
+                 patch.object(pcs.intraday, "load_state", return_value=conflicting), \
+                 patch.object(pcs.intraday, "save_state", return_value=None):
+                result = pcs.run_principal_capital_scan(
+                    now=now, force=True, execution_mode="official", owner_id="run_b",
+                    enable_shadow=False)
+            after = report_file.read_bytes()
+        self.assertEqual(result["status"], "owner_conflict")
+        self.assertEqual(before, after)
+
+    def _day_records(self, day, valid=True):
+        # 每个有效交易日至少 am + pm 两轮，且均 valid、指纹一致
+        return [
+            {"trade_date": day, "session": "am", "valid_for_admission": valid, "config_fingerprint": "fp"},
+            {"trade_date": day, "session": "pm", "valid_for_admission": valid, "config_fingerprint": "fp"},
+        ]
+
+    def test_a16_m5_streak_reset_on_incomplete_day(self):
+        # A16：某交易日任一 invalid 轮次 -> 该日整体无效，有效连续日重新计算；
+        # 周末（09-12/09-13）不打断交易日连续性
+        records = []
+        records += self._day_records("2026-09-08", valid=True)
+        records += self._day_records("2026-09-09", valid=True)
+        records += self._day_records("2026-09-10", valid=True)
+        # 09-11（周五）am valid + pm invalid -> 当日整体无效
+        records.append({"trade_date": "2026-09-11", "session": "am", "valid_for_admission": True, "config_fingerprint": "fp"})
+        records.append({"trade_date": "2026-09-11", "session": "pm", "valid_for_admission": False, "config_fingerprint": "fp"})
+        records += self._day_records("2026-09-14", valid=True)
+        records += self._day_records("2026-09-15", valid=True)
+        self.assertEqual(pl.compute_m5_streak(records, "2026-09-15"), 2)
+
+
+if __name__ == "__main__":
+    unittest.main()
```

## backend/plugins/principal_capital/tests/test_bulk_pipeline.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/tests/test_bulk_pipeline.py b/backend/plugins/principal_capital/tests/test_bulk_pipeline.py
new file mode 100644
index 0000000..bef561e
--- /dev/null
+++ b/backend/plugins/principal_capital/tests/test_bulk_pipeline.py
@@ -0,0 +1,173 @@
+"""23 v2 bulk 适配器 + 候选并集 + shadow 真值对照测试。"""
+import unittest
+from datetime import datetime, timezone, timedelta
+
+import pandas as pd
+
+from backend.plugins.principal_capital.sources import sina_market as sm
+from backend.plugins.principal_capital import pipeline as pl
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)
+
+
+def _bulk_item(symbol="sh600000", ratio=0.5, r0_ratio=0.3, r0_net=1e6, amount=1e8, changeratio=0.02):
+    return {"symbol": symbol, "name": "测试股", "trade": "10.0", "changeratio": changeratio,
+            "amount": amount, "r0_net": r0_net, "r0_ratio": r0_ratio,
+            "r3_net": 1, "netamount": 1e6, "ratioamount": ratio}
+
+
+def _refined(code, ratio):
+    main_net = 2e8 * ratio / 100
+    return {"code": code, "name": f"股票{code}", "price": 10.0, "change_pct": 2.0,
+            "total_amount": 2e8, "main_net_inflow": main_net, "main_inflow_ratio": ratio,
+            "super_net": main_net * 0.6, "big_net": main_net * 0.4, "mid_net": 0, "small_net": -main_net}
+
+
+class BulkParseTest(unittest.TestCase):
+    def test_parse_normalizes_percent_once(self):
+        rows = sm.parse_bulk_rows([_bulk_item(ratio=0.4, r0_ratio=0.2)], NOW)
+        self.assertEqual(rows[0]["ratioamount"], 40.0)
+        self.assertEqual(rows[0]["super_ratio"], 20.0)
+        self.assertAlmostEqual(rows[0]["change_pct"], 2.0)
+        self.assertIn("coarse_candidate", rows[0]["eligible_for"])
+
+    def test_parse_non_list_raises(self):
+        with self.assertRaises(ValueError):
+            sm.parse_bulk_rows({"a": 1}, NOW)
+
+    def test_parse_missing_and_invalid_values_not_zero(self):
+        item = _bulk_item()
+        item["amount"] = "-"
+        item["ratioamount"] = None
+        item["r0_net"] = "NaN"
+        rows = sm.parse_bulk_rows([item], NOW)
+        self.assertIsNone(rows[0]["total_amount"])
+        self.assertIsNone(rows[0]["ratioamount"])
+        self.assertIsNone(rows[0]["super_net"])
+        self.assertEqual(rows[0]["eligible_for"], [])
+        self.assertTrue(any("non_finite" in reason for reason in rows[0]["degraded_reasons"]))
+
+    def test_parse_skips_non_shsz_symbols(self):
+        rows = sm.parse_bulk_rows([_bulk_item(symbol="bj920001")], NOW)
+        self.assertEqual(rows, [])
+
+
+class BulkValidationTest(unittest.TestCase):
+    def test_full_coverage_valid(self):
+        rows = sm.parse_bulk_rows([_bulk_item("sh600000"), _bulk_item("sz000001")], NOW)
+        result = sm.validate_bulk_rows(rows, ["600000", "000001"])
+        self.assertTrue(result["valid"])
+        self.assertEqual(result["coverage_ratio"], 1.0)
+
+    def test_missing_one_code_invalid(self):
+        rows = sm.parse_bulk_rows([_bulk_item("sh600000")], NOW)
+        result = sm.validate_bulk_rows(rows, ["600000", "000001"])
+        self.assertFalse(result["valid"])
+        self.assertIn("000001", result["missing_codes"])
+
+    def test_extra_index_fund_allowed(self):
+        rows = sm.parse_bulk_rows([_bulk_item("sh600000"), _bulk_item("sh000001")], NOW)
+        result = sm.validate_bulk_rows(rows, ["600000"])
+        self.assertTrue(result["valid"])
+        self.assertIn("000001", result["extra_codes"])
+
+    def test_duplicate_invalid(self):
+        rows = sm.parse_bulk_rows([_bulk_item("sh600000"), _bulk_item("sh600000")], NOW)
+        result = sm.validate_bulk_rows(rows, ["600000"])
+        self.assertFalse(result["valid"])
+        self.assertIn("600000", result["duplicate_codes"])
+
+    def test_known_non_trading_reason(self):
+        rows = sm.parse_bulk_rows([_bulk_item("sh600000")], NOW)
+        result = sm.validate_bulk_rows(rows, ["600000", "000001"], known_non_trading={"000001": "停牌"})
+        self.assertTrue(result["valid"])
+        self.assertEqual(result["missing_reasons"]["000001"], "停牌")
+
+
+class CoarseUnionTest(unittest.TestCase):
+    def test_union_dedup_stable_order(self):
+        rows = sm.parse_bulk_rows([
+            _bulk_item("sh600000", ratio=0.6),   # ratioamount_buy
+            _bulk_item("sz000001", ratio=-0.3),  # ratioamount_sell
+            _bulk_item("sh600002", ratio=0.1, r0_net=1e9),  # r0_net_top
+        ], NOW)
+        result = pl.build_coarse_union(rows, ["600003"], ["600004"], ["600005"], {})
+        self.assertEqual(result["codes"], ["000001", "600000", "600002", "600003", "600004", "600005"])
+        self.assertIn("ratioamount_buy", result["reasons"]["600000"])
+
+    def test_previous_dwell_audit_enter(self):
+        rows = sm.parse_bulk_rows([_bulk_item("sh600000", ratio=0.1)], NOW)
+        result = pl.build_coarse_union(rows, ["600001"], ["600002"], ["600003"], {})
+        self.assertIn("600001", result["codes"])
+        self.assertIn("600002", result["codes"])
+        self.assertIn("600003", result["codes"])
+
+
+class CompareWithTruthTest(unittest.TestCase):
+    def _filters(self):
+        from backend.plugins.principal_capital.service import filter_buy_candidates, filter_sell_candidates
+        return {
+            "buy": lambda df: filter_buy_candidates(df),
+            "sell": lambda df: filter_sell_candidates(df),
+        }
+
+    def test_false_negative_buy_detected(self):
+        # ratioamount 粗筛低但精算 main_inflow_ratio >= 50 -> 必须被标 false negative
+        df = pd.DataFrame([_refined("600001", 55), _refined("600002", 30)])
+        coarse = ["600002"]  # 漏掉 600001
+        audit = pl.compare_with_truth(coarse, df, self._filters())
+        self.assertEqual(audit["false_negative_buy"], ["600001"])
+        self.assertEqual(audit["truth_buy_count"], 1)
+
+    def test_false_negative_sell_detected(self):
+        df = pd.DataFrame([_refined("600001", -35), _refined("600002", 10)])
+        coarse = ["600002"]
+        audit = pl.compare_with_truth(coarse, df, self._filters())
+        self.assertEqual(audit["false_negative_sell"], ["600001"])
+
+    def test_no_false_negative_when_union_covers(self):
+        df = pd.DataFrame([_refined("600001", 55), _refined("600002", -35)])
+        audit = pl.compare_with_truth(["600001", "600002"], df, self._filters())
+        self.assertEqual(audit["false_negative_buy"], [])
+        self.assertEqual(audit["false_negative_sell"], [])
+
+
+class PipelineHelpersTest(unittest.TestCase):
+    def test_round_context_requires_aware(self):
+        with self.assertRaises(ValueError):
+            pl.build_round_context("o", "official", "strict", "2026-09-15", datetime(2026, 9, 15, 14, 30), datetime(2026, 9, 15, 14, 31))
+        ctx = pl.build_round_context("o", "official", "strict", "2026-09-15", NOW, NOW + timedelta(minutes=1))
+        self.assertEqual(ctx["pipeline_mode"], "strict")
+
+    def test_auto_fallback(self):
+        audit = {"false_negative_buy": ["600001"], "false_negative_sell": []}
+        decision = pl.evaluate_auto_fallback(audit, None, 1.0, True)
+        self.assertTrue(decision["should_fallback"])
+        self.assertIn("sentinel_false_negative", decision["reasons"])
+
+    def test_complement_audit_deterministic(self):
+        codes = [f"600{i:03d}" for i in range(100)]
+        coarse = set(codes[:20])
+        a, _ = pl.complement_audit_codes(codes, coarse, 0, {"complement_audit_size": 20})
+        b, _ = pl.complement_audit_codes(codes, coarse, 0, {"complement_audit_size": 20})
+        self.assertEqual(a, b)
+        self.assertEqual(len(a), 20)
+        self.assertTrue(set(a).isdisjoint(coarse))
+
+    def test_complement_audit_exact_n_and_cursor_advance(self):
+        # A04：3200 个代码、size=300 -> 恰好 300；游标推进；多轮覆盖不同补集
+        codes = [f"{100000 + i:06d}" for i in range(3200)]
+        coarse = set(codes[:1000])
+        cursor = 0
+        seen = []
+        for _round in range(3):
+            picked, cursor = pl.complement_audit_codes(codes, coarse, cursor, {"complement_audit_size": 300})
+            self.assertEqual(len(picked), 300)
+            self.assertTrue(set(picked).isdisjoint(coarse))
+            seen.extend(picked)
+        self.assertGreater(len(set(seen)), 300)  # 多轮覆盖不同补集
+
+
+if __name__ == "__main__":
+    unittest.main()
```

## backend/plugins/principal_capital/tests/test_intraday_state.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/tests/test_intraday_state.py b/backend/plugins/principal_capital/tests/test_intraday_state.py
new file mode 100644
index 0000000..31c7662
--- /dev/null
+++ b/backend/plugins/principal_capital/tests/test_intraday_state.py
@@ -0,0 +1,292 @@
+"""23 v2 日内状态纯函数测试。"""
+import unittest
+from datetime import datetime, timedelta, timezone
+
+from backend.plugins.principal_capital import intraday_state as its
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)
+
+
+def _row(code, ratio=60.0, net=6e7, amount=2e8):
+    return {"code": code, "name": f"股票{code}", "price": 10.0, "change_pct": 2.0,
+            "total_amount": amount, "main_net_inflow": net, "main_inflow_ratio": ratio,
+            "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
+            "source": "sina_single", "quality_status": "provisional"}
+
+
+def _obs(ts, main_net, total_amount, segment="sina_single:v1:2026-09-15", partial=False, stale=False, cache=False):
+    return {"observed_at": ts.isoformat(), "source_segment": segment, "main_net_inflow": main_net,
+            "total_amount": total_amount, "batch_id": "b", "partial": partial, "stale": stale, "cache": cache}
+
+
+class CandidateStateTest(unittest.TestCase):
+    def test_first_seen_fresh_then_fresh_false(self):
+        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
+        first = its.merge_candidate_state(None, [_row("600001")], [], meta)
+        self.assertTrue(first["buy:600001"]["fresh"])
+        self.assertEqual(first["buy:600001"]["seen_rounds"], 1)
+
+        meta2 = {"batch_id": "b2", "now": (NOW + timedelta(minutes=5)).isoformat(), "is_partial": False}
+        second = its.merge_candidate_state({"candidates": first}, [_row("600001", ratio=62.0)], [], meta2)
+        self.assertFalse(second["buy:600001"]["fresh"])
+        self.assertEqual(second["buy:600001"]["seen_rounds"], 2)
+        self.assertEqual(second["buy:600001"]["latest_metrics"]["main_inflow_ratio"], 62.0)
+
+    def test_complete_batch_marks_missing_as_not_current(self):
+        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
+        first = its.merge_candidate_state(None, [_row("600001"), _row("600002")], [], meta)
+        meta2 = {"batch_id": "b2", "now": (NOW + timedelta(minutes=5)).isoformat(), "is_partial": False}
+        second = its.merge_candidate_state({"candidates": first}, [_row("600001")], [], meta2)
+        self.assertTrue(second["buy:600001"]["is_current"])
+        self.assertFalse(second["buy:600002"]["is_current"])
+
+    def test_partial_batch_does_not_evict(self):
+        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
+        first = its.merge_candidate_state(None, [_row("600001"), _row("600002")], [], meta)
+        meta2 = {"batch_id": "b2", "now": (NOW + timedelta(minutes=5)).isoformat(), "is_partial": True}
+        second = its.merge_candidate_state({"candidates": first}, [_row("600001")], [], meta2)
+        self.assertTrue(second["buy:600002"]["is_current"])
+
+    def test_same_batch_replay_is_idempotent(self):
+        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
+        first = its.merge_candidate_state(None, [_row("600001")], [], meta)
+        replay = its.merge_candidate_state({"candidates": first}, [_row("600001")], [], meta)
+        self.assertEqual(replay["buy:600001"]["seen_rounds"], 1)
+
+    def test_cross_trade_date_resets(self):
+        state = its.empty_state("2026-09-15")
+        state["candidates"] = {"buy:600001": {"is_current": True}}
+        state["summary_state"] = {"am": {"status": "sent"}, "pm": {"status": "pending"}}
+        reset = its.reset_state_for_trade_date(state, "2026-09-16")
+        self.assertEqual(reset["candidates"], {})
+        self.assertEqual(reset["summary_state"], {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}})
+        self.assertEqual(reset["audit_cursor"], 0)
+
+
+class OwnerTest(unittest.TestCase):
+    def test_acquire_owner_and_renew(self):
+        state = its.empty_state("2026-09-15")
+        ok, state, reason = its.acquire_owner(state, "github_actions", 600, NOW)
+        self.assertTrue(ok)
+        self.assertIsNone(reason)
+        ok2, state2, _ = its.acquire_owner(state, "github_actions", 600, NOW + timedelta(minutes=5))
+        self.assertTrue(ok2)
+        self.assertEqual(state2["owner_id"], "github_actions")
+
+    def test_owner_conflict_rejected(self):
+        state = its.empty_state("2026-09-15")
+        ok, state, _ = its.acquire_owner(state, "github_actions", 600, NOW)
+        self.assertTrue(ok)
+        ok2, _, reason = its.acquire_owner(state, "other_owner", 600, NOW + timedelta(minutes=1))
+        self.assertFalse(ok2)
+        self.assertIn("owner_conflict", reason)
+
+    def test_lease_expired_allows_takeover(self):
+        state = its.empty_state("2026-09-15")
+        ok, state, _ = its.acquire_owner(state, "github_actions", 60, NOW)
+        self.assertTrue(ok)
+        ok2, _, _ = its.acquire_owner(state, "github_actions", 600, NOW + timedelta(minutes=11))
+        self.assertTrue(ok2)
+
+
+class FundObservationTest(unittest.TestCase):
+    def test_append_caps_points(self):
+        cfg = {"intraday_max_points": 3}
+        series = {}
+        for i in range(5):
+            meta = {"batch_id": f"b{i}", "observed_at": NOW.isoformat(),
+                    "source_segment": "sina_single:v1:2026-09-15"}
+            series = its.append_fund_observation(
+                series, {"code": "600001", "main_net_inflow": 100 + i, "total_amount": 1000 + i}, meta, cfg)
+        self.assertEqual(len(series["600001"]), 3)
+        self.assertEqual(series["600001"][-1]["main_net_inflow"], 104)
+
+    def test_append_idempotent_by_batch(self):
+        series = {}
+        meta = {"batch_id": "b1", "observed_at": NOW.isoformat()}
+        row = {"code": "600001", "main_net_inflow": 100, "total_amount": 1000}
+        series = its.append_fund_observation(series, row, meta, {})
+        series = its.append_fund_observation(series, row, meta, {})
+        self.assertEqual(len(series["600001"]), 1)
+
+    def test_append_does_not_mutate_input(self):
+        series = {}
+        meta = {"batch_id": "b", "observed_at": NOW.isoformat()}
+        its.append_fund_observation(series, {"code": "600001", "main_net_inflow": 1, "total_amount": 10}, meta, {})
+        self.assertEqual(series, {})
+
+
+class IntradayFeaturesTest(unittest.TestCase):
+    def test_consecutive_five_minute_diff(self):
+        series = [
+            _obs(NOW - timedelta(minutes=10), 100, 1000),
+            _obs(NOW - timedelta(minutes=5), 130, 1300),
+            _obs(NOW, 175, 1600),
+        ]
+        feat = its.compute_intraday_features(series, NOW)
+        # 30 分钟窗口覆盖不足 -> warming + roll None，但 5 分钟增量仍可计算
+        self.assertTrue(feat["warming"])
+        self.assertEqual(feat["warming_reason"], "insufficient_coverage")
+        self.assertEqual(feat["acc_win"], 2)
+        self.assertAlmostEqual(feat["inc_ratio_5m"], 15.0)  # 45 / 300 * 100
+        self.assertIsNone(feat["roll_net_30m"])
+
+    def test_roll_net_30m_computed_when_full_window(self):
+        series = [
+            _obs(NOW - timedelta(minutes=30), 100, 1000),
+            _obs(NOW - timedelta(minutes=25), 130, 1300),
+            _obs(NOW - timedelta(minutes=20), 160, 1600),
+            _obs(NOW - timedelta(minutes=15), 190, 1900),
+            _obs(NOW - timedelta(minutes=10), 220, 2200),
+            _obs(NOW - timedelta(minutes=5), 250, 2500),
+            _obs(NOW, 280, 2800),
+        ]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertFalse(feat["warming"])
+        self.assertAlmostEqual(feat["roll_net_30m"], 180.0)  # 6 * 30
+
+    def test_source_changed_warming(self):
+        series = [
+            _obs(NOW - timedelta(minutes=5), 100, 1000, segment="sina_single:v1:2026-09-15"),
+            _obs(NOW, 130, 1300, segment="tencent:v1:2026-09-15"),
+        ]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertTrue(feat["warming"])
+        self.assertEqual(feat["warming_reason"], "source_changed")
+
+    def test_gap_too_large_warming(self):
+        series = [_obs(NOW - timedelta(minutes=12), 100, 1000), _obs(NOW, 130, 1300)]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertTrue(feat["warming"])
+        self.assertEqual(feat["warming_reason"], "gap_too_large")
+
+    def test_counter_reset_warming(self):
+        series = [_obs(NOW - timedelta(minutes=5), 100, 1500), _obs(NOW, 130, 1300)]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertTrue(feat["warming"])
+        self.assertEqual(feat["warming_reason"], "counter_reset")
+
+    def test_partial_or_cache_obs_excluded(self):
+        series = [
+            _obs(NOW - timedelta(minutes=5), 100, 1000),
+            _obs(NOW, 130, 1300, partial=True),
+        ]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertTrue(feat["warming"])
+        self.assertEqual(feat["warming_reason"], "batch_partial")
+
+    def test_invalid_obs_between_valid_breaks_run(self):
+        # A12：invalid 点位于两个有效点之间，不得跨 invalid 点配对
+        series = [
+            _obs(NOW - timedelta(minutes=10), 100, 1000),
+            _obs(NOW - timedelta(minutes=5), 999, 999, partial=True),
+            _obs(NOW, 200, 2000),
+        ]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertTrue(feat["warming"])
+
+    def test_roll_none_when_coverage_insufficient(self):
+        series = [_obs(NOW - timedelta(minutes=9), 100, 1000), _obs(NOW, 130, 1300)]
+        feat = its.compute_intraday_features(series, NOW)
+        self.assertTrue(feat["warming"])
+        self.assertIsNone(feat["roll_net_30m"])
+
+
+class RadarPoolTest(unittest.TestCase):
+    def test_partial_keeps_old_members_except_stale(self):
+        prev = {
+            "600001": {"code": "600001", "last_seen_at": NOW.isoformat(),
+                       "dwell_until": (NOW + timedelta(minutes=10)).isoformat()},
+            "600002": {"code": "600002", "last_seen_at": (NOW - timedelta(minutes=20)).isoformat(),
+                       "dwell_until": (NOW + timedelta(minutes=10)).isoformat()},
+        }
+        result = its.select_radar_pool(prev, None, NOW, {})
+        self.assertIn("600001", result)
+        self.assertNotIn("600002", result)
+
+    def test_rotation_seats_reserved(self):
+        prev = {}
+        for i in range(50):
+            code = f"600{i:03d}"
+            prev[code] = {"code": code, "entered_at": NOW.isoformat(), "last_seen_at": NOW.isoformat(),
+                          "dwell_until": (NOW + timedelta(minutes=30)).isoformat(),
+                          "latest_metrics": {"main_inflow_ratio": 50 + i, "main_net_inflow": 1e7 + i}}
+        rows = [{"code": f"700{i:03d}", "main_inflow_ratio": 80, "main_net_inflow": 1e8} for i in range(10)]
+        cfg = {"radar_pool_max": 40, "radar_pool_min_dwell_min": 30, "radar_pool_protected_cap": 30,
+               "radar_pool_rotation_seats": 10, "radar_pool_max_stale_min": 15}
+        result = its.select_radar_pool(prev, rows, NOW, cfg)
+        self.assertLessEqual(len(result), 40)
+        fresh_in = sum(1 for entry in result.values() if entry["selection_reason"] != "protected_dwell")
+        self.assertGreaterEqual(fresh_in, 1)
+
+    def test_latest_metrics_attached(self):
+        rows = [{"code": "600001", "main_inflow_ratio": 66, "main_net_inflow": 6e7, "total_amount": 2e8}]
+        result = its.select_radar_pool({}, rows, NOW, {})
+        self.assertIn("600001", result)
+        self.assertEqual(result["600001"]["latest_metrics"]["main_inflow_ratio"], 66)
+
+
+class SentinelFinalizerTest(unittest.TestCase):
+    def test_should_run_sentinel(self):
+        state = {}
+        self.assertEqual(its.should_run_sentinel(state, datetime(2026, 9, 15, 9, 36, tzinfo=BEIJING_TZ), ["09:35", "10:30"]), "09:35")
+        done = its.mark_sentinel_done(state, "09:35")
+        self.assertIsNone(its.should_run_sentinel(done, datetime(2026, 9, 15, 9, 40, tzinfo=BEIJING_TZ), ["09:35", "10:30"]))
+
+    def _latest(self, now_iso, batch_id="b1"):
+        return {
+            "status": "completed", "now": now_iso,
+            "quality": {"notify_eligible": True},
+            "batch_id": batch_id, "trade_date": "2026-09-15",
+        }
+
+    def test_finalize_already_sent(self):
+        state = its.empty_state("2026-09-15")
+        state["summary_state"]["am"] = {"status": "sent"}
+        state["last_batch_id"] = "b1"
+        decision = its.should_finalize_session(state, "am", self._latest(NOW.isoformat()), NOW, {})
+        self.assertFalse(decision["should_send"])
+        self.assertEqual(decision["reason"], "already_sent")
+
+    def test_finalize_delivery_unknown_blocks(self):
+        state = its.empty_state("2026-09-15")
+        state["summary_state"]["am"] = {"status": "delivery_unknown"}
+        decision = its.should_finalize_session(state, "am", self._latest(NOW.isoformat()), NOW, {})
+        self.assertFalse(decision["should_send"])
+        self.assertEqual(decision["reason"], "delivery_unknown")
+
+    def test_finalize_stale_batch(self):
+        state = its.empty_state("2026-09-15")
+        state["last_batch_id"] = "b1"
+        old = (NOW - timedelta(minutes=20)).isoformat()
+        decision = its.should_finalize_session(state, "am", self._latest(old), NOW, {"summary_max_age_min": 15})
+        self.assertFalse(decision["should_send"])
+        self.assertEqual(decision["skipped_reason"], "latest_batch_stale")
+
+    def test_finalize_notify_not_eligible(self):
+        state = its.empty_state("2026-09-15")
+        state["last_batch_id"] = "b1"
+        latest = self._latest(NOW.isoformat())
+        latest["quality"]["notify_eligible"] = False
+        decision = its.should_finalize_session(state, "am", latest, NOW, {})
+        self.assertFalse(decision["should_send"])
+        self.assertEqual(decision["reason"], "notify_not_eligible")
+
+    def test_finalize_batch_mismatch(self):
+        state = its.empty_state("2026-09-15")
+        state["last_batch_id"] = "b1"
+        latest = self._latest(NOW.isoformat(), batch_id="b2")
+        decision = its.should_finalize_session(state, "am", latest, NOW, {})
+        self.assertFalse(decision["should_send"])
+        self.assertEqual(decision["reason"], "batch_mismatch")
+
+    def test_finalize_ready(self):
+        state = its.empty_state("2026-09-15")
+        state["last_batch_id"] = "b1"
+        decision = its.should_finalize_session(state, "am", self._latest(NOW.isoformat()), NOW, {"summary_max_age_min": 15})
+        self.assertTrue(decision["should_send"])
+
+
+if __name__ == "__main__":
+    unittest.main()
```

## backend/plugins/principal_capital/tests/test_integration_v3.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/tests/test_integration_v3.py b/backend/plugins/principal_capital/tests/test_integration_v3.py
new file mode 100644
index 0000000..366f083
--- /dev/null
+++ b/backend/plugins/principal_capital/tests/test_integration_v3.py
@@ -0,0 +1,214 @@
+"""第三轮 P0-R/P1-R 集成测试（调用生产函数，不 mock 最终 meta）。"""
+import json
+import subprocess
+import sys
+import threading
+import time
+import unittest
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+from tempfile import TemporaryDirectory
+from unittest.mock import patch
+
+import pandas as pd
+
+from backend.plugins.principal_capital import intraday_state as its
+from backend.plugins.principal_capital import pipeline as pl
+from backend.plugins.principal_capital import service as pcs
+from backend.plugins.smart_money_radar import service as radar
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)
+
+
+def _truth(source="sina_full", verified=True, coverage=1.0, rejected=None):
+    return {
+        "source": source, "requested_codes": ["600000"], "received_codes": ["600000"],
+        "missing_codes": [], "coverage_ratio": coverage, "rejected_rows": rejected or {},
+        "universe_verified": verified, "has_source_time": False,
+    }
+
+
+def _bulk(status="shadow_only", valid=True):
+    return {"status": status, "validation": {"valid": valid}}
+
+
+def _audit(fn_buy=None, fn_sell=None):
+    return {"false_negative_buy": fn_buy or [], "false_negative_sell": fn_sell or []}
+
+
+class RoundValidTest(unittest.TestCase):
+    def test_shadow_error_invalid(self):
+        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(status="shadow_error"), _audit(), True, True))
+
+    def test_bulk_validation_fail_invalid(self):
+        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(valid=False), _audit(), True, True))
+
+    def test_false_negative_invalid(self):
+        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(), _audit(fn_buy=["600000"]), True, True))
+
+    def test_deadline_exceeded_invalid(self):
+        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(), _audit(), False, True))
+
+    def test_universe_unverified_invalid(self):
+        self.assertFalse(pl.compute_round_valid(_truth(verified=False), _bulk(), _audit(), True, False))
+
+    def test_full_valid(self):
+        self.assertTrue(pl.compute_round_valid(_truth(), _bulk(), _audit(), True, True))
+
+
+class M5StreakTest(unittest.TestCase):
+    def _records(self, day, am_valid=True, pm_valid=True):
+        return [
+            {"trade_date": day, "session": "am", "valid_for_admission": am_valid, "config_fingerprint": "fp"},
+            {"trade_date": day, "session": "pm", "valid_for_admission": pm_valid, "config_fingerprint": "fp"},
+        ]
+
+    def test_same_day_any_failed_invalidates_day(self):
+        records = self._records("2026-09-14", am_valid=False, pm_valid=True)
+        records += self._records("2026-09-15", True, True)
+        self.assertEqual(pl.compute_m5_streak(records, "2026-09-15"), 1)
+
+    def test_weekend_does_not_break(self):
+        # 09-11 周五有效，09-14 周一有效；中间周末不打断
+        records = self._records("2026-09-11", True, True) + self._records("2026-09-14", True, True)
+        self.assertEqual(pl.compute_m5_streak(records, "2026-09-14"), 2)
+
+
+class ConsistencyTest(unittest.TestCase):
+    def test_missing_state_consistency_error(self):
+        report = {"status": "completed", "batch_id": "b1", "trade_date": "2026-09-15"}
+        with patch.object(pcs.intraday, "load_state",
+                          return_value=pcs.intraday.empty_state("2026-09-15")):
+            out = pcs._check_report_consistency(report)
+        self.assertEqual(out["status"], "degraded")
+        self.assertTrue(out["consistency_error"])
+
+
+class OwnerAtomicTest(unittest.TestCase):
+    def test_two_writers_one_wins(self):
+        results = {}
+        with TemporaryDirectory() as tmp:
+            state_path = Path(tmp) / "state.json"
+
+            def attempt(owner):
+                try:
+                    ok, _state, reason = its.acquire_owner_atomic(
+                        state_path, owner, 600, NOW)
+                    results[owner] = (ok, reason)
+                except Exception as exc:  # noqa: BLE001
+                    results[owner] = (False, str(exc))
+
+            threads = [threading.Thread(target=attempt, args=(o,)) for o in ("run_a", "run_b")]
+            for t in threads:
+                t.start()
+            for t in threads:
+                t.join()
+        oks = [v[0] for v in results.values()]
+        self.assertEqual(sum(oks), 1)
+        self.assertEqual(len([v for v in results.values() if not v[0]]), 1)
+
+
+class SentinelTest(unittest.TestCase):
+    def test_sentinel_done_persisted_and_no_dup(self):
+        state = its.empty_state("2026-09-15")
+        label = its.should_run_sentinel(state, datetime(2026, 9, 15, 9, 36, tzinfo=BEIJING_TZ), ["09:35"])
+        self.assertEqual(label, "09:35")
+        state = its.mark_sentinel_done(state, label)
+        self.assertIn("09:35", state["sentinel_done"])
+        self.assertIsNone(its.should_run_sentinel(state, datetime(2026, 9, 15, 9, 40, tzinfo=BEIJING_TZ), ["09:35"]))
+
+
+class FinalizerCrashTest(unittest.TestCase):
+    def test_pending_persisted_then_restart_does_not_resend(self):
+        with TemporaryDirectory() as tmp:
+            state_file = Path(tmp) / "state.json"
+            report_file = Path(tmp) / "latest.json"
+            # 子进程写入 pending+attempt_id 后直接退出，模拟进程在 SMTP 前被强杀
+            code = (
+                "import sys; sys.path.insert(0, %r); "
+                "from backend.plugins.principal_capital import intraday_state as its; "
+                "from datetime import datetime, timezone, timedelta; "
+                "BEIJING_TZ = timezone(timedelta(hours=8)); "
+                "now = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ); "
+                "state = its.empty_state('2026-09-15'); "
+                "ok, state, _ = its.acquire_owner(state, 'github_actions', 600, now); "
+                "state['last_batch_id'] = 'b1'; "
+                "state = its.mark_summary_pending(state, 'pm', now.isoformat(), 'attempt-1'); "
+                "its.save_state(state, path=%r)"
+            ) % (str(Path.cwd()), str(state_file))
+            subprocess.run([sys.executable, "-c", code], check=True, cwd=Path.cwd())
+            report_file.write_text(json.dumps({
+                "status": "completed", "now": NOW.isoformat(), "batch_id": "b1",
+                "trade_date": "2026-09-15", "quality": {"notify_eligible": True},
+            }), encoding="utf-8")
+            with patch.object(pcs.intraday, "INTRADAY_STATE_FILE", state_file),                  patch.object(pcs, "REPORT_FILE", report_file),                  patch.object(pcs, "send_email", return_value=(True, None)) as send:
+                result = pcs.finalize_principal_capital_session(
+                    "pm", now=NOW, execution_mode="official", owner_id="github_actions")
+            self.assertEqual(result["status"], "skipped")
+            self.assertEqual(result["reason"], "delivery_unknown")
+            send.assert_not_called()
+
+
+class ScanIntegrationTest(unittest.TestCase):
+    def _df(self, ratio=60.0):
+        net = 2e8 * ratio / 100
+        return pd.DataFrame([{"code": "600001", "name": "A", "price": 10.0, "change_pct": 1.0,
+                              "total_amount": 2e8, "main_net_inflow": net, "main_inflow_ratio": ratio,
+                              "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
+                              "source": "sina_single", "quality_status": "provisional"}])
+
+    def _truth_tuple(self, df, source="sina_full", verified=True, coverage=1.0):
+        return (df, {"active_source": source, "is_stale": False},
+                {"source": source, "requested_codes": list(df.code), "received_codes": list(df.code),
+                 "missing_codes": [], "coverage_ratio": coverage, "rejected_rows": {},
+                 "universe_verified": verified, "has_source_time": False,
+                 "valid_for_admission": False}, "strict")
+
+    def _patch_files(self, tmp):
+        return [
+            patch.object(pcs, "DATA_DIR", Path(tmp)),
+            patch.object(pcs, "REPORT_DIR", Path(tmp)),
+            patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),
+            patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),
+            patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"),
+            patch.object(pcs, "OWNER_CONFLICT_FILE", Path(tmp) / "conflict.json"),
+            patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"),
+            patch.object(pcs, "MANUAL_STATUS_FILE", Path(tmp) / "manual.json"),
+        ]
+
+    def test_bulk_latency_over_deadline(self):
+        df = self._df()
+        with TemporaryDirectory() as tmp:
+            saved = {}
+            with patch.object(pcs, "DATA_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),                  patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"),                  patch.dict(pcs.CONFIG, {"round_deadline_seconds": 0.05}),                  patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=self._truth_tuple(df)),                  patch.object(pcs, "_run_bulk_shadow",
+                              side_effect=lambda *a, **k: (time.sleep(0.12) or {"status": "shadow_only", "validation": {"valid": True}}, _audit(), 0)),                  patch.object(pcs, "send_email", return_value=(True, None)),                  patch.object(pcs.intraday, "acquire_owner_atomic",
+                              return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),                  patch.object(pcs.intraday, "save_state",
+                              side_effect=lambda s: saved.update(s)):
+                result = pcs.run_principal_capital_scan(
+                    now=NOW, force=True, execution_mode="official", owner_id="github_actions",
+                    enable_shadow=True)
+        self.assertFalse(result["deadline_met"])
+
+    def test_features_reach_pool_and_radar(self):
+        df = self._df()
+        with TemporaryDirectory() as tmp:
+            saved = {}
+            with patch.object(pcs, "DATA_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),                  patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"),                  patch.object(pcs, "_fetch_truth_with_fallback",
+                              return_value=self._truth_tuple(df)),                  patch.object(pcs, "send_email", return_value=(True, None)),                  patch.object(pcs.intraday, "acquire_owner_atomic",
+                              return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),                  patch.object(pcs.intraday, "save_state",
+                              side_effect=lambda s: saved.update(s)):
+                pcs.run_principal_capital_scan(
+                    now=NOW, force=True, execution_mode="official", owner_id="github_actions",
+                    enable_shadow=False)
+        pool = saved.get("pool_entries") or {}
+        entry = pool.get("600001") or {}
+        feat = (entry.get("latest_metrics") or {}).get("features")
+        self.assertIsNotNone(feat)
+        for key in ("roll_net_30m", "acc_win", "inc_ratio_5m", "interval_seconds", "warming", "warming_reason"):
+            self.assertIn(key, feat)
+
+
+if __name__ == "__main__":
+    unittest.main()
```

## backend/plugins/principal_capital/tests/test_pipeline_modes.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/tests/test_pipeline_modes.py b/backend/plugins/principal_capital/tests/test_pipeline_modes.py
new file mode 100644
index 0000000..42b994d
--- /dev/null
+++ b/backend/plugins/principal_capital/tests/test_pipeline_modes.py
@@ -0,0 +1,155 @@
+"""23 v2 执行模式 / 唯一写者 / 副作用边界测试。"""
+import unittest
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+from tempfile import TemporaryDirectory
+from unittest.mock import patch
+
+import pandas as pd
+
+from backend.plugins.principal_capital import service as pcs
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+
+
+def _df(ratio=60.0, code="600001"):
+    net = 2e8 * ratio / 100
+    return pd.DataFrame([{"code": code, "name": "主板股", "price": 10.0, "change_pct": 2.0,
+                          "total_amount": 2e8, "main_net_inflow": net, "main_inflow_ratio": ratio,
+                          "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
+                          "source": "eastmoney"}])
+
+
+def _truth(df, source="eastmoney", coverage=1.0, has_source_time=False):
+    codes = list(df["code"].tolist()) if df is not None and not df.empty else []
+    return (
+        df,
+        {"active_source": source, "is_stale": False},
+        {
+            "source": source, "requested_codes": codes, "received_codes": codes,
+            "missing_codes": [], "coverage_ratio": coverage, "rejected_rows": {},
+            "has_source_time": has_source_time,
+            "valid_for_admission": bool(codes and coverage == 1.0),
+        },
+        "strict",
+    )
+
+
+def _path_patches(tmp):
+    return [
+        patch.object(pcs, "DATA_DIR", Path(tmp)),
+        patch.object(pcs, "REPORT_DIR", Path(tmp)),
+        patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),
+        patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),
+        patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"),
+        patch.object(pcs, "OWNER_CONFLICT_FILE", Path(tmp) / "owner_conflict.json"),
+        patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5_audit.json"),
+        patch.object(pcs, "MANUAL_STATUS_FILE", Path(tmp) / "manual_status.json"),
+    ]
+
+
+class ExecutionModeTest(unittest.TestCase):
+    def test_default_execution_mode_is_readonly(self):
+        self.assertEqual(pcs.resolve_execution_mode(), "readonly")
+
+    def test_invalid_execution_mode_raises(self):
+        with self.assertRaises(ValueError):
+            pcs.resolve_execution_mode("banana")
+
+    def test_hybrid_rejected_until_m6(self):
+        with self.assertRaises(RuntimeError):
+            pcs.resolve_pipeline_mode("hybrid")
+
+    def test_readonly_does_not_write_or_email(self):
+        with TemporaryDirectory() as tmp:
+            patches = _path_patches(tmp) + [
+                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(_df())),
+                patch.object(pcs, "send_email", return_value=(True, None)),
+            ]
+            for p in patches:
+                p.start()
+            try:
+                result = pcs.run_principal_capital_scan(
+                    now=datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ), force=True,
+                    execution_mode="readonly", enable_shadow=False)
+                self.assertEqual(result["status"], "completed")
+                self.assertFalse(result["email_sent"])
+                self.assertFalse((Path(tmp) / "latest.json").exists())
+            finally:
+                for p in reversed(patches):
+                    p.stop()
+
+    def test_official_writes_report_and_emails(self):
+        with TemporaryDirectory() as tmp:
+            patches = _path_patches(tmp) + [
+                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(_df(ratio=-35))),
+                patch.dict(pcs.CONFIG, {"allow_provisional_notify": True}),
+                patch.object(pcs, "send_email", return_value=(True, None)),
+                patch.object(pcs.intraday, "acquire_owner_atomic",
+                             return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),
+                patch.object(pcs.intraday, "save_state", return_value=None),
+            ]
+            for p in patches:
+                p.start()
+            try:
+                result = pcs.run_principal_capital_scan(
+                    now=datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ), force=True,
+                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
+                self.assertEqual(result["status"], "completed")
+                self.assertTrue(result["email_sent"])
+                self.assertTrue((Path(tmp) / "latest.json").exists())
+            finally:
+                for p in reversed(patches):
+                    p.stop()
+
+    def test_owner_conflict_rejected(self):
+        now = datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ)
+        conflicting = pcs.intraday.empty_state("2026-09-15", owner_id="other_owner",
+                                               owner_lease_expires_at=(now + timedelta(minutes=5)).isoformat())
+        with TemporaryDirectory() as tmp:
+            patches = _path_patches(tmp) + [
+                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(_df())),
+                patch.object(pcs, "send_email", return_value=(True, None)),
+                patch.object(pcs.intraday, "acquire_owner_atomic",
+                             return_value=(False, conflicting, "owner_conflict: other_owner")),
+                patch.object(pcs.intraday, "save_state", return_value=None),
+            ]
+            for p in patches:
+                p.start()
+            try:
+                result = pcs.run_principal_capital_scan(
+                    now=now, force=True, execution_mode="official", owner_id="github_actions",
+                    enable_shadow=False)
+            finally:
+                for p in reversed(patches):
+                    p.stop()
+        self.assertEqual(result["status"], "owner_conflict")
+        self.assertFalse(result["email_sent"])
+
+    def test_provisional_source_blocks_direct_notify_by_default(self):
+        df = _df(ratio=-35)
+        with TemporaryDirectory() as tmp:
+            patches = _path_patches(tmp) + [
+                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(df, source="sina")),
+                patch.object(pcs, "send_email", return_value=(True, None)),
+                patch.object(pcs.intraday, "acquire_owner_atomic",
+                             return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),
+                patch.object(pcs.intraday, "save_state", return_value=None),
+            ]
+            send = patches[-2]
+            for p in patches:
+                p.start()
+            try:
+                result = pcs.run_principal_capital_scan(
+                    now=datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ), force=True,
+                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
+            finally:
+                for p in reversed(patches):
+                    p.stop()
+        self.assertEqual(result["quality"]["status"], "provisional")
+        self.assertFalse(result["quality"]["notify_eligible"])
+        self.assertFalse(result["email_sent"])
+
+
+if __name__ == "__main__":
+    unittest.main()
```

## backend/plugins/principal_capital/tests/test_session_finalizer.py（新增）

```diff
diff --git a/backend/plugins/principal_capital/tests/test_session_finalizer.py b/backend/plugins/principal_capital/tests/test_session_finalizer.py
new file mode 100644
index 0000000..afc37cf
--- /dev/null
+++ b/backend/plugins/principal_capital/tests/test_session_finalizer.py
@@ -0,0 +1,157 @@
+"""23 v2 午间/收盘 finalizer 测试。"""
+import json
+import unittest
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+from tempfile import TemporaryDirectory
+from unittest.mock import patch
+
+from backend.plugins.principal_capital import service as pcs
+from backend.plugins.principal_capital import intraday_state as its
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+NOW = datetime(2026, 9, 15, 11, 30, tzinfo=BEIJING_TZ)
+OWNER = "github_actions"
+
+
+def _write_state(state_file, owner=OWNER, batch_id="b1"):
+    state = its.empty_state("2026-09-15")
+    ok, state, _ = its.acquire_owner(state, owner, 600, NOW)
+    assert ok
+    state["last_batch_id"] = batch_id
+    state_file.parent.mkdir(parents=True, exist_ok=True)
+    its.save_state(state, path=state_file)
+
+
+def _write_report(report_file, now_iso, batch_id="b1", notify_eligible=True, status="completed"):
+    report_file.parent.mkdir(parents=True, exist_ok=True)
+    report_file.write_text(json.dumps({
+        "status": status, "now": now_iso, "batch_id": batch_id, "trade_date": "2026-09-15",
+        "quality": {"notify_eligible": notify_eligible},
+    }), encoding="utf-8")
+
+
+class SessionFinalizerTest(unittest.TestCase):
+    def _patches(self, tmp):
+        state_file = Path(tmp) / "intraday_state.json"
+        report_file = Path(tmp) / "latest.json"
+        return (
+            patch.object(pcs.intraday, "INTRADAY_STATE_FILE", state_file),
+            patch.object(pcs, "REPORT_FILE", report_file),
+        ), state_file, report_file
+
+    def test_official_sends_once(self):
+        with TemporaryDirectory() as tmp:
+            patches, state_file, report_file = self._patches(tmp)
+            _write_state(state_file)
+            _write_report(report_file, NOW.isoformat())
+            for p in patches:
+                p.start()
+            try:
+                with patch.object(pcs, "send_email", return_value=(True, None)):
+                    first = pcs.finalize_principal_capital_session(
+                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
+            finally:
+                for p in patches:
+                    p.stop()
+        self.assertEqual(first["status"], "sent")
+        self.assertTrue(first["email_sent"])
+
+    def test_owner_mismatch_conflict(self):
+        with TemporaryDirectory() as tmp:
+            patches, state_file, report_file = self._patches(tmp)
+            _write_state(state_file, owner="other_owner")
+            _write_report(report_file, NOW.isoformat())
+            for p in patches:
+                p.start()
+            try:
+                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
+                    result = pcs.finalize_principal_capital_session(
+                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
+            finally:
+                for p in patches:
+                    p.stop()
+        self.assertEqual(result["status"], "owner_conflict")
+        send.assert_not_called()
+
+    def test_delivery_unknown_blocks_resend(self):
+        with TemporaryDirectory() as tmp:
+            patches, state_file, report_file = self._patches(tmp)
+            _write_state(state_file)
+            _write_report(report_file, NOW.isoformat())
+            for p in patches:
+                p.start()
+            try:
+                with patch.object(pcs, "send_email", side_effect=RuntimeError("smtp boom")):
+                    first = pcs.finalize_principal_capital_session(
+                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
+                # 重新续租（模拟下一次调度），再次调用不得自动重发
+                state = its.load_state(path=state_file, now=NOW)
+                ok, state, _ = its.acquire_owner(state, OWNER, 600, NOW)
+                its.save_state(state, path=state_file)
+                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
+                    second = pcs.finalize_principal_capital_session(
+                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
+            finally:
+                for p in patches:
+                    p.stop()
+        self.assertEqual(first["status"], "delivery_unknown")
+        self.assertEqual(second["status"], "skipped")
+        self.assertEqual(second["reason"], "delivery_unknown")
+        send.assert_not_called()
+
+    def test_provisional_blocks(self):
+        with TemporaryDirectory() as tmp:
+            patches, state_file, report_file = self._patches(tmp)
+            _write_state(state_file)
+            _write_report(report_file, NOW.isoformat(), notify_eligible=False)
+            for p in patches:
+                p.start()
+            try:
+                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
+                    result = pcs.finalize_principal_capital_session(
+                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
+            finally:
+                for p in patches:
+                    p.stop()
+        self.assertEqual(result["status"], "skipped")
+        self.assertEqual(result["reason"], "notify_not_eligible")
+        send.assert_not_called()
+
+    def test_no_complete_batch_skipped(self):
+        with TemporaryDirectory() as tmp:
+            patches, state_file, report_file = self._patches(tmp)
+            _write_state(state_file)
+            _write_report(report_file, NOW.isoformat(), status="no_data")
+            for p in patches:
+                p.start()
+            try:
+                result = pcs.finalize_principal_capital_session(
+                    "am", now=NOW, execution_mode="official", owner_id=OWNER)
+            finally:
+                for p in patches:
+                    p.stop()
+        self.assertEqual(result["status"], "skipped")
+        self.assertEqual(result["reason"], "no_complete_batch")
+
+    def test_shadow_constructs_without_sending(self):
+        with TemporaryDirectory() as tmp:
+            patches, state_file, report_file = self._patches(tmp)
+            _write_state(state_file)
+            _write_report(report_file, NOW.isoformat())
+            for p in patches:
+                p.start()
+            try:
+                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
+                    result = pcs.finalize_principal_capital_session(
+                        "am", now=NOW, execution_mode="shadow")
+            finally:
+                for p in patches:
+                    p.stop()
+        self.assertEqual(result["status"], "constructed")
+        self.assertIn("subject", result)
+        send.assert_not_called()
+
+
+if __name__ == "__main__":
+    unittest.main()
```

## backend/plugins/smart_money_radar/tests/test_pool_selection_v2.py（新增）

```diff
diff --git a/backend/plugins/smart_money_radar/tests/test_pool_selection_v2.py b/backend/plugins/smart_money_radar/tests/test_pool_selection_v2.py
new file mode 100644
index 0000000..5efc6a8
--- /dev/null
+++ b/backend/plugins/smart_money_radar/tests/test_pool_selection_v2.py
@@ -0,0 +1,75 @@
+"""23 v2 雷达池只消费 buy_candidates_current + 日内状态池选择结果。"""
+from datetime import datetime, timedelta, timezone
+
+from backend.plugins.smart_money_radar import service as radar
+
+BEIJING_TZ = timezone(timedelta(hours=8))
+NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)
+
+
+def test_candidate_lists_prefers_buy_current_and_excludes_sell():
+    payload = {
+        "buy_candidates_current": [{"code": "600001", "main_inflow_ratio": 60}],
+        "sell_candidates_current": [{"code": "600002", "main_inflow_ratio": -35}],
+        "buy_triggered": [{"code": "600003", "main_inflow_ratio": 70}],
+    }
+    items = radar._candidate_lists(payload)
+    assert [item["code"] for item in items] == ["600001"]
+
+
+def test_candidate_lists_falls_back_to_triggered():
+    payload = {"buy_triggered": [{"code": "600003", "main_inflow_ratio": 70}], "sell_triggered": []}
+    items = radar._candidate_lists(payload)
+    assert [item["code"] for item in items] == ["600003"]
+
+
+def test_pool_from_intraday_state_attaches_metrics(monkeypatch):
+    state = {"pool_entries": {"600001": {
+        "code": "600001", "latest_metrics": {"main_inflow_ratio": 66, "main_net_inflow": 6e7, "name": "A"},
+    }}}
+    monkeypatch.setattr("backend.plugins.principal_capital.intraday_state.load_state", lambda **kwargs: state)
+    items = radar._pool_from_intraday_state(NOW)
+    assert items[0]["code"] == "600001"
+    assert items[0]["main_inflow_ratio"] == 66
+
+
+def test_load_watch_pool_uses_state_pool(monkeypatch):
+    monkeypatch.setattr(
+        radar, "_pool_from_intraday_state",
+        lambda now: [{"code": "600001", "main_inflow_ratio": 60, "total_amount": 2e8}],
+    )
+    radar._POOL_CACHE.clear()
+    items = radar.load_watch_pool(force=True, now=NOW)
+    assert [item["code"] for item in items] == ["600001"]
+
+
+def test_load_watch_pool_respects_authoritative_empty(monkeypatch, tmp_path):
+    # P1-5 / A13：权威空池 [] 不得回退旧报告复活陈旧候选
+    pool_file = tmp_path / "principal_capital_latest.json"
+    pool_file.write_text(
+        '{"status": "completed", "buy_triggered": [{"code": "600001", "name": "A", "main_inflow_ratio": 61, "total_amount": 200000000}], "sell_triggered": []}',
+        encoding="utf-8",
+    )
+    cfg = dict(radar.CONFIG)
+    cfg.update({"pool_source_file": str(pool_file), "pool_max": 10})
+    monkeypatch.setattr(radar, "CONFIG", cfg)
+    monkeypatch.setattr(radar, "_pool_from_intraday_state", lambda now: [])
+    radar._POOL_CACHE.clear()
+    items = radar.load_watch_pool(force=True, now=NOW)
+    assert items == []
+
+
+def test_load_watch_pool_falls_back_when_state_unavailable(monkeypatch, tmp_path):
+    pool_file = tmp_path / "principal_capital_latest.json"
+    pool_file.write_text(
+        '{"status": "completed", "buy_triggered": [{"code": "600001", "name": "A", "main_inflow_ratio": 61, "total_amount": 200000000}], "sell_triggered": []}',
+        encoding="utf-8",
+    )
+    cfg = dict(radar.CONFIG)
+    cfg.update({"pool_source_file": str(pool_file), "pool_max": 10})
+    monkeypatch.setattr(radar, "CONFIG", cfg)
+    monkeypatch.setattr(radar, "_pool_from_intraday_state", lambda now: None)
+    monkeypatch.setattr(radar, "_fetch_snapshot_json", lambda *a, **k: {})
+    radar._POOL_CACHE.clear()
+    items = radar.load_watch_pool(force=True, now=NOW)
+    assert [item["code"] for item in items] == ["600001"]
```

