"""28 低位竞价雷达 DTO（dataclass，独立于供应商）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AuctionQuote:
    trade_date: str
    code: str
    name: Optional[str] = None
    source: str = ""
    source_timestamp: Optional[str] = None
    received_at: Optional[str] = None
    market_phase: str = "UNKNOWN"
    # 分支 A/B 均需
    indicative_price_yuan: Optional[float] = None
    prev_close_yuan: Optional[float] = None
    float_shares: Optional[int] = None
    price_limit_up_yuan: Optional[float] = None
    price_limit_down_yuan: Optional[float] = None
    # 分支 A：完整因子（仅实测确认可得时启用）
    indicative_match_shares: Optional[int] = None
    indicative_unmatched_shares: Optional[int] = None
    unmatched_side: Optional[str] = None
    final_open_price_yuan: Optional[float] = None
    final_match_shares: Optional[int] = None
    # 分支 B：有限因子（真实普通五档取证后启用）
    orderbook_bid_vol_total: Optional[float] = None
    orderbook_ask_vol_total: Optional[float] = None
    # 行情字段（腾讯原报文基础字段，语义待取证）
    turnover: Optional[float] = None
    volume_ratio: Optional[float] = None
    # 审计
    source_volume_raw: Optional[float] = None
    source_amount_raw: Optional[float] = None
    source_volume_unit: Optional[str] = None
    field_capability_version: str = "unverified"
    raw_payload_hash: str = ""
    quality: str = "UNKNOWN"
    quality_reasons: list[str] = field(default_factory=list)
    confirmed_fields: list[str] = field(default_factory=list)


@dataclass
class QualityResult:
    quality: str
    reasons: list[str] = field(default_factory=list)
    allowed_fields: list[str] = field(default_factory=list)
    lag_ms: Optional[float] = None
    fallback_required: bool = False


@dataclass
class PoolSnapshot:
    status: str = "empty"
    asof_trading_day: Optional[str] = None
    items: list[dict] = field(default_factory=list)
    pool_hash: Optional[str] = None
    total_count: int = 0
    producer_completed_at: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class FrozenPool:
    trade_date: str
    asof_trading_day: str
    pool_hash: str
    items: list[dict] = field(default_factory=list)
    total_count: int = 0


@dataclass
class Milestone:
    key: str
    state: str = "pending"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "evidence": self.evidence,
            "error": self.error,
        }


@dataclass
class RankingSnapshot:
    items: list[dict] = field(default_factory=list)
    ranking_version: Optional[str] = None
    ranking_asof: Optional[str] = None
    scope_is_complete: bool = False


@dataclass
class Publishability:
    all_top_n_confirmed: bool = False
    fast_slo_met: bool = False
    confirmed_count: int = 0
    unconfirmed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
