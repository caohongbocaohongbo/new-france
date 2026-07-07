"""统一数据资产元信息聚合。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"
BEIJING_TZ = timezone(timedelta(hours=8))

OPTIONAL_SOURCE_HEALTH_FILE = DATA_DIR / "source_health.json"
PRINCIPAL_CAPITAL_CACHE_FILE = DATA_DIR / "principal_capital_cache.json"
PRINCIPAL_CAPITAL_HEALTH_FILE = DATA_DIR / "principal_capital_source_health.json"
LATEST_REPORT_FILE = REPORT_DIR / "latest.json"


def _now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _build_asset(
    asset: str,
    source: str,
    fetched_at: Optional[Any],
    record_count: int,
    status: str,
    trading_session: str,
    degraded_from: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    fetched_dt = _parse_dt(fetched_at)
    age_seconds = None
    fetched_at_text = None
    if fetched_dt is not None:
        fetched_at_text = fetched_dt.isoformat()
        age_seconds = max(int((_now() - fetched_dt).total_seconds()), 0)
    return {
        "asset": asset,
        "source": source,
        "fetched_at": fetched_at_text,
        "age_seconds": age_seconds,
        "record_count": int(record_count or 0),
        "status": status,
        "trading_session": trading_session,
        "degraded_from": degraded_from,
        "error": error,
    }


def _unavailable_asset(asset: str, trading_session: str, error: Optional[str] = None) -> Dict[str, Any]:
    return _build_asset(
        asset=asset,
        source="none",
        fetched_at=None,
        record_count=0,
        status="unavailable",
        trading_session=trading_session,
        error=error,
    )


def _read_latest_screening_payload() -> dict:
    return _read_json(LATEST_REPORT_FILE, {})


def _resolve_trading_session(asset: str) -> str:
    from .trading_session import current_trading_session

    session = current_trading_session()
    if asset not in {"quotes", "index_snapshot"} and session in {"pre", "post"}:
        return "closed"
    return session


def _read_principal_capital_asset() -> Dict[str, Any]:
    trading_session = _resolve_trading_session("principal_capital")
    cache = _read_json(PRINCIPAL_CAPITAL_CACHE_FILE, {})
    records = cache.get("records") or []
    fetched_at = cache.get("fetched_at")
    health = _read_json(PRINCIPAL_CAPITAL_HEALTH_FILE, {})
    source = "cache"
    for key in ("eastmoney", "akshare"):
        if key in health:
            source = key
            break
    if not fetched_at and health.get("updated_at"):
        fetched_at = health.get("updated_at")
    if not records and not fetched_at:
        return _unavailable_asset("principal_capital", trading_session)
    asset = _build_asset(
        asset="principal_capital",
        source=source,
        fetched_at=fetched_at,
        record_count=len(records),
        status="fresh" if records else "stale",
        trading_session=trading_session,
        degraded_from=None if source not in {"cache", "data-snapshots"} else "eastmoney",
    )
    if asset["age_seconds"] is not None and asset["age_seconds"] > 1800:
        asset["status"] = "stale"
    return asset


def _read_optional_source_asset(source_key: str = "national_team") -> Dict[str, Any]:
    trading_session = _resolve_trading_session(source_key)
    payload = _read_json(OPTIONAL_SOURCE_HEALTH_FILE, {"sources": {}})
    item = (payload.get("sources") or {}).get(source_key)
    if not item:
        return _unavailable_asset(source_key, trading_session)
    source_status = item.get("source_status") or {}
    fetched_at = source_status.get("fetched_at")
    record_count = int(((item.get("data") or {}).get("record_count")) or 0)
    errors = source_status.get("errors") or []
    status = "fresh"
    max_age_hours = int(item.get("max_age_hours") or 36)
    asset = _build_asset(
        asset=source_key,
        source=source_status.get("source") or "none",
        fetched_at=fetched_at,
        record_count=record_count,
        status=status,
        trading_session=trading_session,
        error=errors[0] if errors else item.get("error"),
    )
    if asset["fetched_at"] is None:
        asset["status"] = "unavailable"
    elif asset["age_seconds"] is not None and asset["age_seconds"] > max_age_hours * 3600:
        asset["status"] = "stale"
    if asset["error"]:
        asset["status"] = "degraded"
    return asset


def _read_screening_asset(asset: str) -> Dict[str, Any]:
    trading_session = _resolve_trading_session(asset)
    payload = _read_latest_screening_payload()
    data = (payload.get("data_assets") or {}).get(asset)
    if not data:
        return _unavailable_asset(asset, trading_session)
    return _build_asset(
        asset=asset,
        source=data.get("source") or "none",
        fetched_at=data.get("fetched_at"),
        record_count=int(data.get("record_count") or 0),
        status=data.get("status") or "unavailable",
        trading_session=trading_session,
        degraded_from=data.get("degraded_from"),
        error=data.get("error"),
    )


def _read_snapshot_asset(asset: str) -> Dict[str, Any]:
    """读取 data_backend 本地快照元信息(只读, 不触发现拉)。"""
    from . import snapshots

    trading_session = _resolve_trading_session(asset)
    try:
        meta = snapshots.read_snapshot_meta(asset)
    except Exception as exc:  # noqa: BLE001
        return _unavailable_asset(asset, trading_session, error=str(exc))
    # 以 registry 统一口径覆盖 trading_session, 保持全局一致
    meta["trading_session"] = trading_session
    return meta


def get_all_assets() -> List[Dict[str, Any]]:
    """聚合全部数据资产元信息，任一资产读取失败都降级为 unavailable。"""
    readers = [
        lambda: _read_snapshot_asset("zt_pool"),
        lambda: _read_snapshot_asset("quotes"),
        lambda: _read_snapshot_asset("index_snapshot"),
        _read_principal_capital_asset,
        lambda: _read_screening_asset("historical_kline"),
        _read_optional_source_asset,
    ]
    assets: List[Dict[str, Any]] = []
    names = ["zt_pool", "quotes", "index_snapshot", "principal_capital", "historical_kline", "national_team"]
    for name, reader in zip(names, readers):
        try:
            assets.append(reader())
        except Exception as exc:  # noqa: BLE001
            assets.append(_unavailable_asset(name, _resolve_trading_session(name), error=str(exc)))
    return assets

