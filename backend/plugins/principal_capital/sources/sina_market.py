"""新浪全主板主力资金流：先取主板代码清单，再并发单股查询资金流。

用途：东方财富全市场接口失效时的「真兜底」源。东财与 akshare 同源，一旦
同时不可用，本源作为独立数据源接管。清单和资金流全部走新浪：
  - 清单：Market_Center.getHQNodeData（隔夜套利插件已在 GitHub Actions 用它，
    证明美国 IP 可访问）
  - 资金流：MoneyFlow 单股接口并发查询（单股返回可用 code 直接对应，天然不错位；
    逗号批量接口会乱序且不带 code，故不用批量）
"""
import collections
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import pandas as pd
import requests

from .eastmoney import FundFlowFetchError
from .sina import fetch_codes_fund_flow_sina
from ..config import CONFIG, DATA_DIR, SINA_CODES_CACHE_FILE

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

SINA_NODE_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
SINA_NODE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}

# 沪深主板：沪市 60；深市 000/001/002/003。
# 排除创业板(300/301)、科创板(688)、北交所(8x/920/430)。
MAIN_BOARD_PREFIXES = ("60", "000", "001", "002", "003")

# 最近一次取清单若走了过期缓存降级，记录其日期(MM-DD)；否则 None。
# 由 fetch_main_board_codes 写、get_last_codes_stale_date 读，供上层标注滞后。
_last_codes_stale_date: Optional[str] = None

# 与东财 fetch_market_fund_flow 一致的列结构，供上层 _base_filter 复用。
_COLUMNS = [
    "code", "name", "price", "change_pct", "total_amount",
    "main_net_inflow", "main_inflow_ratio", "super_net", "big_net",
    "mid_net", "small_net", "source",
    # 23 v2 数据契约：单股结果补齐元数据，供质量门控与同源差分复用
    "endpoint", "fetched_at", "source_time", "freshness_basis", "quality_status",
]


def _is_main_board_code(code: str) -> bool:
    return str(code or "").zfill(6).startswith(MAIN_BOARD_PREFIXES)


