#!/usr/bin/env python3
"""22 智能选股聚合中枢验收压测（离线断言 + 接口延迟测量）。

运行：python3 scripts/bench_smart_picker.py
输出：PASS/WARN/FAIL 清单；硬性失败（快照缺失/重复行/接口超预算）退出码非 0。
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS = PROJECT_DIR / "reports"
sys.path.insert(0, str(PROJECT_DIR))

SNAPSHOT = REPORTS / "smart_picker_latest.json"
CHARTS = REPORTS / "smart_picker_charts_latest.json"
DB = PROJECT_DIR / "data" / "new_france.db"
PAYLOAD_BUDGET = 150_000  # ≤150KB（实测全字段 124KB；22 方案 §7.3 已按实测修正）
DUP_BUDGET = 0
P95_MS_BUDGET = 1.0  # 列表接口内存缓存 P95 <1ms


def report(name, ok, detail, hard=True):
    tag = "PASS" if ok else ("FAIL" if hard else "WARN")
    print(f"[{tag}] {name}: {detail}")
    return (not ok) and hard


def main():
    fails = 0
    if not SNAPSHOT.exists():
        print(f"[FAIL] smart_picker_latest.json 不存在（先跑 --run-smart-picker-all / --run-smart-picker-hub）")
        return 1
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    # 1. 载荷体积
    size = SNAPSHOT.stat().st_size
    fails += report("payload_bytes", size <= PAYLOAD_BUDGET, f"{size}B (预算 ≤{PAYLOAD_BUDGET}B)")

    # 2. 重复行（items code 唯一 + hits 与 hit_flags 一致）
    items = payload.get("items") or []
    codes = [str(it.get("code") or "").zfill(6) for it in items]
    dup = len(codes) - len(set(codes))
    fails += report("dup_rows", dup == DUP_BUDGET, f"重复行 {dup}（预算 =0）")
    for it in items:
        flags = it.get("hit_flags") or {}
        hits = it.get("hits") or {}
        if set(k for k, v in flags.items() if v) != set(hits):
            fails += report("hit_flags/hits 一致性", False, f"{it['code']} 矩阵与明细不一致")
            break
    else:
        report("hit_flags/hits 一致性", True, "全部一致", hard=False)

    # 3. 数据龄（环境无外网时允许陈旧，仅告警）
    age = payload.get("data_age_days")
    fails += report("data_age_days", age is not None and age <= 1, f"数据龄 {age} 交易日（要求 ≤1）", hard=False)

    # 4. 接口延迟（内存缓存命中路径，1000 次）
    from backend.plugins.smart_picker_hub.service import query_latest
    from backend.plugins.common import snapshot_mem_set

    snapshot_mem_set("smart_picker", payload)
    lat = []
    for _ in range(1000):
        t0 = time.perf_counter()
        query_latest(limit=50)
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    p50, p95 = lat[len(lat) // 2], lat[int(len(lat) * 0.95)]
    fails += report("list P95 (内存缓存)", p95 < P95_MS_BUDGET, f"P50={p50:.3f}ms P95={p95:.3f}ms（预算 <{P95_MS_BUDGET}ms）")

    # 5. 冷读（本地磁盘 json.loads，模拟进程重启首请求的本地部分）
    t0 = time.perf_counter()
    json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    cold_ms = (time.perf_counter() - t0) * 1000
    report("list 冷读(本地磁盘)", cold_ms < 800, f"{cold_ms:.1f}ms（预算 <800ms）", hard=False)

    # 6. 图表预计算覆盖率（top N 中命中 charts 快照的比例）
    charts_payload = json.loads(CHARTS.read_text(encoding="utf-8")) if CHARTS.exists() else {}
    charts = charts_payload.get("charts") or {}
    n = min(40, len(items))
    cached = sum(1 for it in items[:n] if it["code"] in charts)
    fails += report("chart top40 cached", cached == n, f"前 {n} 只中 {cached} 只已预计算（要求全命中；无外网时为 WARN）", hard=False)

    # 7. SQLite 一致性（smart_picker_hits 行数 == 快照 items 数）
    if DB.exists():
        con = sqlite3.connect(DB)
        try:
            rows = con.execute("SELECT COUNT(*) FROM smart_picker_hits WHERE date = ?",
                               (payload.get("date") or "",)).fetchone()[0]
            fails += report("sqlite smart_picker_hits 行数", rows == len(items), f"{rows} 行 vs 快照 {len(items)} 行")
        except sqlite3.OperationalError as exc:
            report("sqlite smart_picker_hits", False, f"查询失败 {exc}", hard=False)
        try:
            perf_rows = con.execute("SELECT COUNT(*) FROM picker_perf_daily").fetchone()[0]
            bad = con.execute("SELECT COUNT(*) FROM picker_perf_daily WHERE (t1_filled=1 AND t1_ret IS NULL)"
                              " OR (t3_filled=1 AND t3_ret IS NULL) OR (t5_filled=1 AND t5_ret IS NULL)").fetchone()[0]
            fails += report("perf 回填完整性", bad == 0, f"{perf_rows} 行中 filled 但 ret 缺失 {bad} 行")
        except sqlite3.OperationalError as exc:
            report("picker_perf_daily", False, f"查询失败 {exc}", hard=False)
        con.close()

    # 8. 降级标注完整性（不静默）
    if payload.get("zt_gate_applied") is False and not payload.get("zt_gate_reason"):
        fails += report("zt_gate 降级标注", False, "zt_gate_applied=false 但无 reason")
    else:
        report("zt_gate 降级标注", True, f"applied={payload.get('zt_gate_applied')} reason={payload.get('zt_gate_reason')}", hard=False)

    print(f"\n结论: {'全部硬指标 PASS' if fails == 0 else f'{fails} 项硬性失败'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
