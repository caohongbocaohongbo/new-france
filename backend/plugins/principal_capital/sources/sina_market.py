"""新浪全主板主力资金流：先取主板代码清单，再并发单股查询资金流。

用途：东方财富全市场接口失效时的「真兜底」源。东财与 akshare 同源，一旦
同时不可用，本源作为独立数据源接管。清单和资金流全部走新浪：
  - 清单：Market_Center.getHQNodeData（隔夜套利插件已在 GitHub Actions 用它，
    证明美国 IP 可访问）
  - 资金流：MoneyFlow 单股接口并发查询（单股返回可用 code 直接对应，天然不错位；
    逗号批量接口会乱序且不带 code，故不用批量）
"""
import logging
from typing import List

import pandas as pd
import requests

from .eastmoney import FundFlowFetchError
from .sina import fetch_codes_fund_flow_sina

logger = logging.getLogger(__name__)

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

# 与东财 fetch_market_fund_flow 一致的列结构，供上层 _base_filter 复用。
_COLUMNS = [
    "code", "name", "price", "change_pct", "total_amount",
    "main_net_inflow", "main_inflow_ratio", "super_net", "big_net",
    "mid_net", "small_net", "source",
]


def _is_main_board_code(code: str) -> bool:
    return str(code or "").zfill(6).startswith(MAIN_BOARD_PREFIXES)


def fetch_main_board_codes(max_pages: int = 80, timeout: int = 12) -> List[str]:
    """从新浪全A榜单分页取沪深主板代码清单。

    榜单按涨跌幅排序，主板股散落各页，必须翻完所有页才能取全，
    因此 max_pages 需覆盖全A股数量（约 5400 只 / 80 每页 ≈ 68 页）。
    """
    codes: List[str] = []
    seen = set()
    session = requests.Session()
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
        resp = session.get(
            SINA_NODE_URL, params=params, headers=SINA_NODE_HEADERS, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        for item in data:
            code = str(item.get("code") or "").zfill(6)
            if not code or code in seen or not _is_main_board_code(code):
                continue
            codes.append(code)
            seen.add(code)
        if len(data) < 80:
            break
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
        codes = fetch_main_board_codes(max_pages=list_pages)
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


def fetch_market_fund_flow_via_sina(
    max_workers: int = 20,
    batch_timeout: float = 90.0,
    codes: List[str] = None,
) -> pd.DataFrame:
    """新浪全主板主力资金流，返回与东财一致的 DataFrame 列结构。

    codes 显式传入时跳过榜单拉取（便于测试/复用现成清单）。
    """
    if codes is None:
        codes = fetch_main_board_codes()
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
    logger.info("新浪全主板主力资金流：请求 %d 只，成功 %d 只", len(codes), len(df))
    return df
