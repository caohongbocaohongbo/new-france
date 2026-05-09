"""
Layer 1: DataCollectorAgent — 只读数据采集
职责: 从外部API拉取所有数据，不做计算或写入
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List, Dict
import pandas as pd

from .sources.eastmoney_zt import fetch_zt_pool
from .sources.eastmoney_quote import fetch_stock_quotes
from .sources.historical_kline import fetch_historical
from .sources.index_data import fetch_index_gain

logger = logging.getLogger(__name__)


@dataclass
class RawDataPack:
    """不可变数据包，从 Layer1 传递给 Layer2"""
    date: str
    zt_pool: Optional[pd.DataFrame] = None
    quotes: pd.DataFrame = field(default_factory=pd.DataFrame)
    historical: Dict[str, pd.DataFrame] = field(default_factory=dict)
    index_gain: float = 0.0
    events: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class DataCollectorAgent:
    """只读数据采集Agent — 禁止任何写操作"""

    async def collect_daily_data(self, target_date: Optional[date] = None) -> RawDataPack:
        """采集每日全量数据"""
        if target_date is None:
            target_date = date.today()
        date_str = target_date.strftime("%Y-%m-%d")

        logger.info(f"DataCollectorAgent: 开始采集 {date_str} 数据...")
        pack = RawDataPack(date=date_str)

        # 1. 涨停股池
        pack.zt_pool = self._safe_fetch("涨停股池", fetch_zt_pool, pack)

        # 2. 上证指数涨幅
        pack.index_gain = fetch_index_gain()
        logger.info(f"  上证指数涨幅: {pack.index_gain:+.2f}%")

        # 3. 事件数据（由 EventEngine 预填充）
        # 外部调用时先传入 events

        return pack

    async def collect_watchlist_quotes(self, codes: List[str]) -> pd.DataFrame:
        """为监控列表股票拉取实时行情"""
        if not codes:
            return pd.DataFrame()
        logger.info(f"  拉取 {len(codes)} 只监控股票的实时行情...")
        return fetch_stock_quotes(codes)

    async def collect_historical_batch(self, symbols: List[str],
                                       days: int = 60) -> Dict[str, pd.DataFrame]:
        """批量拉取历史K线（带限流）"""
        result = {}
        for i, sym in enumerate(symbols):
            try:
                hist = fetch_historical(sym, days=days)
                if hist is not None and not hist.empty:
                    result[sym] = hist
                if i > 0 and i % 10 == 0:
                    time.sleep(0.5)  # 限流
            except Exception as e:
                logger.debug(f"  {sym} 历史数据失败: {e}")
        return result

    def _safe_fetch(self, label: str, fn, pack: RawDataPack, **kwargs):
        try:
            result = fn(**kwargs)
            logger.info(f"  {label} 获取成功")
            return result
        except Exception as e:
            msg = f"{label} 获取失败: {e}"
            logger.warning(f"  {msg}")
            pack.errors.append(msg)
            return None
