"""可选 SQLite 指标快照与回放。"""
import json
import math
import sqlite3
from pathlib import Path


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


class RadarReplay:
    def __init__(self, path, timeout: float = 5):
        self.path = Path(path)
        self.timeout = timeout

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=self.timeout)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radar_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                code TEXT NOT NULL,
                stage TEXT,
                smart_money_score REAL,
                launch_score REAL,
                metrics_json TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_snapshot_code_ts ON radar_snapshot(code, ts)")
        return conn

    def dump(self, rows: list) -> None:
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO radar_snapshot(ts, code, stage, smart_money_score, launch_score, metrics_json) VALUES (?, ?, ?, ?, ?, ?)",
                [(
                    str(row.get("time") or ""), str(row.get("code") or "").zfill(6), row.get("stage"),
                    row.get("smart_money_score"), row.get("launch_score"),
                    json.dumps(_json_safe(row.get("metrics") or {}), ensure_ascii=False, allow_nan=False),
                ) for row in rows],
            )

    def query(self, code: str = None, start: str = None, end: str = None, limit: int = 1000) -> list:
        if not self.path.exists():
            return []
        clauses, params = [], []
        if code:
            clauses.append("code = ?"); params.append(str(code).zfill(6))
        if start:
            clauses.append("ts >= ?"); params.append(str(start))
        if end:
            clauses.append("ts <= ?"); params.append(str(end))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT ts, code, stage, smart_money_score, launch_score, metrics_json FROM radar_snapshot{where} ORDER BY ts ASC LIMIT ?"
        params.append(max(1, min(int(limit), 10000)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{
            "time": row[0], "code": row[1], "stage": row[2],
            "smart_money_score": row[3], "launch_score": row[4],
            "metrics": json.loads(row[5]),
        } for row in rows]
