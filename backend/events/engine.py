"""
事件驱动引擎 — 采集业绩预告、财报日历、行业政策、大宗交易
"""
import logging
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class EventEngine:
    """事件采集 + 股票-事件关联"""

    async def collect_daily_events(self,
                                    target_date: Optional[date] = None) -> List[Dict]:
        """采集当日所有事件"""
        if target_date is None:
            target_date = date.today()
        events = []

        # 业绩预告
        earnings = await self._fetch_earnings_forecast(target_date)
        events.extend(earnings)

        # 财报日历
        calendar = await self._fetch_financial_calendar(target_date)
        events.extend(calendar)

        logger.info(f"EventEngine: {len(events)} 条事件")
        return events

    async def match_stock_events(self, events: List[Dict],
                                  watchlist_codes: List[str],
                                  watchlist_names: List[str]) -> Dict[str, List[Dict]]:
        """将事件关联到监控列表中的股票"""
        matched: Dict[str, List[Dict]] = {c: [] for c in watchlist_codes}

        for evt in events:
            related_codes = evt.get("related_codes", [])
            for code in related_codes:
                if code in matched:
                    matched[code].append(evt)

        return matched

    async def _fetch_earnings_forecast(self, target_date: date) -> List[Dict]:
        """业绩预告采集"""
        try:
            import akshare as ak
            df = ak.stock_yjyg_em(date=target_date.strftime("%Y%m%d"))
            if df is None or df.empty:
                return []

            events = []
            for _, row in df.iterrows():
                code = str(row.get('股票代码', '')).zfill(6)
                name = str(row.get('股票简称', ''))
                etype = str(row.get('预告类型', ''))
                change = float(row.get('变动幅度', 0) or 0)

                impact = 0
                if '增' in etype and change > 50:
                    impact = 8
                elif '增' in etype:
                    impact = 5
                elif '亏' in etype or '减' in etype:
                    impact = -5 if change < -50 else -3

                events.append({
                    "event_date": target_date.strftime("%Y-%m-%d"),
                    "event_type": "earnings_forecast",
                    "title": f"{name}({code}) 业绩预告: {etype} {change:+.1f}%",
                    "related_codes": [code],
                    "impact": "positive" if impact > 0 else ("negative" if impact < 0 else "neutral"),
                    "impact_score": impact,
                    "source": "东方财富",
                })
            return events
        except Exception as e:
            logger.warning(f"业绩预告采集失败: {e}")
            return []

    async def _fetch_financial_calendar(self, target_date: date) -> List[Dict]:
        """财报披露日历采集"""
        try:
            import akshare as ak
            df = ak.stock_yjbb_em(date=target_date.strftime("%Y%m%d"))
            if df is None or df.empty:
                return []

            events = []
            for _, row in df.iterrows():
                code = str(row.get('股票代码', '')).zfill(6)
                name = str(row.get('股票简称', ''))
                events.append({
                    "event_date": target_date.strftime("%Y-%m-%d"),
                    "event_type": "financial_calendar",
                    "title": f"{name}({code}) 财报披露日",
                    "related_codes": [code],
                    "impact": "neutral",
                    "impact_score": 3,
                    "source": "东方财富",
                })
            return events
        except Exception as e:
            logger.debug(f"财报日历采集失败: {e}")
            return []
