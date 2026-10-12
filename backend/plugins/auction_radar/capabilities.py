"""28 字段能力表：按来源/市场/阶段/字段索引/语义/单位逐字段登记。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldCapability:
    source: str
    exchange: str
    market_phase: str
    raw_field_index: int
    semantic: str
    unit: str = ""
    observed_at: str = ""
    verified: bool = False
    evidence: str = ""

    def key(self):
        return (self.source, self.exchange, self.market_phase,
                self.raw_field_index, self.semantic)


class CapabilityRegistry:
    """P0 默认「未验证」；Phase 1 probe 取证后把 verified 置 True。"""

    def __init__(self):
        self._rows = {}

    def register(self, cap: FieldCapability):
        self._rows[cap.key()] = cap

    def is_verified(self, cap: FieldCapability) -> bool:
        row = self._rows.get(cap.key())
        return bool(row and row.verified)

    def rows(self):
        return list(self._rows.values())

    def to_dicts(self):
        return [r.__dict__ for r in self._rows.values()]
