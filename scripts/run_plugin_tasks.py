#!/usr/bin/env python3
"""每日插件任务调度：配置化清单 + 线程池并发 + 超时 + 结果记录。

设计目标：
- 可扩展：后续新增任务只需在 TASKS 清单加一行。
- 线程/并发管理：ThreadPoolExecutor + PLUGIN_TASK_WORKERS 控制并发子进程数（免费源限流）。
- 免费维护：纯 Python 标准库（subprocess/concurrent.futures），零额外依赖。
- 性能：并发数默认 2，避免同时轰炸东财/新浪等免费源；blocking 任务失败才非零退出。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]

# ==================== 可扩展任务清单（后续在此加一行） ====================
# 字段说明：
#   cli          backend.main 的 CLI 参数
#   label        展示/日志标签
#   blocking     失败是否阻断整条调度（True=失败 exit 1）
#   timeout_s    单任务超时（秒）
TASKS = [
    {"cli": "--run-emotion-once",        "label": "情绪周期",     "blocking": False, "timeout_s": 300},
    {"cli": "--run-lhb-once",            "label": "龙虎榜",       "blocking": False, "timeout_s": 300},
    {"cli": "--run-tail-raid-once",      "label": "尾盘抢筹",     "blocking": False, "timeout_s": 300},
    {"cli": "--run-board-once",          "label": "板块轮动",     "blocking": False, "timeout_s": 300},
    {"cli": "--run-zt-seal-once",        "label": "涨停封单",     "blocking": False, "timeout_s": 300},
    {"cli": "--run-volume-profile-once", "label": "分价成本带",   "blocking": False, "timeout_s": 600},
    {"cli": "--run-factor-lab",          "label": "因子实验室",   "blocking": False, "timeout_s": 300},
    {"cli": "--run-resonance-once",      "label": "四维共振",     "blocking": False, "timeout_s": 600},
    {"cli": "--run-tier-flow-once",      "label": "分层资金流",   "blocking": False, "timeout_s": 300},
]

MAX_WORKERS = int(os.environ.get("PLUGIN_TASK_WORKERS", "2"))


def run_one(task):
    label, cli = task["label"], task["cli"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "backend.main", cli],
            cwd=str(PROJECT_DIR), capture_output=True, text=True,
            timeout=int(task.get("timeout_s", 300)),
        )
        dt = round(time.monotonic() - t0, 1)
        if proc.returncode == 0:
            return {"label": label, "status": "ok", "seconds": dt, "tail": ""}
        tail = (proc.stderr or proc.stdout or "").strip()[-200:]
        return {"label": label, "status": "failed", "seconds": dt, "tail": tail}
    except subprocess.TimeoutExpired:
        return {"label": label, "status": "timeout", "seconds": round(time.monotonic() - t0, 1), "tail": ""}
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "status": "error", "seconds": round(time.monotonic() - t0, 1), "tail": str(exc)}


def main():
    if "--dry-run" in sys.argv:
        print(f"[插件任务][dry-run] 共 {len(TASKS)} 项，并发 {MAX_WORKERS}")
        for t in TASKS:
            print(f"  - {t['label']}: {t['cli']} (blocking={t['blocking']}, timeout={t['timeout_s']}s)")
        return 0
    print(f"[插件任务] 开始：共 {len(TASKS)} 项，并发 {MAX_WORKERS}")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(run_one, t): t for t in TASKS}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            line = f"[插件任务] {r['label']}: {r['status']} ({r['seconds']}s)"
            if r["tail"]:
                line += f" | {r['tail']}"
            print(line)

    by_label = {t["label"]: t for t in TASKS}
    failed = [r for r in results if r["status"] != "ok"]
    blocking_failed = [r for r in failed if by_label.get(r["label"], {}).get("blocking")]
    print(f"[插件任务] 完成：成功 {len(results) - len(failed)} / 失败 {len(failed)}")
    if blocking_failed:
        print(f"[插件任务] 阻断任务失败：{', '.join(r['label'] for r in blocking_failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