def _fetch_main_board_codes_remote(max_pages: int = 80, timeout: int = 18):
    """从新浪全A榜单分页翻取沪深主板代码清单（纯网络，无缓存）。

    返回 (codes, meta)。meta 记录 failed_pages / pages_fetched / terminal_page_seen /
    duplicate_count / verified。终止页之前出现任何缺页 -> verified=false。
    """
    codes: List[str] = []
    seen = set()
    duplicates = 0
    failed_pages: List[int] = []
    pages_fetched = 0
    terminal_page_seen = False
    session = requests.Session()
    empty_streak = 0
    for page in range(1, max_pages + 1):
        params = {
            "page": page,
            "num": 80,
            "sort": "changepercent",
            "asc": 0,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page",
        }
        data = None
        for attempt in range(2):  # 首次 + 重试 1 次
            try:
                resp = session.get(
                    SINA_NODE_URL, params=params, headers=SINA_NODE_HEADERS, timeout=timeout
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:  # noqa: BLE001 单页失败不应中断整份清单
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                logger.warning("主板清单第 %d 页拉取失败（已重试）：%s", page, exc)
                data = None
        if data is None:
            failed_pages.append(page)
            continue
        pages_fetched += 1
        if not data:
            empty_streak += 1
            if empty_streak >= 2:  # 连续空页视为翻到末尾
                terminal_page_seen = True
                break
            continue
        empty_streak = 0
        for item in data:
            code = str(item.get("code") or "").zfill(6)
            if not code:
                continue
            if code in seen:
                duplicates += 1
                continue
            if not _is_main_board_code(code):
                continue
            seen.add(code)
            codes.append(code)
        if len(data) < 80:
            terminal_page_seen = True
            break
    verified = bool(codes and not failed_pages and terminal_page_seen)
    meta = {
        "failed_pages": failed_pages,
        "pages_fetched": pages_fetched,
        "terminal_page_seen": terminal_page_seen,
        "duplicate_count": duplicates,
        "verified": verified,
    }
    return codes, meta


def _read_codes_cache(
    ttl_seconds: int, allow_stale: bool = False
) -> Tuple[Optional[List[str]], Optional[datetime]]:
    """读主板代码清单缓存，返回 (codes, cached_at)。

    未命中/损坏返回 (None, None)。allow_stale=False 时过期同样返回 (None, None)；
    allow_stale=True 时忽略 TTL 返回缓存及其写入时间，供网络失败后的降级路径
    据 cached_at 计算滞后天数。
    """
    if not SINA_CODES_CACHE_FILE.exists():
        return None, None
    try:
        payload = json.loads(SINA_CODES_CACHE_FILE.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(payload["cached_at"])
        codes = payload.get("codes") or []
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None, None
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=BEIJING_TZ)
    if not codes:
        return None, None
    age = (datetime.now(BEIJING_TZ) - cached_at).total_seconds()
    if age > ttl_seconds and not allow_stale:
        return None, None
    return codes, cached_at


def _write_codes_cache(codes: List[str], verified: bool = True, cache_version: Optional[str] = None) -> None:
    from ..config import atomic_write_json

    payload = {
        "cached_at": datetime.now(BEIJING_TZ).isoformat(),
        "codes": codes,
        "verified": bool(verified),
        "cache_version": cache_version or datetime.now(BEIJING_TZ).strftime("%Y%m%dT%H%M%S"),
    }
    atomic_write_json(SINA_CODES_CACHE_FILE, payload)


def _read_codes_cache_payload() -> Optional[dict]:
    if not SINA_CODES_CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(SINA_CODES_CACHE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def fetch_main_board_universe(max_pages: int = 80, timeout: int = 18, use_cache: bool = True) -> dict:
    """P0-R5：取主板 universe，返回 codes + 分页完整性元数据。

    只有 verified=true 才能写为新鲜权威缓存；中间页失败/未确认终止页必须 verified=false，
    只能回退上一次经过验证的缓存并记录 cache_version/cached_at。
    """
    global _last_codes_stale_date
    _last_codes_stale_date = None
    ttl = int(CONFIG.get("sina_codes_cache_ttl_seconds", 259200))
    now = datetime.now(BEIJING_TZ)

    if use_cache:
        cached, cached_at = _read_codes_cache(ttl)
        if cached:
            payload = _read_codes_cache_payload() or {}
            logger.info("主板代码清单命中缓存：%d 只", len(cached))
            return {
                "codes": cached,
                "failed_pages": [],
                "pages_fetched": None,
                "terminal_page_seen": True,
                "duplicate_count": 0,
                "verified": bool(payload.get("verified", False)),
                "cache_version": payload.get("cache_version"),
                "cached_at": cached_at.isoformat() if cached_at else None,
                "from_cache": True,
            }

    codes, meta = _fetch_main_board_codes_remote(max_pages=max_pages, timeout=timeout)
    if codes and meta["verified"]:
        if use_cache:
            _write_codes_cache(codes, verified=True)
        return {**meta, "codes": codes, "cache_version": None, "cached_at": now.isoformat()}

    # 实时拉取未验证：回退上一次经过验证的缓存（stale better than none）
    if use_cache:
        stale_codes, cached_at = _read_codes_cache(ttl, allow_stale=True)
        if stale_codes:
            payload = _read_codes_cache_payload() or {}
            _last_codes_stale_date = cached_at.strftime("%m-%d") if cached_at else None
            logger.warning("主板清单实时拉取未验证，回退旧缓存：%d 只", len(stale_codes))
            return {
                **meta,
                "codes": stale_codes,
                "verified": False,
                "cache_version": payload.get("cache_version"),
                "cached_at": cached_at.isoformat() if cached_at else None,
                "fallback_to_cache": True,
            }
    return {**meta, "codes": codes, "cache_version": None, "cached_at": None}


def get_last_codes_stale_date() -> Optional[str]:
    """返回最近一次 fetch_main_board_codes 若走了过期缓存降级时的清单日期。

    命中新鲜缓存或实时拉取成功时为 None。格式 MM-DD，供上层标注清单滞后。
    """
    return _last_codes_stale_date


def fetch_main_board_codes(
    max_pages: int = 80, timeout: int = 18, use_cache: bool = True
) -> List[str]:
    """取沪深主板代码清单（向后兼容：返回 codes 列表）。"""
    universe = fetch_main_board_universe(max_pages=max_pages, timeout=timeout, use_cache=use_cache)
    return universe["codes"]


def verify_sina_connectivity(list_pages: int = 2, sample_size: int = 30) -> dict:
    """轻量连通性验证：只拉清单前几页 + 抽查少量资金流，几秒内完成。

    用于在 GitHub Actions（美国 IP）上快速回答「新浪两个接口能否访问」，
    不受扫描 job 120s 墙钟限制。返回结构化结果，供 CLI 打印与判定。
    """
    from time import perf_counter

    result = {
        "node_ok": False,       # 榜单接口(getHQNodeData)是否可用
        "moneyflow_ok": False,  # 资金流接口(MoneyFlow)是否可用
        "list_count": 0,
        "sample_requested": 0,
        "sample_success": 0,
        "list_ms": 0,
        "flow_ms": 0,
        "error": None,
        "samples": [],
    }
    try:
        t0 = perf_counter()
        # 连通性验证测真实网络，绕过缓存
        codes = fetch_main_board_codes(max_pages=list_pages, use_cache=False)
        result["list_ms"] = int((perf_counter() - t0) * 1000)
        result["list_count"] = len(codes)
        result["node_ok"] = len(codes) > 0
        if not codes:
            result["error"] = "榜单接口返回空，无法取到主板代码"
            return result

        sample_codes = codes[:sample_size]
        result["sample_requested"] = len(sample_codes)
        t1 = perf_counter()
        rows = fetch_codes_fund_flow_sina(sample_codes, max_workers=15, batch_timeout=45.0)
        result["flow_ms"] = int((perf_counter() - t1) * 1000)
        result["sample_success"] = len(rows)
        result["moneyflow_ok"] = len(rows) > 0
        result["samples"] = [
            {"code": r["code"], "name": r["name"], "ratio": r.get("main_inflow_ratio")}
            for r in rows[:3]
        ]
    except Exception as exc:  # noqa: BLE001 验证入口需捕获全部异常并如实上报
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def verify_sina_full_timing(max_workers: int = 20) -> dict:
    """全量计时验证：拉全主板清单 + 全量查资金流，测真实耗时/成功率/并发表现。

    仅用于评估当前网络环境（如 GitHub 美国 IP）能否把全主板扫描塞进墙钟预算。
    不落库、不截断（batch_timeout 放宽），结果供 CLI 打印与方案决策。
    """
    from time import perf_counter

    result = {
        "max_workers": max_workers,
        "list_count": 0,
        "flow_success": 0,
        "success_rate": 0.0,
        "list_ms": 0,
        "flow_ms": 0,
        "total_ms": 0,
        "throughput_per_sec": 0.0,
        "error": None,
    }
    try:
        t0 = perf_counter()
        # 计时验证测真实翻页耗时，绕过缓存
        codes = fetch_main_board_codes(use_cache=False)
        result["list_ms"] = int((perf_counter() - t0) * 1000)
        result["list_count"] = len(codes)
        if not codes:
            result["error"] = "榜单接口返回空"
            return result

        t1 = perf_counter()
        # batch_timeout 放宽到 10 分钟，确保测出真实全量耗时而非被截断
        rows = fetch_codes_fund_flow_sina(
            codes, max_workers=max_workers, batch_timeout=600.0
        )
        flow_s = perf_counter() - t1
        result["flow_ms"] = int(flow_s * 1000)
        result["flow_success"] = len(rows)
        result["success_rate"] = round(len(rows) / len(codes) * 100, 1) if codes else 0.0
        result["throughput_per_sec"] = round(len(rows) / flow_s, 1) if flow_s > 0 else 0.0
        result["total_ms"] = result["list_ms"] + result["flow_ms"]
    except Exception as exc:  # noqa: BLE001 计时入口需捕获全部异常并如实上报
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def fetch_market_fund_flow_via_sina(
    max_workers: Optional[int] = None,
    batch_timeout: float = 110.0,
    codes: List[str] = None,
) -> pd.DataFrame:
    """新浪全主板主力资金流，返回与东财一致的 DataFrame 列结构。

    max_workers 默认取 CONFIG（美国 IP 实测 40 并发全主板 ~54s，100% 成功）。
    codes 显式传入时跳过榜单拉取（便于测试/复用现成清单）。
    """
    if max_workers is None:
        max_workers = int(CONFIG.get("sina_max_workers", 40))
    codes_stale_date = None
    if codes is None:
        codes = fetch_main_board_codes()
        codes_stale_date = get_last_codes_stale_date()
    if not codes:
        raise FundFlowFetchError("新浪主板代码清单为空")
    rows = fetch_codes_fund_flow_sina(
        codes, max_workers=max_workers, batch_timeout=batch_timeout
    )
    if not rows:
        raise FundFlowFetchError("新浪主力资金流全部查询失败")
    df = pd.DataFrame(rows)
    for col in _COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[_COLUMNS].drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    # 清单若走了过期缓存降级，用 df.attrs 回传日期供上层标注滞后（pandas 原生元数据通道）
    df.attrs["codes_stale_date"] = codes_stale_date
    logger.info("新浪全主板主力资金流：请求 %d 只，成功 %d 只", len(codes), len(df))
    return df

# --------------------------------------------------------------------------- #
# 23 v2 bulk 适配器：MoneyFlow.ssl_bkzj_ssggzj 单请求全市场粗筛
# --------------------------------------------------------------------------- #

SINA_BULK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_bkzj_ssggzj"
)
SINA_BULK_ENDPOINT = "MoneyFlow.ssl_bkzj_ssggzj"

# bulk 原始字段 -> 标准化后字段。ratioamount / r0_ratio 是「总净占比分数」，
# 必须在适配器内一次性 *100 标准化成百分数，任何下游不得再二次 *100。
_BULK_KEY_FIELDS = ("total_amount", "super_net", "super_ratio", "netamount", "ratioamount")


def _bulk_float(value):
    """bulk 数值解析：None/空串/-/NaN/Inf 一律 None，不得变成合法零。"""
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_bulk_rows(payload, fetched_at) -> List[dict]:
    """把新浪 bulk 响应解析为 BulkRow 列表（无网络）。

    - 只接受 sh/sz 前缀的 symbol，code 唯一标准化。
    - ratioamount/r0_ratio 标准化为百分数；缺失/非法数值标 degraded，不进粗筛候选。
    """
    if not isinstance(payload, list):
        raise ValueError("bulk 响应必须是列表")
    rows: List[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        if symbol[:2] not in ("sh", "sz"):
            continue
        code = symbol[2:].zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        changeratio = _bulk_float(item.get("changeratio"))
        change_pct = round(changeratio * 100, 4) if changeratio is not None else None
        super_ratio_raw = _bulk_float(item.get("r0_ratio"))
        ratioamount_raw = _bulk_float(item.get("ratioamount"))
        row = {
            "code": code,
            "name": str(item.get("name") or "").strip(),
            "price": _bulk_float(item.get("trade")),
            "change_pct": change_pct,
            "total_amount": _bulk_float(item.get("amount")),
            "super_net": _bulk_float(item.get("r0_net")),
            "super_ratio": round(super_ratio_raw * 100, 4) if super_ratio_raw is not None else None,
            "netamount": _bulk_float(item.get("netamount")),
            "ratioamount": round(ratioamount_raw * 100, 4) if ratioamount_raw is not None else None,
            "source": "sina_bulk",
            "endpoint": SINA_BULK_ENDPOINT,
            "fetched_at": fetched_at.isoformat() if isinstance(fetched_at, datetime) else str(fetched_at),
            "source_time": None,
            "eligible_for": [],
            "degraded_reasons": ["source_time_unavailable"],
        }
        non_finite = [field for field in _BULK_KEY_FIELDS if row[field] is None]
        if non_finite:
            row["degraded_reasons"].append(f"non_finite:{','.join(sorted(non_finite))}")
        else:
            row["eligible_for"].append("coarse_candidate")
        rows.append(row)
    return rows


def validate_bulk_rows(rows, universe, known_non_trading=None) -> dict:
    """整表确定性校验（无网络）。universe 为当日有效主板代码集合。"""
    known = {str(code).zfill(6): reason for code, reason in (known_non_trading or {}).items()}
    universe_set = {str(code).zfill(6) for code in (universe or [])}
    row_codes = [str(row.get("code") or "").zfill(6) for row in rows]
    unique_codes = set(row_codes)
    counts = collections.Counter(row_codes)
    duplicates = sorted(code for code, count in counts.items() if count > 1)
    missing = sorted(universe_set - unique_codes)
    unexplained_missing = [code for code in missing if code not in known]
    extra = sorted(unique_codes - universe_set)
    non_finite = sorted({
        row.get("code") for row in rows
        if any(row.get(field) is None for field in _BULK_KEY_FIELDS)
    })
    # 只有「当日活跃主板 universe 内」的代码出现 non_finite 才算数据故障。
    # universe 之外的 non_finite（停牌/退市/非主板如创业板·科创板·B股·ETF）
    # 属预期现象：这些代码本就无成交，bulk 返回 "-" 是合法缺失而非损坏。
    non_finite_unexplained = [code for code in non_finite if code in universe_set and code not in known]
    received_in_universe = sorted(unique_codes & universe_set)
    reasons = []
    if duplicates:
        reasons.append(f"duplicate_code:{len(duplicates)}")
    if unexplained_missing:
        reasons.append(f"missing_codes:{len(unexplained_missing)}")
    if non_finite_unexplained:
        reasons.append(f"non_finite:{len(non_finite_unexplained)}")
    valid = not duplicates and not unexplained_missing and not non_finite_unexplained
    coverage_ratio = round(len(received_in_universe) / len(universe_set), 6) if universe_set else 0.0
    return {
        "valid": valid,
        "universe_count": len(universe_set),
        "received_count": len(received_in_universe),
        "coverage_ratio": coverage_ratio,
        "missing_codes": missing,
        "missing_reasons": {code: known.get(code, "unexplained") for code in missing},
        "extra_codes": extra,
        "duplicate_codes": duplicates,
        "non_finite_codes": non_finite,
        "non_finite_unexplained_codes": non_finite_unexplained,
        "reasons": reasons,
    }


def fetch_bulk_fund_flow(
    num: int = 8000,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> Tuple[List[dict], int]:
    """单请求拉取新浪全市场资金流榜单。返回 (原始列表, 延迟毫秒)。"""
    http = session or requests.Session()
    start = time.perf_counter()
    resp = http.get(
        SINA_BULK_URL,
        params={"num": str(num)},
        headers=SINA_NODE_HEADERS,
        timeout=timeout,
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise FundFlowFetchError(f"bulk 非列表返回: {str(data)[:120]}")
    return data, latency_ms
