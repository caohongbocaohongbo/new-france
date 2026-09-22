"""监控列表文件读写与去重。"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
FRANCE_FILE = PROJECT_DIR / "data" / "france.md"

WATCHLIST_HEADER = (
    "# 涨停监控列表\n\n"
    "| 代码 | 名称 | 涨停日期 | 参考价 | 加入时间 | 封板时间 | 炸板次数 | 涨停次数 | 连板数 |\n"
    "|------|------|----------|--------|----------|----------|----------|----------|--------|\n"
)

# 默认值（旧格式兼容）
DEFAULTS = {
    "added_date": "",
    "seal_time": "0",
    "break_count": "0",
    "zt_count": "0",
    "consecutive": "0",
}

# 北交所代码段：43/83/87/88（老段）+ 920（新段）。行情源当前不支持北交所，监控列表不收录。
BSE_PREFIXES = ("920", "43", "83", "87", "88")


def is_bse_code(code) -> bool:
    """判断是否为北交所代码。"""
    return str(code or "").zfill(6).startswith(BSE_PREFIXES)


def _parse_line(line: str) -> Optional[Dict]:
    """解析单行，兼容旧格式(4列)、8列、和9列(含炸板次数)"""
    # 9列格式：含炸板次数
    match = re.match(
        r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|"
        r"\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|",
        line,
    )
    if match:
        return {
            "code": match.group(1),
            "name": match.group(2).strip(),
            "zt_date": match.group(3),
            "ref_price": float(match.group(4)),
            "added_date": match.group(5).strip() or match.group(3),
            "seal_time": match.group(6).strip() or "0",
            "break_count": match.group(7).strip() or "0",
            "zt_count": match.group(8).strip() or "0",
            "consecutive": match.group(9).strip() or "0",
        }

    # 8列格式兼容：无炸板次数
    match = re.match(
        r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|"
        r"\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|",
        line,
    )
    if match:
        return {
            "code": match.group(1),
            "name": match.group(2).strip(),
            "zt_date": match.group(3),
            "ref_price": float(match.group(4)),
            "added_date": match.group(5).strip() or match.group(3),
            "seal_time": match.group(6).strip() or "0",
            "break_count": "0",
            "zt_count": match.group(7).strip() or "0",
            "consecutive": match.group(8).strip() or "0",
        }

    # 旧格式兼容：4列
    match = re.match(
        r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|",
        line,
    )
    if match:
        return {
            "code": match.group(1),
            "name": match.group(2).strip(),
            "zt_date": match.group(3),
            "ref_price": float(match.group(4)),
            "added_date": match.group(3),
            "seal_time": "0",
            "break_count": "0",
            "zt_count": "0",
            "consecutive": "0",
        }
    return None


def dedupe_watchlist_entries(entries: List[Dict]) -> Tuple[List[Dict], int]:
    """按股票代码去重，保留首次进入监控列表的记录。"""
    seen = set()
    unique = []
    duplicate_count = 0

    for entry in entries:
        code = entry.get("code")
        if not code:
            continue
        if code in seen:
            duplicate_count += 1
            continue
        seen.add(code)
        unique.append(entry)

    return unique, duplicate_count


def parse_watchlist(path: Path = FRANCE_FILE) -> List[Dict]:
    """从 france.md 读取监控列表，并返回去重后的记录。"""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return []

    entries = []
    for line in content.splitlines():
        entry = _parse_line(line)
        if entry and not is_bse_code(entry["code"]):
            entries.append(entry)

    unique, _ = dedupe_watchlist_entries(entries)
    return unique


def write_watchlist(entries: List[Dict], path: Path = FRANCE_FILE) -> int:
    """写入监控列表文件，写入前再次去重并剔除北交所代码。"""
    unique, _ = dedupe_watchlist_entries(
        [entry for entry in entries if entry and not is_bse_code(entry.get("code"))]
    )
    lines = [
        f"| {entry['code']} | {entry['name']} | {entry['zt_date']} | {entry['ref_price']:.2f} | "
        f"{entry.get('added_date', entry['zt_date'])} | "
        f"{entry.get('seal_time', '0')} | "
        f"{entry.get('break_count', '0')} | "
        f"{entry.get('zt_count', '0')} | "
        f"{entry.get('consecutive', '0')} |"
        for entry in unique
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WATCHLIST_HEADER + "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(unique)


def update_watchlist_entry(code: str, updates: Dict, path: Path = FRANCE_FILE) -> bool:
    """更新单条监控记录的部分字段。返回是否成功找到并更新。"""
    entries = parse_watchlist(path)
    updated = False
    for entry in entries:
        if entry["code"] == code:
            entry.update(updates)
            updated = True
            break
    if updated:
        write_watchlist(entries, path)
    return updated


def normalize_watchlist_file(path: Path = FRANCE_FILE) -> Tuple[List[Dict], int]:
    """清理文件中的重复监控项；无重复时不改写文件。同时升级旧格式到新格式。"""
    if not path.exists():
        return [], 0

    content = path.read_text(encoding="utf-8")
    entries = []
    bse_removed = 0
    for line in content.splitlines():
        entry = _parse_line(line)
        if not entry:
            continue
        if is_bse_code(entry["code"]):
            bse_removed += 1
            continue
        entries.append(entry)

    unique, duplicate_count = dedupe_watchlist_entries(entries)

    # 检查是否需要升级格式（旧格式4/8列 → 新格式9列）
    needs_upgrade = "| 炸板次数" not in content

    if duplicate_count or needs_upgrade or bse_removed:
        write_watchlist(unique, path)
    return unique, duplicate_count


def count_zt_30days(code: str, path: Path = FRANCE_FILE, days: int = 30) -> int:
    """统计某股票在配置周期内出现在监控列表中的次数（即涨停次数）。"""
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")

    entries = parse_watchlist(path)
    count = 0
    for e in entries:
        if e["code"] == code and e["zt_date"] >= cutoff:
            count += 1
    return count
