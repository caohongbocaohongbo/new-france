"""新浪全主板主力资金流：先取主板代码清单，再并发单股查询资金流。

用途：东方财富全市场接口失效时的「真兜底」源。东财与 akshare 同源，一旦
同时不可用，本源作为独立数据源接管。清单和资金流全部走新浪：
  - 清单：Market_Center.getHQNodeData（隔夜套利插件已在 GitHub Actions 用它，
    证明美国 IP 可访问）
  - 资金流：MoneyFlow 单股接口并发查询（单股返回可用 code 直接对应，天然不错位；
    逗号批量接口会乱序且不带 code，故不用批量）
"""
import json
import logging
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
]


def _is_main_board_code(code: str) -> bool:
    return str(code or "").zfill(6).startswith(MAIN_BOARD_PREFIXES)


def _fetch_main_board_codes_remote(max_pages: int = 80, timeout: int = 18) -> List[str]:
    """从新浪全A榜单分页翻取沪深主板代码清单（纯网络，无缓存）。

    榜单按涨跌幅排序，主板股散落各页，必须翻完所有页才能取全，
    因此 max_pages 需覆盖全A股数量（约 5400 只 / 80 每页 ≈ 68 页）。

    跨太平洋抖动下单页偶发超时属常态，因此单页失败重试 1 次；仍失败则
    跳过该页继续翻（已翻到的页照常累积），避免一页拖垮整份清单。只有全部
    页都失败（codes 为空）才视为拉取失败，交由上层降级到旧缓存。
    """
    codes: List[str] = []
    seen = set()
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
            continue  # 该页放弃，继续下一页
        if not data:
            empty_streak += 1
            if empty_streak >= 2:  # 连续空页视为翻到末尾
                break
            continue
        empty_streak = 0
        for item in data:
            code = str(item.get("code") or "").zfill(6)
            if not code or code in seen or not _is_main_board_code(code):
                continue
            codes.append(code)
            seen.add(code)
        if len(data) < 80:
            break
    return codes


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


def _write_codes_cache(codes: List[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"cached_at": datetime.now(BEIJING_TZ).isoformat(), "codes": codes}
    SINA_CODES_CACHE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_last_codes_stale_date() -> Optional[str]:
    """返回最近一次 fetch_main_board_codes 若走了过期缓存降级时的清单日期。

    命中新鲜缓存或实时拉取成功时为 None。格式 MM-DD，供上层标注清单滞后。
    """
    return _last_codes_stale_date


def fetch_main_board_codes(
    max_pages: int = 80, timeout: int = 18, use_cache: bool = True
) -> List[str]:
    """取沪深主板代码清单，默认带缓存。

    主板成分变动极慢（仅新股上市增量），缓存 TTL 内直接复用，省去每轮翻页
    约 25s（美国 IP 实测）。缓存未命中时翻页拉取并落盘。
    use_cache=False 时强制走网络（供连通性验证等场景）。

    网络拉取返回空时降级读过期缓存（allow_stale）——只要曾成功过一次，就不会
    因榜单单次抖动而让整条新浪源判空；此时记录缓存日期供上层标注滞后。
    """
    global _last_codes_stale_date
    _last_codes_stale_date = None
    ttl = int(CONFIG.get("sina_codes_cache_ttl_seconds", 259200))
    if use_cache:
        cached, _ = _read_codes_cache(ttl)
        if cached:
            logger.info("主板代码清单命中缓存：%d 只", len(cached))
            return cached
    codes = _fetch_main_board_codes_remote(max_pages=max_pages, timeout=timeout)
    if codes:
        if use_cache:
            _write_codes_cache(codes)
        return codes
    # 实时拉取失败：降级到过期缓存（stale better than none）
    if use_cache:
        stale_codes, cached_at = _read_codes_cache(ttl, allow_stale=True)
        if stale_codes:
            _last_codes_stale_date = cached_at.strftime("%m-%d") if cached_at else None
            logger.warning(
                "主板清单实时拉取失败，降级使用 %s 的旧缓存：%d 只",
                _last_codes_stale_date, len(stale_codes),
            )
            return stale_codes
    return codes


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
