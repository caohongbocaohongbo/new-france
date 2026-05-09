"""
BaseSkill — 所有因子的抽象基类
每个因子独立一个文件，统一通过 score() 接口返回 SkillResult
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SkillResult:
    """单个因子的评分结果"""
    name: str                              # 因子名称
    key: str                               # 因子 key
    score: float                           # 0.0 - 10.0 得分
    weight: float                          # 因子权重
    detail: str                            # 评分细节描述
    passed: bool = True                    # 硬门控是否通过


class BaseSkill(ABC):
    """每个因子的标准化接口"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    @abstractmethod
    def weight(self) -> float: ...

    @abstractmethod
    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        """
        计算 0-10 分
        ctx 包含: code, name, current_price, ref_price, drop_pct,
                  history (DataFrame), 行情字段 (量比/换手率/PE/流通市值/涨跌幅),
                  zt_quality (封板时间/炸板次数/涨停频率),
                  events (匹配事件列表), index_gain
        """

    # ---- 评分工具方法 ----
    @staticmethod
    def linear_score(value: float, ideal_min: float, ideal_max: float,
                     floor: float = 0.0, ceil: float = 10.0) -> float:
        """线性评分：在理想区间内得满分，向外线性递减到 floor"""
        if ideal_min <= value <= ideal_max:
            return ceil
        if value < ideal_min:
            ratio = value / ideal_min if ideal_min > 0 else 0
            return max(floor, ratio * ceil)
        # value > ideal_max
        over = (value - ideal_max) / ideal_max
        return max(floor, ceil - over * ceil)

    @staticmethod
    def clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))
