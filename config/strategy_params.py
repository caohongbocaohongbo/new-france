"""
策略参数 — 多因子权重 + 阈值配置
可以从 YAML 加载，也可以直接修改此文件
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FactorConfig:
    """单个因子的配置"""
    name: str
    key: str
    weight: float          # 权重 (0.0 - 1.0)
    enabled: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class StrategyConfig:
    """完整策略配置"""
    factors: List[FactorConfig] = field(default_factory=list)
    hard_gates: dict = field(default_factory=dict)

    # 推荐等级阈值
    strong_buy_threshold: float = 70.0
    buy_threshold: float = 55.0
    watch_threshold: float = 40.0

    # 监控周期
    tracking_days: int = 30

    # 涨停质量过滤
    latest_fbt: int = 140000      # 最晚封板时间 (HHMMSS)
    max_zbc: int = 0              # 最大炸板次数
    max_zt_frequency: int = 2     # 30天内最大涨停次数

    def active_factors(self) -> List[FactorConfig]:
        return [f for f in self.factors if f.enabled]

    def total_weight(self) -> float:
        return sum(f.weight for f in self.active_factors())

    def validate_weights(self) -> bool:
        return abs(self.total_weight() - 1.0) < 0.001


# ---- 默认因子配置 ----
DEFAULT_FACTORS = [
    FactorConfig(name="回撤幅度",     key="pullback",       weight=0.15,
                 params={"ideal_min": 5.0, "ideal_max": 8.0, "hard_max": 10.0}),
    FactorConfig(name="量能趋势",     key="volume_trend",   weight=0.12),
    FactorConfig(name="均线多头",     key="ma_alignment",   weight=0.12),
    FactorConfig(name="强势确认",     key="strength",       weight=0.10),
    FactorConfig(name="尾盘买点",     key="entry_point",    weight=0.10),
    FactorConfig(name="流通市值",     key="market_cap",     weight=0.10,
                 params={"ideal_min": 50.0, "ideal_max": 100.0, "hard_min": 50.0, "hard_max": 200.0}),
    FactorConfig(name="量比",         key="volume_ratio",   weight=0.08,
                 params={"ideal_min": 1.5, "ideal_max": 3.0, "hard_min": 1.0, "hard_max": 5.0}),
    FactorConfig(name="换手率",       key="turnover",       weight=0.08,
                 params={"ideal_min": 5.0, "ideal_max": 8.0, "hard_min": 5.0, "hard_max": 10.0}),
    FactorConfig(name="市盈率",       key="pe",             weight=0.08,
                 params={"max_pe": 50}),
    FactorConfig(name="涨停质量",     key="zt_quality",     weight=0.07,
                 params={"latest_fbt": 140000, "max_zbc": 0, "max_zt_frequency": 2}),
    FactorConfig(name="事件驱动加分", key="event_bonus",    weight=0.00,
                 params={"max_bonus": 8.0, "max_penalty": -8.0}),
]


# ---- 通知配置 ----
NOTIFY_CONFIG = {
    "email_enabled": True,
    "email_host": "smtp.qq.com",
    "email_port": 465,
    "email_user": "896256756@qq.com",
    "email_to": "896256756@qq.com",
    "wechat_enabled": False,
    "pushplus_enabled": False,
}

# ---- 邮件涨停列表排序配置 ----
# sort_by: change_pct(涨幅) / turnover(换手) / vol_ratio(量比) / pe(PE) / mcap(流通市值) / seal_time(封板)
# sort_order: asc / desc
ZT_SORT_CONFIG = {
    "sort_by": "seal_time",     # 默认按封板时间排序（最早封板在前）
    "sort_order": "asc",
}

DATA_DIR = str(PROJECT_DIR := __import__("pathlib").Path(__file__).resolve().parent.parent / "data")
