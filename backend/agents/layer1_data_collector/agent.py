"""
Layer 1: DataCollectorAgent — 只读数据采集
职责: 从外部API拉取所有数据，不做计算或写入
"""
import asyncio
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

# 历史K线并发抓取限制（避免被数据源封IP）
_KLINE_SEMAPHORE = asyncio.Semaphore(8)


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
        """为监控列表股票拉取实时行情（Tushare优先 → 东方财富降级）"""
        if not codes:
            return pd.DataFrame()
        logger.info(f"  拉取 {len(codes)} 只监控股票的实时行情...")

        pd.set_option('future.no_silent_downcasting', True)

        # 1. 尝试 Tushare（主数据源，99.9%准确率）
        try:
            from .sources.tushare_source import fetch_quotes as ts_quotes, is_available
            if is_available():
                ts_df = ts_quotes(codes)
                if not ts_df.empty:
                    logger.info(f"  Tushare 实时行情: {len(ts_df)} 只")
                    return ts_df
        except Exception as e:
            logger.debug(f"  Tushare 行情异常: {e}")

        # 2. 降级到东方财富
        df = fetch_stock_quotes(codes)
        if df.empty:
            logger.warning("  所有实时行情数据源均失败，将使用历史K线降级价格")
        return df

    async def collect_historical_batch(self, symbols: List[str],
                                       days: int = 60) -> Dict[str, pd.DataFrame]:
        """批量拉取历史K线（Tushare优先 → 东方财富降级，并发限制 8）"""
        result = {}

        # 1. 尝试 Tushare 批量获取（主数据源，99.5%准确率）
        try:
            from .sources.tushare_source import fetch_historical as ts_historical, is_available
            if is_available():
                ts_result = await asyncio.to_thread(ts_historical, symbols, days)
                if ts_result:
                    result.update(ts_result)
                    logger.info(f"  Tushare 历史K线: {len(ts_result)}/{len(symbols)} 成功"
                                f" ({len(ts_result)/len(symbols)*100:.0f}%)")
                    missing = [s for s in symbols if s not in result]
                    if not missing:
                        return result
                    symbols = missing  # 只对缺失的降级
        except Exception as e:
            logger.debug(f"  Tushare 历史K线异常: {e}")

        # 2. 降级到东方财富（单只并发）
        failed = []

        async def _fetch_one(sym):
            try:
                async with _KLINE_SEMAPHORE:
                    hist = await asyncio.to_thread(fetch_historical, sym, days)
                if hist is not None and not hist.empty:
                    result[sym] = hist
                else:
                    failed.append(sym)
            except Exception as e:
                logger.debug(f"  {sym} 历史数据失败: {e}")
                failed.append(sym)

        await asyncio.gather(*(_fetch_one(s) for s in symbols))
        total = len(symbols)
        success = len([s for s in symbols if s in result])
        if total > 0:
            logger.info(f"  东方财富历史K线: {success}/{total} 成功 ({success/total*100:.0f}%)"
                        f"{'，失败: '+','.join(failed[:5])+('...' if len(failed)>5 else '') if failed else ''}")
        return result

    def _safe_fetch(self, label: str, fn, pack: RawDataPack, **kwargs):
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                result = fn(**kwargs)
                if attempt > 0:
                    logger.info(f"  {label} 第{attempt+1}次重试成功")
                else:
                    logger.info(f"  {label} 获取成功")
                return result
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    import asyncio
                    wait = (attempt + 1) * 2  # 渐进等待: 2s, 4s
                    logger.warning(f"  {label} 第{attempt+1}次失败: {e}, {wait}s后重试...")
                    import time
                    time.sleep(wait)
        msg = f"{label} 获取失败({max_retries}次): {last_error}"
        logger.warning(f"  {msg}")
        pack.errors.append(msg)
        return None
