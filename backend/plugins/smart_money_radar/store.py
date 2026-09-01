"""盘中雷达内存滑动窗口与低频数据缓存。"""
from collections import defaultdict, deque
from datetime import datetime


class RadarStore:
    """保存单日滑动窗口，Phase 1 覆盖报价与逐笔去重。"""

    def __init__(self, max_quotes: int = 512, max_fund_points: int = 512):
        self.max_quotes = max_quotes
        self.max_fund_points = max(1, int(max_fund_points))
        self.quotes = defaultdict(lambda: deque(maxlen=max_quotes))
        self.last_num = {}
        self.minute_buckets = defaultdict(dict)
        self.stage = {}
        self.bar_cache = {}
        self.finance_cache = {}
        self.fund_series = defaultdict(lambda: deque(maxlen=self.max_fund_points))
        self.auction_frames = defaultdict(lambda: deque(maxlen=max_quotes))
        self.state_date = None

    def ensure_date(self, now: datetime) -> None:
        """交易日切换时清空所有单日状态。"""
        current_date = now.date()
        if self.state_date is not None and self.state_date != current_date:
            self.reset()
        self.state_date = current_date

    def add_quote(self, code: str, quote: dict, now: datetime) -> None:
        self.quotes[str(code).zfill(6)].append({"ts": now.isoformat(), "quote": dict(quote or {})})

    def smooth_strength(self, code: str, frames: int) -> float:
        rows = list(self.quotes[str(code).zfill(6)])[-max(1, int(frames)):]
        values = []
        for row in rows:
            value = row.get("quote", {}).get("_strength_amt")
            if isinstance(value, (int, float)):
                values.append(float(value))
        if not values:
            return 0.0
        return round(sum(values) / len(values), 2)

    def update_transactions(self, code: str, txs: list, now: datetime) -> list:
        code = str(code).zfill(6)
        last = int(self.last_num.get(code, 0) or 0)
        fresh = []
        for tx in txs or []:
            try:
                num = int(tx.get("num") or 0)
            except (TypeError, ValueError):
                continue
            if num <= last:
                continue
            fresh.append(dict(tx))
            minute = str(tx.get("time") or now.strftime("%H:%M"))
            bucket = self.minute_buckets[code].setdefault(
                minute,
                {"buy_amt": 0.0, "sell_amt": 0.0, "neutral_amt": 0.0, "count": 0},
            )
            price = _to_float(tx.get("price"), 0) or 0
            vol = _to_float(tx.get("vol"), 0) or 0
            amount = price * vol * 100
            side = int(tx.get("buyorsell") if tx.get("buyorsell") is not None else 2)
            if side == 0:
                bucket["buy_amt"] += amount
            elif side == 1:
                bucket["sell_amt"] += amount
            else:
                bucket["neutral_amt"] += amount
            bucket["count"] += 1
            last = max(last, num)
        if fresh:
            self.last_num[code] = last
        return fresh

    def cache_bars(self, code: str, category: int, bars: list, fetched_at: datetime) -> None:
        key = (str(code).zfill(6), int(category))
        self.bar_cache[key] = {"fetched_at": fetched_at, "bars": list(bars or [])}

    def get_cached_bars(self, code: str, category: int, now: datetime, ttl_seconds: float):
        key = (str(code).zfill(6), int(category))
        entry = self.bar_cache.get(key)
        if not entry or (now - entry["fetched_at"]).total_seconds() > float(ttl_seconds):
            return None
        return list(entry["bars"])

    def cache_finance(self, code: str, finance: dict, fetched_at: datetime) -> None:
        self.finance_cache[str(code).zfill(6)] = {
            "date": fetched_at.date(),
            "finance": dict(finance or {}),
        }

    def get_cached_finance(self, code: str, now: datetime):
        entry = self.finance_cache.get(str(code).zfill(6))
        if not entry or entry["date"] != now.date():
            return None
        return dict(entry["finance"])

    def add_fund_point(self, code: str, point: dict) -> None:
        self.fund_series[str(code).zfill(6)].append(dict(point or {}))

    def add_auction_frame(self, code: str, quote: dict, now: datetime) -> None:
        """13 集合竞价采样帧（收盘 reset）。"""
        self.auction_frames[str(code).zfill(6)].append({"ts": now.isoformat(), "quote": dict(quote or {})})

    def gc(self, now: datetime, ttl_seconds: float = 45) -> None:
        """午休清理过期分钟线缓存，保留盘口和资金日内窗口。"""
        expired = [
            key for key, entry in self.bar_cache.items()
            if (now - entry["fetched_at"]).total_seconds() > float(ttl_seconds)
        ]
        for key in expired:
            self.bar_cache.pop(key, None)

    def reset(self) -> None:
        self.quotes.clear()
        self.last_num.clear()
        self.minute_buckets.clear()
        self.stage.clear()
        self.bar_cache.clear()
        self.finance_cache.clear()
        self.fund_series.clear()
        self.auction_frames.clear()
        self.state_date = None


def _to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
