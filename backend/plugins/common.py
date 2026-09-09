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
import queue as _queue
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
    "GITHUB_RAW_BASE",
    os.environ.get(
        "NF_SNAPSHOT_RAW_BASE",  # 兼容旧 env 名
        "https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots",
    ),
).rstrip("/")
REMOTE_CACHE_DIR = REPORT_DIR / ".cache"  # 磁盘 TTL 缓存（不进 git）


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


def _atomic_write(path: Path, text: str) -> None:
    """原子写：先写临时文件再 rename，避免读者读到半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ==== SSE 推送（Phase 3）：快照写入后广播给订阅者 ====

# 用线程安全的标准队列，避免 asyncio.Queue 跨事件循环绑定问题（测试 ASGI 与 uvicorn 均可用）
_sse_subscribers: list = []  # list[_queue.Queue]


def subscribe_sse():
    """返回一个订阅队列，SSE 端点消费它。"""
    q = _queue.Queue(maxsize=100)
    _sse_subscribers.append(q)
    return q


def unsubscribe_sse(q) -> None:
    try:
        _sse_subscribers.remove(q)
    except ValueError:
        pass


def publish_snapshot_update(name: str) -> None:
    """快照写入后广播快照名（非阻塞，无订阅者或队列满时忽略）。"""
    for q in list(_sse_subscribers):
        try:
            q.put_nowait(name)
        except Exception:  # noqa: BLE001 队列满等情况忽略
            pass


# ==== K 线共享缓存（18/19/20/21 共用，第七波 G5）====
# 当日 K 线内存缓存：同日内复用，避免四插件重复拉东财（东财请求量减半）
_kline_cache: dict = {}
_kline_cache_date: Optional[str] = None


def get_kline_cached(code: str, days: int = 130, fetcher=None):
    """当日 K 线内存缓存。同日内复用，18/19/20/21 共享，避免重复拉东财。"""
    global _kline_cache, _kline_cache_date
    today = datetime.now(BEIJING_TZ).date().isoformat()
    if _kline_cache_date != today:
        _kline_cache = {}
        _kline_cache_date = today
    code = str(code).zfill(6)
    if code not in _kline_cache:
        if fetcher is None:
            from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical as fetcher
        _kline_cache[code] = fetcher(code, days)
    return _kline_cache[code]


def kline_cache_clear() -> None:
    """清空 K 线缓存（测试/跨日重置用）。"""
    global _kline_cache, _kline_cache_date
    _kline_cache = {}
    _kline_cache_date = None


# ==== 快照内存缓存（router 列表接口 <1ms，第七波 G6）====
_snapshot_mem_cache: dict = {}


def snapshot_mem_get(name: str):
    """读快照内存缓存（<1ms）。"""
    return _snapshot_mem_cache.get(name)


def snapshot_mem_set(name: str, payload: dict) -> None:
    """写快照内存缓存。"""
    _snapshot_mem_cache[name] = payload


def snapshot_mem_pop(name: str) -> None:
    """快照内存缓存失效（SSE 写完后 pop）。"""
    _snapshot_mem_cache.pop(name, None)


def write_snapshot(name: str, payload: dict) -> Path:
    """双写快照：reports/<name>_latest.json + reports/data_backend/<name>_latest.json（原子）。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_BACKEND_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(json_safe(payload), ensure_ascii=False, indent=2, default=str)
    _atomic_write(latest_path(name), text)
    _atomic_write(data_backend_path(name), text)
    snapshot_mem_set(name, payload)  # 写完更新内存缓存，列表接口直接命中
    publish_snapshot_update(name)
    return latest_path(name)


def read_snapshot(name: str) -> Optional[dict]:
    path = latest_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_json_file(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _remote_cache_path(name: str) -> Path:
    return REMOTE_CACHE_DIR / f"{name}_remote.json"


def _read_remote_cache(name: str, ttl_seconds: int) -> Optional[dict]:
    """读磁盘 TTL 缓存；ttl_seconds<=0 或过期/损坏返回 None。"""
    if int(ttl_seconds) <= 0:
        return None
    path = _remote_cache_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["cached_at"])
        if (now_beijing() - cached_at).total_seconds() < int(ttl_seconds):
            return data.get("payload")
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
        return None
    return None


