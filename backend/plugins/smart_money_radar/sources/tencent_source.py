"""
腾讯/新浪 HTTP 行情源 — 替代已失效的 TDX 免费行情服务器。

- 五档盘口/主动买卖：腾讯 qt.gtimg.cn（88 字段，外盘/内盘近似 b_vol/s_vol）
- 分钟K线：新浪 getKLineData（5/15 分钟；免费源无 1 分钟，category 8 退化为 5 分钟）
- 流通股本：腾讯流通市值(亿元) / 现价 推算
- 逐笔成交：免费 HTTP 源无，txs 返回空列表（decay_sell/sell_surge 随之退化）

接口与 TdxPool 对齐（connect/fetch_stock/fetch_bars/fetch_finance/disconnect），
并额外提供 fetch_quotes_batch 供 poll_pool_once 一次批量拉取。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from ..config import BEIJING_TZ, CONFIG

logger = logging.getLogger(__name__)

_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
_SINA_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)

# TDX 分钟周期 category -> 新浪 scale（免费源无 1 分钟，category 8 退化为 5 分钟）
_CATEGORY_TO_SCALE = {0: "5", 1: "15", 8: "5"}


def _num(value):
    try:
        v = float(value)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def market_symbol(code: str) -> str:
    return ("sh" if str(code).startswith(("6", "9")) else "sz") + str(code).zfill(6)


def market_of(code: str) -> int:
    return 1 if str(code).startswith(("6", "9")) else 0


def parse_tencent_orderbook(fields: list) -> Optional[dict]:
    """腾讯 qt.gtimg.cn 88 字段 -> 雷达 quote 格式（五档 + 外盘内盘 + 基础价）。"""
    if not isinstance(fields, list) or len(fields) < 33:
        return None
    at = lambda i: fields[i] if i < len(fields) else None  # noqa: E731
    return {
        "price": _num(at(3)), "last_close": _num(at(4)), "change_pct": _num(at(32)),
        "bid1": _num(at(9)), "bid_vol1": _num(at(10)),
        "bid2": _num(at(11)), "bid_vol2": _num(at(12)),
        "bid3": _num(at(13)), "bid_vol3": _num(at(14)),
        "bid4": _num(at(15)), "bid_vol4": _num(at(16)),
        "bid5": _num(at(17)), "bid_vol5": _num(at(18)),
        "ask1": _num(at(19)), "ask_vol1": _num(at(20)),
        "ask2": _num(at(21)), "ask_vol2": _num(at(22)),
        "ask3": _num(at(23)), "ask_vol3": _num(at(24)),
        "ask4": _num(at(25)), "ask_vol4": _num(at(26)),
        "ask5": _num(at(27)), "ask_vol5": _num(at(28)),
        # 外盘/内盘 = 当日累计主动买/主动卖，近似 TDX 的 b_vol/s_vol
        "b_vol": _num(at(7)), "s_vol": _num(at(8)),
        "name": at(1),
        # 流通市值(亿元)，供 fetch_finance 推算流通股本
        "_float_mv": _num(at(44)),
    }


class TencentQuoteSource:
    """HTTP 行情源，接口与 TdxPool 对齐。"""

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._session = None
        self._last_quotes: Dict[str, dict] = {}

    # ---- 生命周期（兼容 TdxPool 调用约定）----
    def connect(self) -> bool:
        import requests

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0"})
        return True

    def disconnect(self):
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def ensure_alive(self):
        if self._session is None:
            self.connect()

    def _get(self, url: str, params: dict = None, referer: str = ""):
        self.ensure_alive()
        headers = {"Referer": referer} if referer else {}
        return self._session.get(url, params=params, headers=headers, timeout=self.timeout)

    # ---- 批量五档（poll_pool_once 主路径）----
    def fetch_quotes_batch(self, codes: List[str]) -> Dict[str, dict]:
        """一次请求批量拉取五档，返回 {code: quote_dict}。"""
        codes = [str(c).zfill(6) for c in (codes or [])]
        if not codes:
            return {}
        symbols = [market_symbol(c) for c in codes]
        try:
            resp = self._get(_TENCENT_QUOTE_URL + ",".join(symbols), referer="https://gu.qq.com/")
            resp.raise_for_status()
            resp.encoding = "gbk"
            text = resp.text
        except Exception as exc:
            logger.warning("腾讯五档批量拉取失败: %s", exc)
            return {}
        result: Dict[str, dict] = {}
        for m in re.findall(r'v_((?:sh|sz)\d{6})="([^"\r\n]*)"', text):
            code = m[0][2:]
            quote = parse_tencent_orderbook(m[1].split("~"))
            if quote is not None and quote.get("price") is not None:
                result[code] = quote
        self._last_quotes = result
        return result

    # ---- 单只兼容（保留 TdxPool 同款接口）----
    def fetch_stock(self, market: int, code: str) -> dict:
        code = str(code).zfill(6)
        quote = self._last_quotes.get(code)
        if quote is None:
            quote = self.fetch_quotes_batch([code]).get(code)
        if quote is None:
            raise RuntimeError(f"{code} 无报价")
        now = datetime.now(BEIJING_TZ)
        return {
            "code": code,
            "quote": quote,
            "txs": [],  # 免费 HTTP 源无逐笔
            "servertime": now.strftime("%H:%M:%S"),
            "fetched_at": now.isoformat(),
        }

    def fetch_bars(self, market: int, code: str, category: int, count: int) -> list:
        """新浪分钟K，映射为雷达 bar 格式（vol=手, amount≈close×volume）。"""
        symbol = market_symbol(code)
        scale = _CATEGORY_TO_SCALE.get(category, "5")
        try:
            resp = self._get(
                _SINA_KLINE_URL,
                params={"symbol": symbol, "scale": scale, "ma": "no", "datalen": str(count)},
                referer="https://finance.sina.com.cn/",
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("%s 分钟K拉取失败: %s", code, exc)
            return []
        if not isinstance(data, list):
            return []

        bars = []
        for item in data:
            close = _num(item.get("close"))
            high = _num(item.get("high"))
            low = _num(item.get("low"))
            op = _num(item.get("open"))
            vol_shares = _num(item.get("volume"))
            if close is None:
                continue
            day_raw = str(item.get("day", ""))
            # 雷达约定 datetime 为 "YYYY-MM-DD HH:MM"（16 字符，无秒）
            datetime_str = day_raw[:16] if len(day_raw) >= 16 else day_raw
            bars.append({
                "open": op, "close": close, "high": high, "low": low,
                "vol": (vol_shares or 0) / 100.0,  # 股 -> 手
                "amount": (close * (vol_shares or 0)) if close is not None else 0.0,
                "datetime": datetime_str,
                "time": datetime_str[11:16] if len(datetime_str) >= 16 else datetime_str,
            })
        return bars

    def fetch_finance(self, market: int, code: str) -> dict:
        """流通股本(股) = 腾讯流通市值(亿元) / 现价。"""
        code = str(code).zfill(6)
        quote = self._last_quotes.get(code)
        if quote is None:
            quote = self.fetch_quotes_batch([code]).get(code) or {}
        mv = quote.get("_float_mv")  # 亿元
        price = quote.get("price")
        liutongguben = None
        if mv is not None and price and price > 0:
            liutongguben = mv * 1e8 / price
        return {"liutongguben": liutongguben, "source": "tencent"}


def make_quote_source():
    """按配置返回行情源；默认腾讯（TDX 免费源已失效，仅留作显式回退）。"""
    if CONFIG.get("radar_quote_source", "tencent") == "tdx":
        from .tdx_source import TdxPool

        return TdxPool()
    return TencentQuoteSource()
