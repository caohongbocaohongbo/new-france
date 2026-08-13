"""主力资金流多源协调器（plugin 独立版本）。

策略: eastmoney(多域名轮询) → akshare(东财同源兜底) → 本地缓存降级
熔断: 连续失败达到阈值后短时退避
持久化: 状态文件 / 缓存文件路径都在 plugin 独立命名空间。
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Optional, Tuple

import pandas as pd
import requests

from .akshare_source import fetch_market_fund_flow_via_akshare
from .eastmoney import EASTMONEY_HOSTS, FundFlowFetchError, fetch_market_fund_flow
from .sina import fetch_codes_fund_flow_sina
from .sina_market import fetch_market_fund_flow_via_sina
from .tencent import fetch_codes_fund_flow_tencent
from ..config import CACHE_FILE, CONFIG, DATA_DIR, SOURCE_HEALTH_FILE

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
FAILURE_THRESHOLD = int(CONFIG.get("data_source_failure_threshold", 5))
BLOCK_MINUTES = int(CONFIG.get("data_source_circuit_break_minutes", 8))

_CACHE_JSON_FILE = DATA_DIR / "principal_capital_cache.json"

_HEALTH: Optional[dict] = None


def _now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _default_health() -> dict:
    return {
        "eastmoney": {"failure_streak": 0, "blocked_until": None},
        "akshare": {"failure_streak": 0, "blocked_until": None},
        "sina": {"failure_streak": 0, "blocked_until": None},
        "updated_at": None,
    }


def _load_health() -> dict:
    global _HEALTH
    if _HEALTH is not None:
        return _HEALTH
    if SOURCE_HEALTH_FILE.exists():
        try:
            _HEALTH = json.loads(SOURCE_HEALTH_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _HEALTH = _default_health()
    else:
        _HEALTH = _default_health()
    if "eastmoney" not in _HEALTH:
        eastmoney_entry = (
            _HEALTH.get("eastmoney")
            or _HEALTH.get("eastmoney_push2")
            or _HEALTH.get("eastmoney_push2his")
            or {"failure_streak": 0, "blocked_until": None}
        )
        _HEALTH["eastmoney"] = eastmoney_entry
        _HEALTH.pop("eastmoney_push2", None)
        _HEALTH.pop("eastmoney_push2his", None)
    _HEALTH.setdefault("akshare", {"failure_streak": 0, "blocked_until": None})
    _HEALTH.setdefault("sina", {"failure_streak": 0, "blocked_until": None})
    _HEALTH.setdefault("updated_at", None)
    return _HEALTH


def _save_health() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    health = _load_health()
    health["updated_at"] = _now().isoformat()
    SOURCE_HEALTH_FILE.write_text(
        json.dumps(health, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_blocked(source: str, now: datetime) -> bool:
    blocked_until = (_load_health().get(source) or {}).get("blocked_until")
    if not blocked_until:
        return False
    try:
        ts = datetime.fromisoformat(blocked_until)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=BEIJING_TZ)
    return ts > now


def _mark_success(source: str) -> None:
    health = _load_health()
    health.setdefault(source, {})
    health[source]["failure_streak"] = 0
    health[source]["blocked_until"] = None
    _save_health()


def _mark_failure(source: str, transient: bool = False) -> None:
    health = _load_health()
    item = health.setdefault(source, {"failure_streak": 0, "blocked_until": None})
    item["failure_streak"] = int(item.get("failure_streak", 0)) + 1
    if item["failure_streak"] >= FAILURE_THRESHOLD:
        block_minutes = min(BLOCK_MINUTES, 3) if transient else BLOCK_MINUTES
        item["blocked_until"] = (_now() + timedelta(minutes=block_minutes)).isoformat()
    _save_health()


def _write_cache(df: pd.DataFrame, fetched_at: datetime) -> None:
    if df is None or df.empty:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": fetched_at.isoformat(),
        "records": df.to_dict("records"),
    }
    _CACHE_JSON_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    try:
        df.assign(_cached_at=fetched_at.isoformat()).to_parquet(CACHE_FILE, index=False)
    except Exception:
        logger.debug("主力资金 parquet 缓存写入失败，已回退 JSON 缓存")


def _read_cache(cache_ttl_seconds: int) -> Tuple[pd.DataFrame, Optional[int]]:
    now = _now()
    payload = None
    if _CACHE_JSON_FILE.exists():
        try:
            payload = json.loads(_CACHE_JSON_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = None
    elif CACHE_FILE.exists():
        try:
            df = pd.read_parquet(CACHE_FILE)
            cached_at = (
                str(df["_cached_at"].iloc[0])
                if "_cached_at" in df.columns and not df.empty
                else None
            )
            payload = {
                "fetched_at": cached_at,
                "records": df.drop(columns=["_cached_at"], errors="ignore").to_dict("records"),
            }
        except Exception:
            payload = None
    if not payload or not payload.get("fetched_at"):
        return pd.DataFrame(), None
    try:
        cached_at = datetime.fromisoformat(payload["fetched_at"])
    except ValueError:
        return pd.DataFrame(), None
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=BEIJING_TZ)
    age = int((now - cached_at).total_seconds())
    if age > cache_ttl_seconds:
        return pd.DataFrame(), age
    return pd.DataFrame(payload.get("records") or []), age


def _verify_sample(df: pd.DataFrame) -> dict:
    sample_codes = [str(code).zfill(6) for code in df.head(5).get("code", []).tolist()]
    sina_rows = fetch_codes_fund_flow_sina(sample_codes, max_workers=5)
    tencent_rows = fetch_codes_fund_flow_tencent(sample_codes, max_workers=5)
    sina_map = {item["code"]: item for item in sina_rows}
    tencent_map = {item["code"]: item for item in tencent_rows}
    comparisons = []
    for _, row in df.head(5).iterrows():
        code = str(row.get("code", "")).zfill(6)
        base = float(row.get("main_net_inflow") or 0.0)
        sina_value = float((sina_map.get(code) or {}).get("main_net_inflow") or 0.0)
        tencent_value = float((tencent_map.get(code) or {}).get("main_net_inflow") or 0.0)
        item = {
            "code": code,
            "eastmoney": base,
            "sina": sina_value if code in sina_map else None,
            "tencent": tencent_value if code in tencent_map else None,
            "sina_error_pct": None,
            "tencent_error_pct": None,
        }
        if code in sina_map:
            sina_denominator = abs(base) if abs(base) > 1 else max(abs(sina_value), 1.0)
            sina_error_pct = abs(base - sina_value) / sina_denominator * 100
            item["sina_error_pct"] = round(sina_error_pct, 2)
            if sina_error_pct > 10:
                logger.warning("主力资金新浪抽样核验偏差较大 %s: %.2f%%", code, sina_error_pct)
        if code in tencent_map:
            tencent_denominator = abs(base) if abs(base) > 1 else max(abs(tencent_value), 1.0)
            tencent_error_pct = abs(base - tencent_value) / tencent_denominator * 100
            item["tencent_error_pct"] = round(tencent_error_pct, 2)
            if tencent_error_pct > 10:
                logger.warning("主力资金腾讯抽样核验偏差较大 %s: %.2f%%", code, tencent_error_pct)
        if code not in sina_map and code not in tencent_map:
            item["status"] = "missing"
        comparisons.append(item)
    return {"sample_size": len(sample_codes), "comparisons": comparisons}


def _fetch_eastmoney_any() -> pd.DataFrame:
    errors = []
    for base_url in EASTMONEY_HOSTS:
        try:
            df = fetch_market_fund_flow(base_url=base_url)
            if df is not None and not df.empty:
                return df
            errors.append(f"{base_url}: empty dataframe")
        except Exception as exc:
            errors.append(f"{base_url}: {exc}")
    raise FundFlowFetchError("; ".join(errors) or "eastmoney all hosts failed")


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return bool(status_code and int(status_code) >= 500)


def fetch_market_fund_flow_resilient(
    enable_verify: bool = False,
    cache_ttl_seconds: int = 1800,
) -> Tuple[pd.DataFrame, dict]:
    """带熔断与缓存降级的全市场主力资金流查询。"""
    now = _now()
    attempts = []
    sources = [
        ("eastmoney", _fetch_eastmoney_any),
        # akshare 底层同为东财数据，非独立源，仅作东财主接口异常时的同源兜底
        ("akshare", lambda: fetch_market_fund_flow_via_akshare()),
        # sina 为独立数据源：东财/akshare 同源，一旦同时失效由新浪全主板接管。
        # 走单股并发（非批量），code 直接对应不错位；线程池并发已在 fetch 内限流。
        ("sina", lambda: fetch_market_fund_flow_via_sina()),
    ]
    status_report = {
        "active_source": "none",
        "attempts": attempts,
        "is_stale": False,
        "cache_age_seconds": None,
        # 新浪清单若走过期缓存降级，记录其日期(MM-DD)供邮件/页面标注；否则 None。
        "codes_stale_date": None,
        "verify_result": None,
    }

    for source_name, loader in sources:
        if _is_blocked(source_name, now):
            attempts.append({"source": source_name, "status": "skipped_blocked", "latency_ms": 0})
            continue
        start = perf_counter()
        try:
            df = loader()
            latency_ms = int((perf_counter() - start) * 1000)
            if df is None or df.empty:
                raise FundFlowFetchError("empty dataframe")
            _mark_success(source_name)
            attempts.append({"source": source_name, "status": "ok", "latency_ms": latency_ms})
            _write_cache(df, now)
            status_report["active_source"] = source_name
            status_report["codes_stale_date"] = df.attrs.get("codes_stale_date")
            if enable_verify and source_name == "eastmoney":
                status_report["verify_result"] = _verify_sample(df)
            return df, status_report
        except Exception as exc:
            latency_ms = int((perf_counter() - start) * 1000)
            _mark_failure(source_name, transient=_is_transient_error(exc))
            attempts.append({
                "source": source_name, "status": "error",
                "error": str(exc), "latency_ms": latency_ms,
            })

    try:
        cache_df, cache_age_seconds = _read_cache(cache_ttl_seconds)
        status_report["cache_age_seconds"] = cache_age_seconds
        if not cache_df.empty:
            status_report["active_source"] = "cache"
            status_report["is_stale"] = True
            return cache_df, status_report
    except Exception as exc:
        attempts.append({"source": "cache", "status": "error", "error": str(exc), "latency_ms": 0})
    return pd.DataFrame(), status_report


def get_cached_fund_flow(max_age_seconds: int = 600) -> Tuple[pd.DataFrame, Optional[int]]:
    """对外暴露的只读缓存接口（不发 HTTP）。"""
    return _read_cache(cache_ttl_seconds=max_age_seconds)
