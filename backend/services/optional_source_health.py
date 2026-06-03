"""可选数据源健康状态与生产展示门控。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_HEALTH_FILE = PROJECT_DIR / "data" / "source_health.json"
DEFAULT_CONFIG_FILE = PROJECT_DIR / "config" / "optional_sources.json"
BEIJING_TZ = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _iso_now() -> str:
    return _now().isoformat()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=BEIJING_TZ)
        return value.astimezone(BEIJING_TZ)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(BEIJING_TZ)
    except (TypeError, ValueError):
        return None


def _env_flag(name: str) -> Optional[bool]:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _source_env_key(source_key: str, suffix: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in source_key.upper())
    return f"OPTIONAL_SOURCE_{normalized}_{suffix}"


def _extract_errors(result: Dict[str, Any]) -> list:
    source_status = result.get("source_status") or {}
    errors = source_status.get("errors")
    if isinstance(errors, list):
        return [str(item) for item in errors if str(item)]
    if source_status.get("error"):
        return [str(source_status["error"])]
    if result.get("error"):
        return [str(result["error"])]
    return []


def _extract_record_count(result: Dict[str, Any]) -> int:
    for key in ("record_count", "holding_count", "total_holdings", "event_count", "raw_count", "total"):
        value = result.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class OptionalSourceHealthStore:
    """记录可选数据源连续成功次数，并判断是否达到稳定阈值。"""

    def __init__(self, path: Path = DEFAULT_HEALTH_FILE,
                 required_successes: Optional[int] = None,
                 max_age_hours: Optional[int] = None):
        self.path = Path(path)
        self.required_successes = int(required_successes or os.getenv("OPTIONAL_SOURCE_REQUIRED_SUCCESSES", "3"))
        self.max_age_hours = int(max_age_hours or os.getenv("OPTIONAL_SOURCE_MAX_AGE_HOURS", "36"))

    def _load(self) -> Dict[str, Any]:
        return _read_json(self.path, {"version": 1, "sources": {}})

    def _save(self, payload: Dict[str, Any]) -> None:
        payload["version"] = 1
        payload["updated_at"] = _iso_now()
        _write_json(self.path, payload)

    def record_result(self, source_key: str, result: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._load()
        sources = payload.setdefault("sources", {})
        previous = sources.get(source_key, {})

        source_status = result.get("source_status") or {}
        errors = _extract_errors(result)
        ok = bool(result.get("ok")) and not errors
        record_count = _extract_record_count(result)
        fetched_at = source_status.get("fetched_at") or result.get("fetched_at") or _iso_now()
        fetched_dt = _parse_datetime(fetched_at) or _now()
        consecutive = int(previous.get("consecutive_successes") or 0)
        consecutive = consecutive + 1 if ok else 0
        stable = self._is_fresh(fetched_dt) and ok and consecutive >= self.required_successes and record_count > 0

        entry = {
            "key": source_key,
            "ok": ok,
            "stable": stable,
            "consecutive_successes": consecutive,
            "required_successes": self.required_successes,
            "max_age_hours": self.max_age_hours,
            "updated_at": _iso_now(),
            "last_success_at": fetched_dt.isoformat() if ok else previous.get("last_success_at"),
            "last_failure_at": None if ok else _iso_now(),
            "source_status": {
                "source": source_status.get("source"),
                "source_url": source_status.get("source_url"),
                "fetched_at": fetched_dt.isoformat(),
                "errors": errors,
                "note": source_status.get("note"),
            },
            "data": {
                "record_count": record_count,
                "holding_count": result.get("holding_count"),
                "change_count": result.get("change_count"),
                "event_count": result.get("event_count"),
                "periods": result.get("periods") or [],
            },
            "error": errors[0] if errors else None,
        }
        sources[source_key] = entry
        self._save(payload)
        return entry

    def get(self, source_key: str) -> Dict[str, Any]:
        return self._load().get("sources", {}).get(source_key, {})

    def all(self) -> Dict[str, Any]:
        return self._load().get("sources", {})

    def is_promoted(self, source_key: str) -> bool:
        forced = _env_flag(_source_env_key(source_key, "PROMOTED"))
        if forced is not None:
            return forced
        entry = self.get(source_key)
        fetched_at = _parse_datetime((entry.get("source_status") or {}).get("fetched_at"))
        return bool(entry.get("stable") and fetched_at and self._is_fresh(fetched_at))

    def _is_fresh(self, fetched_at: datetime) -> bool:
        return _now() - fetched_at <= timedelta(hours=self.max_age_hours)


def _load_optional_source_config(path: Path = DEFAULT_CONFIG_FILE) -> Dict[str, Any]:
    return _read_json(path, {"version": 1, "sources": {}})


def record_optional_source_result(source_key: str, result: Dict[str, Any]) -> Dict[str, Any]:
    config = _load_optional_source_config()
    source_cfg = (config.get("sources") or {}).get(source_key, {})
    store = OptionalSourceHealthStore(
        DEFAULT_HEALTH_FILE,
        required_successes=source_cfg.get("required_successes"),
        max_age_hours=source_cfg.get("max_age_hours"),
    )
    return store.record_result(source_key, result)


def get_optional_source_statuses() -> Dict[str, Any]:
    config = _load_optional_source_config()
    sources = {}
    for key, cfg in (config.get("sources") or {}).items():
        store = OptionalSourceHealthStore(
            DEFAULT_HEALTH_FILE,
            required_successes=cfg.get("required_successes"),
            max_age_hours=cfg.get("max_age_hours"),
        )
        entry = store.get(key)
        surfaces = cfg.get("surfaces") or {}
        sources[key] = {
            **entry,
            "label": cfg.get("label") or key,
            "surfaces": {
                "email_enabled": is_optional_source_enabled(key, "email"),
                "dashboard_enabled": is_optional_source_enabled(key, "dashboard"),
                **surfaces,
            },
            "ready_for_promotion": store.is_promoted(key),
        }
    return {
        "sources": sources,
        "updated_at": _iso_now(),
    }


def is_optional_source_enabled(source_key: str, surface: str) -> bool:
    forced = _env_flag(_source_env_key(source_key, surface.upper()))
    if forced is not None:
        return forced
    config = _load_optional_source_config()
    source_cfg = (config.get("sources") or {}).get(source_key, {})
    surfaces = source_cfg.get("surfaces") or {}
    return bool(surfaces.get(surface, False))