def _write_remote_cache(name: str, payload: dict) -> None:
    """写磁盘 TTL 缓存（best-effort，失败静默，不阻断主链路）。"""
    try:
        REMOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _remote_cache_path(name).write_text(
            json.dumps({"cached_at": now_beijing().isoformat(), "payload": payload},
                       ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError:  # noqa: BLE001
        pass


def read_snapshot_resilient(snapshot_name: str, timeout: float = 5.0, ttl_seconds: int = 600) -> dict:
    """22 方案 §2.4 规格：本地文件 → 磁盘 TTL → raw.githubusercontent(data-snapshots) 兜底。

    永不抛异常；失败返回 {"status":"no_data","_source":"unavailable","items":[],"reason":"..."}。
    函数本身不做内存缓存——内存缓存在各 router 的 _latest_cached() 一层完成。
    """
    name = str(snapshot_name)
    # 步骤 1/2：本地完成态优先（reports/ 主目录 + data_backend 目录）
    for path in (latest_path(name), data_backend_path(name)):
        payload = _read_json_file(path)
        if payload is not None:
            payload["_source"] = "local"
            return payload
    # 步骤 3：chip 专属拦截（本地专属，云端不发网络）
    if name == "chip_scanner":
        chip_remote_fetch = False
        try:
            from backend.plugins.chip_scanner.config import CONFIG as _CHIP_CONFIG
            chip_remote_fetch = bool(_CHIP_CONFIG.get("chip_remote_fetch", False))
        except Exception:  # noqa: BLE001 配置缺失按默认 False
            pass
        if not chip_remote_fetch:
            return {"status": "no_data", "_source": "local_only", "items": [],
                    "reason": "chip_remote_fetch_disabled"}
    # 步骤 3.5：磁盘 TTL 缓存（未过期即命中，不再发网络）
    payload = _read_remote_cache(name, ttl_seconds)
    if payload is not None:
        payload["_source"] = "snapshot"
        return payload
    # 步骤 4：网络兜底（httpx，超时可配置）
    try:
        import httpx  # 延迟导入
        url = f"{SNAPSHOT_RAW_BASE}/reports/data_backend/{name}_latest.json"
        resp = httpx.get(url, timeout=float(timeout))
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("远端返回非 JSON 对象")
    except Exception as exc:  # noqa: BLE001 永不抛异常
        code = "err"
        resp_obj = getattr(exc, "response", None)
        if resp_obj is not None and getattr(resp_obj, "status_code", None):
            code = str(resp_obj.status_code)
        logger.info("远程快照拉取失败(%s): %s", name, exc)
        return {"status": "no_data", "_source": "unavailable", "items": [],
                "reason": f"remote_fetch_failed:{code}:{type(exc).__name__}"}
    payload["_source"] = "snapshot"
    if int(ttl_seconds) > 0:
        _write_remote_cache(name, payload)
    return payload


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


def read_code_kline(code: str, days: int = 60) -> list:
    """个股日线 OHLC（供前端副图），复用历史 K 线源，失败返回空列表。

    18/19/20/21 详情副图与 17 四维共振 K 线副图共用此入口，避免各插件重复拉源。
    返回 [{date, open, close, high, low, vol}, ...]（JSON 安全）。
    """
    try:
        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical

        hist = fetch_historical(str(code).zfill(6), int(days))
    except Exception:  # noqa: BLE001
        return []
    if hist is None or getattr(hist, "empty", True):
        return []

    def col(*names):
        for n in names:
            if n in hist.columns:
                return hist[n].tolist()
        return None

    dates = col("日期", "date")
    opens = col("开盘", "open")
    closes = col("收盘", "close")
    highs = col("最高", "high")
    lows = col("最低", "low")
    vols = col("成交量", "vol", "volume")
    records = []
    for i in range(len(hist)):
        records.append({
            "date": str(dates[i])[:10] if dates else None,
            "open": float_or(opens[i]) if opens else None,
            "close": float_or(closes[i]) if closes else None,
            "high": float_or(highs[i]) if highs else None,
            "low": float_or(lows[i]) if lows else None,
            "vol": float_or(vols[i]) if vols else None,
        })
    return json_safe(records)


def intraday_append(values: list, realtime_value) -> list:
    """盘中实时化：把当日实时值追加到序列末尾重算指标（realtime 无效则返回原序列）。

    与 17 d2_score_intraday 同口径：实时价等价于「日线收盘序列 + 今日实时价」。
    数据真实性：仅在调用方明确 is_intraday 且实时值有效时使用，序列不足/无实时值时透传，不伪造。
    """
    rt = float_or(realtime_value)
    if rt is None or rt <= 0:
        return values or []
    return list(values or []) + [rt]

