"""akshare 主力资金流兜底。"""
import logging

import pandas as pd

from .eastmoney import FundFlowFetchError

logger = logging.getLogger(__name__)


def _to_float(value):
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_market_fund_flow_via_akshare(timeout: int = 30) -> pd.DataFrame:
    """通过 akshare 获取全市场主力资金流。"""
    del timeout
    try:
        import akshare as ak

        df = ak.stock_individual_fund_flow_rank(indicator="今日")
    except Exception as exc:
        logger.warning("akshare 主力资金抓取失败: %s", exc)
        raise FundFlowFetchError(str(exc)) from exc

    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "code", "name", "price", "change_pct", "total_amount",
            "main_net_inflow", "main_inflow_ratio", "super_net", "big_net",
            "mid_net", "small_net", "source",
        ])

    rows = []
    for _, row in df.iterrows():
        rows.append({
            "code": str(row.get("代码", "")).zfill(6),
            "name": str(row.get("名称", "")).strip(),
            "price": _to_float(row.get("最新价")),
            "change_pct": _to_float(row.get("今日涨跌幅")),
            "total_amount": None,
            "main_net_inflow": _to_float(row.get("今日主力净流入-净额")),
            "main_inflow_ratio": _to_float(row.get("今日主力净流入-净占比")),
            "super_net": _to_float(row.get("今日超大单净流入-净额")),
            "big_net": _to_float(row.get("今日大单净流入-净额")),
            "mid_net": _to_float(row.get("今日中单净流入-净额")),
            "small_net": _to_float(row.get("今日小单净流入-净额")),
            "source": "akshare",
        })
    return pd.DataFrame(rows)
