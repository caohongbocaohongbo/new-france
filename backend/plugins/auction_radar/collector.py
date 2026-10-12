"""28 低位竞价雷达采集器：腾讯批量 + 原报文提取 + 基础校验（字段语义标记未验证）。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime

from .config import BEIJING_TZ, CONFIG
from .models import AuctionQuote

logger = logging.getLogger(__name__)

_TENCENT_URL = "https://qt.gtimg.cn/q="


def market_symbol(code: str) -> str:
    return ("sh" if str(code).startswith(("6", "9")) else "sz") + str(code).zfill(6)


def _num(value):
    try:
        v = float(value)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def parse_tencent_auction_quote(text, capability_version="unverified") -> dict[str, AuctionQuote]:
    """从腾讯原报文提取基础字段 + 五档；字段语义按能力版本标记，不默认证明。"""
    quotes = {}
    for m in re.findall(r'v_((?:sh|sz)\d{6})="([^"\r\n]*)"', text):
        symbol, raw = m[0], m[1]
        code = symbol[2:]
        fields = raw.split("~")
        if len(fields) < 50 or not fields[1]:
            continue
        at = lambda i: fields[i] if i < len(fields) else None  # noqa: E731
        price, prev = _num(at(3)), _num(at(4))
        if price is None or prev is None:
            continue
        source_ts = None
        try:
            source_ts = datetime.strptime(at(30), "%Y%m%d%H%M%S").replace(tzinfo=BEIJING_TZ).isoformat()
        except (TypeError, ValueError):
            source_ts = None
        bid_vol_total = sum((_num(at(i)) or 0) for i in range(10, 19, 2))
        ask_vol_total = sum((_num(at(i)) or 0) for i in range(20, 29, 2))
        raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        quotes[code] = AuctionQuote(
            trade_date="",
            code=code,
            name=at(1),
            source="tencent",
            source_timestamp=source_ts,
            received_at=datetime.now(BEIJING_TZ).isoformat(),
            market_phase="UNKNOWN",
            indicative_price_yuan=price,
            prev_close_yuan=prev,
            price_limit_up_yuan=_num(at(47)),
            price_limit_down_yuan=_num(at(48)),
            orderbook_bid_vol_total=bid_vol_total,
            orderbook_ask_vol_total=ask_vol_total,
            turnover=_num(at(38)),
            volume_ratio=_num(at(49)),
            source_volume_raw=_num(at(6)),
            source_amount_raw=(_num(at(37)) or 0) * 10000 if _num(at(37)) is not None else None,
            source_volume_unit="lot",
            field_capability_version=capability_version,
            raw_payload_hash=raw_hash,
            quality="UNKNOWN",
            confirmed_fields=[],
        )
    return quotes


class TencentAuctionSource:
    def __init__(self, timeout: float = None):
        self.timeout = timeout or float(CONFIG.get("http_timeout_s", 5))
        self._session = None

    def connect(self):
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

    def fetch_quotes_batch(self, codes):
        codes = [str(c).zfill(6) for c in (codes or [])]
        if not codes:
            return {}
        if self._session is None:
            self.connect()
        url = _TENCENT_URL + ",".join(market_symbol(c) for c in codes)
        try:
            resp = self._session.get(url, headers={"Referer": "https://gu.qq.com/"}, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = "gbk"
            return parse_tencent_auction_quote(resp.text, CONFIG.get("field_capability_version", "unverified"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("腾讯五档批量拉取失败: %s", exc)
            return {}


async def poll_pool_once(source, codes, cfg=None, deadline: float = None):
    """分批拉取低位池竞价快照；Semaphore 限流；返回 [{code, quote}]。"""
    cfg = cfg or CONFIG
    codes = [str(c).zfill(6) for c in (codes or [])]
    if not codes:
        return []
    batch = max(1, int(cfg.get("batch_size", 60)))
    sem = asyncio.Semaphore(max(1, int(cfg.get("concurrency", 4))))
    payloads = []

    async def one(batch_codes):
        async with sem:
            try:
                quotes = await asyncio.to_thread(source.fetch_quotes_batch, batch_codes)
            except Exception as exc:  # noqa: BLE001
                return [{"code": c, "quote": None, "error": str(exc)} for c in batch_codes]
            out = []
            for c in batch_codes:
                q = quotes.get(c)
                if q is None:
                    out.append({"code": c, "quote": None, "error": f"{c} 无报价"})
                else:
                    out.append({"code": c, "quote": q, "error": None})
            return out

    groups = [codes[i:i + batch] for i in range(0, len(codes), batch)]
    results = await asyncio.gather(*(one(g) for g in groups), return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            payloads.extend(r)
        else:
            payloads.append({"code": "", "quote": None, "error": str(r)})
    return payloads
