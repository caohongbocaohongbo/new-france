"""日K历史持久化（§12.4 第3步）：最小增量、按需补洞、复权版本失效、备份与恢复。

单一 SQLite 表 bars_daily，主键 (code, trade_date, adjustment) 做幂等 upsert；
不新建平行数据平台，复用现有 backend.db.database 引擎。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from backend.db.database import engine
from backend.plugins.common import BEIJING_TZ, now_beijing
from backend.services.trading_calendar import is_trading_day, trading_days_between_dates

TABLE = "bars_daily"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars_daily (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    adjustment TEXT NOT NULL DEFAULT 'raw',
    adjustment_version TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    source TEXT,
    is_final INTEGER DEFAULT 0,
    fetched_at TEXT,
    PRIMARY KEY (code, trade_date, adjustment)
);
CREATE INDEX IF NOT EXISTS idx_bars_daily_code_date ON bars_daily(code, trade_date);
"""

_COLUMN_MAP = {
    "日期": "trade_date", "date": "trade_date",
    "开盘": "open", "open": "open",
    "最高": "high", "high": "high",
    "最低": "low", "low": "low",
    "收盘": "close", "close": "close",
    "成交量": "volume", "volume": "volume", "vol": "volume",
    "成交额": "amount", "amount": "amount",
}


def ensure_schema() -> None:
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                cur.execute(stmt)
        raw.commit()
    finally:
        raw.close()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns={k: v for k, v in _COLUMN_MAP.items() if k in df.columns})
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def upsert_daily_bars(code: str, df: pd.DataFrame, *, adjustment: str = "raw",
                      adjustment_version: Optional[str] = None, source: Optional[str] = None,
                      is_final: bool = False) -> int:
    """幂等 upsert 日K；返回写入/更新行数。"""
    if df is None or df.empty:
        return 0
    ensure_schema()
    normalized = _normalize(df)
    if "trade_date" not in normalized.columns:
        return 0
    code = str(code).zfill(6)
    fetched_at = now_beijing().isoformat()
    rows = []
    for _, r in normalized.iterrows():
        td = str(r.get("trade_date"))[:10]
        if not td:
            continue
        rows.append((
            code, td, adjustment, adjustment_version,
            r.get("open"), r.get("high"), r.get("low"), r.get("close"),
            r.get("volume"), r.get("amount"), source, int(bool(is_final)), fetched_at,
        ))
    if not rows:
        return 0
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.executemany(
            f"""INSERT INTO {TABLE}
                (code, trade_date, adjustment, adjustment_version,
                 open, high, low, close, volume, amount, source, is_final, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(code, trade_date, adjustment) DO UPDATE SET
                 adjustment_version=excluded.adjustment_version,
                 open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                 volume=excluded.volume, amount=excluded.amount,
                 source=excluded.source, is_final=excluded.is_final, fetched_at=excluded.fetched_at""",
            rows,
        )
        raw.commit()
        return len(rows)
    finally:
        raw.close()


def read_daily_bars(code: str, start: Optional[str] = None, end: Optional[str] = None,
                    adjustment: str = "raw") -> pd.DataFrame:
    ensure_schema()
    sql = f"SELECT trade_date, open, high, low, close, volume, amount, source, is_final, adjustment_version FROM {TABLE} WHERE code = :c AND adjustment = :a"
    params = {"c": str(code).zfill(6), "a": adjustment}
    if start:
        sql += " AND trade_date >= :s"; params["s"] = str(start)[:10]
    if end:
        sql += " AND trade_date <= :e"; params["e"] = str(end)[:10]
    sql += " ORDER BY trade_date"
    return pd.read_sql(sql, engine, params=params)


def missing_dates(code: str, start: str, end: str, adjustment: str = "raw") -> list:
    """按交易日历补洞：闭区间内应存在的交易日减去已入库日期。"""
    s = date.fromisoformat(str(start)[:10])
    e = date.fromisoformat(str(end)[:10])
    expected = [d.isoformat() for d in trading_days_between_dates(s, e) if is_trading_day(d)]
    existing = set(read_daily_bars(code, start, end, adjustment)["trade_date"].tolist())
    return [d for d in expected if d not in existing]


def delete_adjustment(code: str, adjustment: str) -> int:
    """复权版本失效：删除该 (code, adjustment) 全部行。"""
    ensure_schema()
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(f"DELETE FROM {TABLE} WHERE code = :c AND adjustment = :a",
                    {"c": str(code).zfill(6), "a": adjustment})
        raw.commit()
        return cur.rowcount
    finally:
        raw.close()


def backup(target_path: Path) -> Path:
    """在线备份 API（源库保持可写）；失败抛异常，不假装成功。"""
    ensure_schema()
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    src = engine.raw_connection()
    dst = sqlite3.connect(str(target_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target_path


def verify_backup(target_path: Path) -> bool:
    """恢复演练校验：备份可打开、表存在、行数可读。"""
    path = Path(target_path)
    if not path.exists():
        return False
    con = sqlite3.connect(str(path))
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,))
        if cur.fetchone() is None:
            return False
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        cur.fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()
