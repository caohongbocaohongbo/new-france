"""Signal Engine Skills — package init"""
from .base import BaseSkill, SkillResult
from .skill_pullback import PullbackSkill
from .skill_volume_ratio import VolumeRatioSkill
from .skill_turnover import TurnoverSkill
from .skill_market_cap import MarketCapSkill
from .skill_pe import PESkill
from .skill_volume_trend import VolumeTrendSkill
from .skill_ma_alignment import MAAlignmentSkill
from .skill_strength import StrengthSkill
from .skill_entry_point import EntryPointSkill
from .skill_zt_quality import ZTQualitySkill
from .skill_event_bonus import EventBonusSkill

# 注册所有因子
ALL_SKILLS = [
    PullbackSkill, VolumeTrendSkill, MAAlignmentSkill,
    StrengthSkill, EntryPointSkill, MarketCapSkill,
    VolumeRatioSkill, TurnoverSkill, PESkill,
    ZTQualitySkill, EventBonusSkill,
]

# ---- 可选注册: 主力资金插件 skill ----
try:
    from backend.plugins.principal_capital.skill import PrincipalCapitalSkill
    if PrincipalCapitalSkill not in ALL_SKILLS:
        ALL_SKILLS.append(PrincipalCapitalSkill)
except ImportError:
    pass

def build_skills() -> list:
    """实例化所有启用的因子"""
    return [cls() for cls in ALL_SKILLS]
