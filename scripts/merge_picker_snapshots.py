#!/usr/bin/env python3
"""22 方案：picker 快照只升不降合并（并发 push 时取较新，防云端数据回退）。

用法：python3 merge_picker_snapshots.py <keep_dir> <work_dir> <rel_path...>
规则（按顶层 "date" 字段 YYYY-MM-DD 比较）：
- work 缺失且 keep 存在 → 从 keep 恢复（GH runner 没产数时不丢数据）
- keep.date > work.date → 用 keep 覆盖 work（本地产物更旧时不回退云端）
- 其余情况不动 work（本地更新优先）
"""
import json
import os
import shutil
import sys


def _date(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return str(json.load(fh).get("date") or "")
    except Exception:  # noqa: BLE001 损坏文件按无日期处理
        return ""


def main(argv):
    if len(argv) < 3:
        print("用法: merge_picker_snapshots.py <keep_dir> <work_dir> <rel_path...>", file=sys.stderr)
        return 2
    keep_dir, work_dir = argv[0], argv[1]
    for rel in argv[2:]:
        keep_p = os.path.join(keep_dir, rel)
        work_p = os.path.join(work_dir, rel)
        if not os.path.exists(work_p):
            if os.path.exists(keep_p):
                os.makedirs(os.path.dirname(work_p), exist_ok=True)
                shutil.copy(keep_p, work_p)
                print(f"restored: {rel}")
            continue
        if not os.path.exists(keep_p):
            continue
        if _date(keep_p) > _date(work_p):
            shutil.copy(keep_p, work_p)
            print(f"kept newer branch version: {rel} ({_date(keep_p)} > {_date(work_p)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
