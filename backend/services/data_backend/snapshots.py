"""统一快照读写封装。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd

from .trading_session import current_trading_session

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_BACKEND_DIR = PROJECT_DIR / "data" / "data_backend"
REPORT_DATA_BACKEND_DIR = PROJECT_DIR / "reports" / "data_backend"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_RAW_BASE = os.environ.get(
    "TASK_SNAPSHOT_RAW_BASE",
    "https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots",
)
_MEMORY_CACHE: Dict[str, dict] = {}
ASSET_TTLS = {
    "zt_pool": int(os.environ.get("DATA_BACKEND_ZT_POOL_TTL", "120")),
    "quotes": int(os.environ.get("DATA_BACKEND_QUOTES_TTL", "45")),
    "index_snapshot": int(os.environ.get("DATA_BACKEND_INDEX_TTL", "45")),
}


def _now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def _snapshot_path(asset: str) -> Path:
    return DATA_BACKEND_DIR / f"{asset}.json"


def _report_snapshot_path(asset: str) -> Path:
    return REPORT_DATA_BACKEND_DIR / f"{asset}.json"


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_local_snapshot(asset: str, payload: dict) -> None:
    DATA_BACKEND_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DATA_BACKEND_DIR.mkdir(parents=True, exist_ok=True)
    _MEMORY_CACHE[asset] = payload
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    _snapshot_path(asset).write_text(text, encoding="utf-8")
    _report_snapshot_path(asset).write_text(text, encoding="utf-8")


def _read_local_snapshot(asset: str) -> Optional[dict]:
    if asset in _MEMORY_CACHE:
        return _MEMORY_CACHE[asset]
    payload = _read_json(_snapshot_path(asset))
    if not payload:
        payload = _read_json(_report_snapshot_path(asset))
    if payload:
        _MEMORY_CACHE[asset] = payload
    return payload


def _fetch_remote_snapshot_json(asset: str) -> Optional[dict]:
    import requests

    url = f"{SNAPSHOT_RAW_BASE}/reports/data_backend/{asset}.json"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _age_seconds(fetched_at: Any) -> Optional[int]:
    fetched_dt = _parse_dt(fetched_at)
    if fetched_dt is None:
        return None
    return max(int((_now() - fetched_dt).total_seconds()), 0)


def _is_payload_covering_codes(payload: Optional[dict], request_codes: Optional[list[str]]) -> bool:
    if not request_codes:
        return True
    if not payload:
        return False
    cached_codes = set(payload.get("codes") or [])
    return set(request_codes).issubset(cached_codes)


def _is_fresh(asset: str, payload: Optional[dict]) -> bool:
    if not payload:
        return False
    fetched = _parse_dt(payload.get("fetched_at"))
    if fetched is None:
        return False
    now = _now()
    # 跨自然日快照一律不视为新鲜，避免昨日日终数据冒充今日实时。
    if fetched.date() != now.date():
        return False
    session = current_trading_session(now)
    if session != "open":
        return True
    age = _age_seconds(payload.get("fetched_at"))
    if age is None:
        return False
    return age <= ASSET_TTLS[asset]


def _status_for_cache(asset: str, source: str) -> Tuple[str, Optional[str]]:
    session = current_trading_session(_now())
    if session != "open":
        return "stale", None
    # 交易时段内走了缓存/远程快照, 说明本次现拉未成功, 标记降级并记录本应使用的上游
    return "degraded", "live"


def _meta(asset: str, payload: Optional[dict], source: str, status: str, degraded_from: Optional[str], error: Optional[str] = None) -> dict:
    records = payload.get("records") if payload else None
    if isinstance(records, list):
        record_count = len(records)
    elif isinstance(records, dict):
        record_count = 1 if records else 0
    else:
        record_count = 0
    return {
        "asset": asset,
        "source": source,
        "fetched_at": payload.get("fetched_at") if payload else None,
        "age_seconds": _age_seconds(payload.get("fetched_at")) if payload else None,
        "record_count": record_count,
        "status": status,
        "trading_session": current_trading_session(_now()),
        "degraded_from": degraded_from,
        "error": error,
    }


def _to_dataframe(records: Any) -> Optional[pd.DataFrame]:
    if not isinstance(records, list) or not records:
        return None
    return pd.DataFrame(records)


def _read_asset(
    asset: str,
    fetcher: Callable[..., Any],
    fetch_args: tuple = (),
    prefer_dataframe: bool = True,
    request_codes: Optional[list[str]] = None,
) -> Tuple[Any, dict]:
    local = _read_local_snapshot(asset)
    local_usable = _is_payload_covering_codes(local, request_codes)
    if local_usable and _is_fresh(asset, local):
        if prefer_dataframe:
            return _to_dataframe(local.get("records")) if local else None, _meta(asset, local, local.get("source") or "cache", "fresh", None)
        return (local.get("records") if local else None), _meta(asset, local, local.get("source") or "cache", "fresh", None)

    try:
        fetched = fetcher(*fetch_args)
        if fetched is not None and (not prefer_dataframe or not getattr(fetched, "empty", False)):
            records = fetched.to_dict("records") if prefer_dataframe else fetched
            payload = {
                "source": "live",
                "fetched_at": _now().isoformat(),
                "records": records,
                "codes": sorted(set(request_codes or [])),
            }
            _write_local_snapshot(asset, payload)
            return fetched, _meta(asset, payload, "live", "fresh", None)
    except Exception as exc:  # noqa: BLE001
        fetch_error = str(exc)
    else:
        fetch_error = None

    if local and local_usable:
        source = local.get("source") or "cache"
        status, degraded_from = _status_for_cache(asset, source)
        if prefer_dataframe:
            return _to_dataframe(local.get("records")), _meta(asset, local, source, status, degraded_from, fetch_error)
        return local.get("records"), _meta(asset, local, source, status, degraded_from, fetch_error)

    remote = _fetch_remote_snapshot_json(asset)
    if remote:
        status, degraded_from = _status_for_cache(asset, "data-snapshots")
        if prefer_dataframe:
            return _to_dataframe(remote.get("records")), _meta(asset, remote, "data-snapshots", status, degraded_from, fetch_error)
        return remote.get("records"), _meta(asset, remote, "data-snapshots", status, degraded_from, fetch_error)

    return None, {
        "asset": asset,
        "source": "none",
        "fetched_at": None,
        "age_seconds": None,
        "record_count": 0,
        "status": "unavailable",
        "trading_session": current_trading_session(_now()),
        "degraded_from": None,
        "error": fetch_error,
    }


def read_zt_pool(fetcher: Callable[[], Any]) -> Tuple[Optional[pd.DataFrame], dict]:
    return _read_asset("zt_pool", fetcher)


def read_quotes(codes: list[str], fetcher: Callable[[list[str]], Any]) -> Tuple[Optional[pd.DataFrame], dict]:
    return _read_asset("quotes", fetcher, fetch_args=(codes,), request_codes=codes)


def read_index_snapshot(fetcher: Callable[[], Any]) -> Tuple[Optional[dict], dict]:
    return _read_asset("index_snapshot", fetcher, prefer_dataframe=False)


def read_snapshot_meta(asset: str) -> dict:
    """只读元信息: 仅读本地快照文件, 绝不触发现拉。供状态面板/registry 使用。"""
    payload = _read_local_snapshot(asset)
    if not payload:
        return {
            "asset": asset,
            "source": "none",
            "fetched_at": None,
            "age_seconds": None,
            "record_count": 0,
            "status": "unavailable",
            "trading_session": current_trading_session(_now()),
            "degraded_from": None,
            "error": None,
        }
    source = payload.get("source") or "cache"
    status = "fresh" if _is_fresh(asset, payload) else "stale"
    return _meta(asset, payload, source, status, None)


def refresh_market_snapshots(
    watchlist_codes: list[str],
    zt_fetcher: Optional[Callable[[], Any]] = None,
    quotes_fetcher: Optional[Callable[[list[str]], Any]] = None,
    index_fetcher: Optional[Callable[[], Any]] = None,
) -> dict:
    """高频 cron 刷新行情快照，并同步写入 reports/data_backend。"""
    if zt_fetcher is None:
        from ...agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool as zt_fetcher
    if quotes_fetcher is None:
        from ...agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes as quotes_fetcher
    if index_fetcher is None:
        from ...agents.layer1_data_collector.sources.index_data import fetch_index_snapshot as index_fetcher

    assets: Dict[str, dict] = {}
    errors = []
    zt_codes: list[str] = []

    try:
        zt_pool = zt_fetcher()
        if zt_pool is not None and not zt_pool.empty:
            payload = {
                "source": (zt_pool.attrs.get("source_meta") or {}).get("source", "live"),
                "fetched_at": _now().isoformat(),
                "records": zt_pool.to_dict("records"),
            }
            _write_local_snapshot("zt_pool", payload)
            assets["zt_pool"] = _meta("zt_pool", payload, payload["source"], "fresh", None)
            zt_codes = [str(code).zfill(6) for code in zt_pool.get("代码", []).tolist()]
        else:
            assets["zt_pool"] = _meta("zt_pool", None, "none", "unavailable", None, "zt_pool empty")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"zt_pool: {exc}")
        assets["zt_pool"] = _meta("zt_pool", None, "none", "unavailable", None, str(exc))

    quote_codes = sorted({str(code).zfill(6) for code in (watchlist_codes or []) if str(code).strip()} | set(zt_codes))
    try:
        quotes = quotes_fetcher(quote_codes) if quote_codes else pd.DataFrame()
        if quotes is not None and not quotes.empty:
            payload = {
                "source": "live",
                "fetched_at": _now().isoformat(),
                "records": quotes.to_dict("records"),
                "codes": quote_codes,
            }
            _write_local_snapshot("quotes", payload)
            assets["quotes"] = _meta("quotes", payload, payload["source"], "fresh", None)
        else:
            assets["quotes"] = _meta("quotes", None, "none", "unavailable", None, "quotes empty")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"quotes: {exc}")
        assets["quotes"] = _meta("quotes", None, "none", "unavailable", None, str(exc))

    try:
        index_snapshot = index_fetcher()
        if index_snapshot:
            payload = {
                "source": index_snapshot.get("source") or "live",
                "fetched_at": index_snapshot.get("fetched_at") or _now().isoformat(),
                "records": index_snapshot,
            }
            _write_local_snapshot("index_snapshot", payload)
            assets["index_snapshot"] = _meta("index_snapshot", payload, payload["source"], "fresh", None)
        else:
            assets["index_snapshot"] = _meta("index_snapshot", None, "none", "unavailable", None, "index_snapshot empty")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"index_snapshot: {exc}")
        assets["index_snapshot"] = _meta("index_snapshot", None, "none", "unavailable", None, str(exc))

    return {
        "status": "ok" if not errors else "partial",
        "generated_at": _now().isoformat(),
        "assets": assets,
        "errors": errors,
    }
