"""监控列表文件读写与去重。"""
import re
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
FRANCE_FILE = PROJECT_DIR / "data" / "france.md"

WATCHLIST_HEADER = (
    "# 涨停监控列表\n\n"
    "| 代码 | 名称 | 涨停日期 | 参考价 |\n"
    "|------|------|----------|--------|\n"
)


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
        match = re.match(
            r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|",
            line,
        )
        if match:
            entries.append({
                "code": match.group(1),
                "name": match.group(2).strip(),
                "zt_date": match.group(3),
                "ref_price": float(match.group(4)),
            })

    unique, _ = dedupe_watchlist_entries(entries)
    return unique


def write_watchlist(entries: List[Dict], path: Path = FRANCE_FILE) -> int:
    """写入监控列表文件，写入前再次去重。"""
    unique, _ = dedupe_watchlist_entries(entries)
    lines = [
        f"| {entry['code']} | {entry['name']} | {entry['zt_date']} | {entry['ref_price']:.2f} |"
        for entry in unique
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WATCHLIST_HEADER + "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(unique)


def normalize_watchlist_file(path: Path = FRANCE_FILE) -> Tuple[List[Dict], int]:
    """清理文件中的重复监控项；无重复时不改写文件。"""
    if not path.exists():
        return [], 0

    content = path.read_text(encoding="utf-8")
    entries = []
    for line in content.splitlines():
        match = re.match(
            r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|",
            line,
        )
        if match:
            entries.append({
                "code": match.group(1),
                "name": match.group(2).strip(),
                "zt_date": match.group(3),
                "ref_price": float(match.group(4)),
            })

    unique, duplicate_count = dedupe_watchlist_entries(entries)
    if duplicate_count:
        write_watchlist(unique, path)
    return unique, duplicate_count
