"""插件共享工具（新增，不改动任何既有插件）。

统一提供新插件常用的能力，避免每个插件重复造轮子：
- JSON 安全序列化（清理 NaN/Inf）
- 快照双写 reports/<name>_latest.json + reports/data_backend/<name>_latest.json
- data-snapshots 分支远程兜底读取
- 交易时段 / 交易日历委托入口
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parents[2]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_BACKEND_DIR = PROJECT_DIR / "reports" / "data_backend"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))

SNAPSHOT_RAW_BASE = os.environ.get(
    "NF_SNAPSHOT_RAW_BASE",
    "https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots",
).rstrip("/")


def json_safe(value):
    """清理 NaN/Infinity，保证 JSON 可被 FastAPI 直接响应。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def float_or(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def latest_path(name: str) -> Path:
    return REPORT_DIR / f"{name}_latest.json"


def data_backend_path(name: str) -> Path:
    return DATA_BACKEND_DIR / f"{name}_latest.json"


def write_snapshot(name: str, payload: dict) -> Path:
    """双写快照：reports/<name>_latest.json + reports/data_backend/<name>_latest.json。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_BACKEND_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(json_safe(payload), ensure_ascii=False, indent=2, default=str)
    latest_path(name).write_text(text, encoding="utf-8")
    data_backend_path(name).write_text(text, encoding="utf-8")
    return latest_path(name)


def read_snapshot(name: str) -> Optional[dict]:
    path = latest_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch_remote_snapshot(name: str) -> Optional[dict]:
    """从 data-snapshots 分支拉取 reports/<name>_latest.json，失败返回 None。"""
    import requests  # 延迟导入

    url = f"{SNAPSHOT_RAW_BASE}/reports/data_backend/{name}_latest.json"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("远程快照拉取失败(%s): %s", name, exc)
        return None


def read_snapshot_resilient(name: str) -> dict:
    """本地完成态优先，否则回退远程快照。"""
    local = read_snapshot(name)
    if local:
        return local
    remote = fetch_remote_snapshot(name)
    if remote:
        remote["_source"] = "snapshot"
        return remote
    return {"status": "empty", "items": [], "message": f"{name} 暂无数据，请先运行任务"}


def error_response(message: str) -> dict:
    return {"status": "error", "message": str(message), "items": []}


def db_append(table: str, rows: list) -> int:
    """最佳努力写入 SQLite（pandas to_sql append），失败返回 0 不阻断主链路。"""
    if not rows:
        return 0
    try:
        import pandas as pd
        from backend.db.database import engine, init_db

        init_db()
        pd.DataFrame(rows).to_sql(table, engine, if_exists="append", index=False)
        return len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.info("SQLite 写入 %s 失败(已忽略): %s", table, exc)
        return 0


def db_delete(table: str, where: dict) -> int:
    """按条件删除行（用于 date 唯一表的重复写入前清理），失败返回 0。"""
    if not where:
        return 0
    try:
        from sqlalchemy import text

        from backend.db.database import engine

        clauses = " AND ".join(f"{k} = :{k}" for k in where)
        with engine.begin() as conn:
            return conn.execute(text(f"DELETE FROM {table} WHERE {clauses}"), where).rowcount
    except Exception as exc:  # noqa: BLE001
        logger.info("SQLite 删除 %s 失败(已忽略): %s", table, exc)
        return 0


def db_query(sql: str, params: dict = None):
    """只读查询，返回 DataFrame；失败返回空 DataFrame。"""
    try:
        import pandas as pd
        from backend.db.database import engine

        return pd.read_sql(sql, engine, params=params or {})
    except Exception as exc:  # noqa: BLE001
        logger.info("SQLite 查询失败(已忽略): %s", exc)
        import pandas as pd

        return pd.DataFrame()

def market_filter(df, show_gem: bool = False, show_star: bool = False, min_amount: float = None):
    """全市场过滤（复用 principal_capital 原语，不重写判断逻辑）。

    默认仅主板（剔 300/301 创业板、688 科创板、ST）；show_gem/show_star 打开后
    纳入对应板块且主板票仍排前。返回新 DataFrame。
    """
    from backend.plugins.principal_capital.service import (
        _is_excluded_market, _is_main_board, _is_st_name, _is_star_market, _stock_code,
    )
    if df is None or getattr(df, "empty", True):
        return df
    import pandas as pd

    df = df.copy()
    df["code"] = df["code"].map(_stock_code)
    df["name"] = df["name"].fillna("").astype(str)
    keep = df["code"].map(_is_main_board)
    if show_gem:
        keep = keep | df["code"].map(_is_excluded_market)
    if show_star:
        keep = keep | df["code"].map(_is_star_market)
    keep = keep & ~df["name"].map(_is_st_name)
    df = df[keep]
    if min_amount is not None:
        df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce")
        df = df[df["total_amount"].isna() | (df["total_amount"] >= float(min_amount))]
    return df.reset_index(drop=True)

