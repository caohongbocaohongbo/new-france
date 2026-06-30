"""
Layer 2: SignalEngineAgent — 纯计算信号引擎
职责: 多因子打分、事件相关性匹配、综合排序
约束: 无任何IO操作（网络/文件/DB），纯函数式
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import pandas as pd

from .scoring import MultiFactorScorer, ScoredStock
from .skills import build_skills

logger = logging.getLogger(__name__)


class SignalEngineAgent:
    """纯计算信号引擎 — 禁止任何IO操作"""

    def __init__(self, factor_weights: Optional[Dict[str, float]] = None,
                 strategy_config: Optional[Dict[str, Any]] = None):
        self.strategy_config = strategy_config or {}
        skills = build_skills()
        for skill in skills:
            if factor_weights and skill.key in factor_weights:
                skill.weight = float(factor_weights[skill.key])
        self.scorer = MultiFactorScorer(skills=skills)

    def evaluate(self, candidates: List[Dict], quotes: pd.DataFrame,
                 historical: Dict[str, pd.DataFrame], index_gain: float,
                 events: Optional[Dict[str, List[Dict]]] = None) -> List[ScoredStock]:
        """对所有候选股票进行多因子评分"""
        quote_map = {}
        for _, row in quotes.iterrows():
            quote_map[row["代码"]] = row

        results = []
        for c in candidates:
            code = c["code"]
            q = quote_map.get(code, {})
            hist = historical.get(code)
            stock_events = (events or {}).get(code, [])

            ctx = {
                "code": code,
                "name": c.get("name", ""),
                "zt_date": c.get("zt_date", ""),
                "ref_price": c.get("ref_price", 0.0),
                "current_price": c.get("current_price", 0.0),
                "drop_pct": c.get("drop_pct", 0.0),
                "涨跌幅": q.get("涨跌幅"),
                "换手率": q.get("换手率"),
                "市盈率": q.get("市盈率"),
                "量比": q.get("量比"),
                "总市值": q.get("总市值"),
                "流通市值": q.get("流通市值"),
                "history": hist,
                "index_gain": index_gain,
                "events": stock_events,
                "main_inflow_ratio": c.get("main_inflow_ratio"),
                "zt_quality": {
                    "封板时间": c.get("封板时间", 0),
                    "炸板次数": c.get("炸板次数", 0),
                    "涨停频率": c.get("涨停频率", 0),
                    "latest_fbt": self.strategy_config.get("latestFbt"),
                    "max_zbc": self.strategy_config.get("maxZbc"),
                    "max_zt_frequency": self.strategy_config.get("maxZtFrequency"),
                },
                "strategy_params": self.strategy_config,
                "extra": {
                    "封板时间": c.get("封板时间", 0),
                    "炸板次数": c.get("炸板次数", 0),
                    "涨停频率": c.get("涨停频率", 0),
                    "added_date": c.get("added_date", c.get("zt_date", "")),
                    "price_history": c.get("price_history", []),
                    "quote_metrics": {
                        "pe": q.get("市盈率"),
                        "volume_ratio": q.get("量比"),
                        "turnover": q.get("换手率"),
                        "market_cap": q.get("流通市值"),
                    },
                },
            }

            try:
                scored = self.scorer.score_stock(ctx)
                if scored.adjusted_score >= self.scorer.thresholds["watch"]:
                    results.append(scored)
            except Exception as e:
                logger.error(f"  {code} {c.get('name', '')} 评分异常(已跳过该股): {type(e).__name__}: {e}")

        ranked = self.scorer.rank(results)
        logger.info(f"SignalEngine: {len(results)} 只进入推荐池 (WATCH及以上)")
        strong = sum(1 for s in ranked if s.recommendation == "STRONG_BUY")
        buy = sum(1 for s in ranked if s.recommendation == "BUY")
        logger.info(f"  STRONG_BUY: {strong}, BUY: {buy}, "
                     f"WATCH: {len(ranked) - strong - buy}")
        return ranked
