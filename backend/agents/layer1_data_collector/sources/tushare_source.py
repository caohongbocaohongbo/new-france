"""
Tushare 数据源适配层 — 实时行情 + 历史K线 + 涨停池
作为主数据源，与东方财富/AKShare 交叉验证

Tushare 准确率 99.9%，延迟 1-3 秒，需 TOKEN 注册 (https://tushare.pro)
无 TOKEN 时自动降级到东方财富/AKShare
"""
import os
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, date, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))

# 尝试初始化 Tushare
_ts_pro = None


def _get_pro():
    """延迟初始化 Tushare Pro API"""
    global _ts_pro
    if _ts_pro is not None:
        return _ts_pro

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        logger.info("未配置 TUSHARE_TOKEN，Tushare 不可用")
        return None

    try:
        import tushare as ts
        ts.set_token(token)
        _ts_pro = ts.pro_api()
        logger.info("Tushare Pro API 已连接")
        return _ts_pro
    except Exception as e:
        logger.warning(f"Tushare 初始化失败: {e}")
        return None


def is_available() -> bool:
    """检查 Tushare 是否可用"""
    return _get_pro() is not None


def fetch_quotes(codes: List[str]) -> pd.DataFrame:
    """
    通过 Tushare 批量获取实时行情。
    失败时返回空 DataFrame（调用方应降级到东方财富源）。
    """
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()

    try:
        def _ts_market(c):
            if c.startswith('8'): return 'BJ'
            if c.startswith(('6', '9')): return 'SH'
            return 'SZ'
        ts_codes = [f"{code}.{_ts_market(code)}" for code in codes]
        today = datetime.now(BEIJING_TZ).strftime("%Y%m%d")

        df = pro.daily_basic(
            ts_code=",".join(ts_codes),
            trade_date=today,
            fields="ts_code,trade_date,close,pe,pe_ttm,pb,total_mv,circ_mv,volume_ratio,turnover_rate"
        )
        if df is None or df.empty:
            logger.debug("Tushare 实时行情返回空数据")
            return pd.DataFrame()

        # 补充股票名称（stock_basic 缓存）
        codes_in_df = [ts_code.split(".")[0] for ts_code in df["ts_code"].tolist()]
        name_map = {}
        try:
            basic = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
            if basic is not None and not basic.empty:
                for _, r in basic.iterrows():
                    c = str(r["ts_code"]).split(".")[0]
                    name_map[c] = str(r.get("name", ""))
        except Exception:
            pass

        rows = []
        for _, row in df.iterrows():
            ts_code = str(row["ts_code"])
            code = ts_code.split(".")[0]

            # PE: 优先 pe_ttm，但0.0也是合法值（不能用 or 短路）
            pe_ttm_val = row.get("pe_ttm")
            pe_val = row.get("pe")
            if pd.notna(pe_ttm_val):
                pe = float(pe_ttm_val)
            elif pd.notna(pe_val):
                pe = float(pe_val)
            else:
                pe = None

            rows.append({
                "代码": code,
                "名称": name_map.get(code, ""),
                "最新价": float(row["close"]) if pd.notna(row["close"]) else None,
                "涨跌幅": None,  # daily_basic 不含涨跌幅，由调用方用东方财富补全
                "换手率": float(row["turnover_rate"]) if pd.notna(row["turnover_rate"]) else None,
                "市盈率": pe,
                "量比": float(row["volume_ratio"]) if pd.notna(row["volume_ratio"]) else None,
                "总市值": float(row["total_mv"]) if pd.notna(row["total_mv"]) else None,
                "流通市值": float(row["circ_mv"]) if pd.notna(row["circ_mv"]) else None,
                "source": "tushare",
            })

        return pd.DataFrame(rows)

        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"Tushare 行情获取失败: {e}")
        return pd.DataFrame()


def fetch_historical(codes: List[str], days: int = 60) -> Dict[str, pd.DataFrame]:
    """
    通过 Tushare 批量获取历史日K线数据。
    返回 {code: DataFrame} 字典。
    """
    pro = _get_pro()
    if pro is None:
        return {}

    try:
        def _ts_market(c):
            if c.startswith('8'): return 'BJ'
            if c.startswith(('6', '9')): return 'SH'
            return 'SZ'
        ts_codes = [f"{code}.{_ts_market(code)}" for code in codes]
        end_date = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        start_date = (datetime.now(BEIJING_TZ) - timedelta(days=days + 10)).strftime("%Y%m%d")

        # Tushare 限制单次查询最多 100 个 ts_code
        results: Dict[str, pd.DataFrame] = {}
        batch_size = 80

        for i in range(0, len(ts_codes), batch_size):
            batch = ts_codes[i:i + batch_size]
            try:
                df = pro.daily(
                    ts_code=",".join(batch),
                    start_date=start_date,
                    end_date=end_date,
                    fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"
                )
                if df is None or df.empty:
                    continue

                for ts_code, group in df.groupby("ts_code"):
                    code = ts_code.split(".")[0]
                    group = group.sort_values("trade_date")
                    group = group.rename(columns={
                        "trade_date": "日期",
                        "open": "开盘",
                        "high": "最高",
                        "low": "最低",
                        "close": "收盘",
                        "vol": "成交量",
                        "amount": "成交额",
                        "pct_chg": "涨跌幅",
                    })
                    group["日期"] = group["日期"].astype(str)
                    results[code] = group
            except Exception as e:
                logger.warning(f"Tushare 历史K线批次 {i // batch_size + 1} 失败: {e}")
                continue

        return results
    except Exception as e:
        logger.warning(f"Tushare 历史K线获取失败: {e}")
        return {}


def fetch_daily_basic_history(code: str, days: int = 60) -> pd.DataFrame:
    """通过 Tushare 获取个股历史换手率、量比、PE 等日频指标。

    无 TUSHARE_TOKEN 或接口失败时返回空 DataFrame，调用方应保持空态而不是造数。
    """
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()

    try:
        def _ts_market(c):
            if c.startswith('8'): return 'BJ'
            if c.startswith(('6', '9')): return 'SH'
            return 'SZ'

        ts_code = f"{code}.{_ts_market(code)}"
        end_date = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        start_date = (datetime.now(BEIJING_TZ) - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,pe,pe_ttm,volume_ratio,turnover_rate",
        )
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.sort_values("trade_date").rename(columns={
            "trade_date": "日期",
            "pe_ttm": "PE_TTM",
            "pe": "PE",
            "volume_ratio": "量比",
            "turnover_rate": "换手率",
        })
        df["日期"] = df["日期"].astype(str)
        return df
    except Exception as e:
        logger.warning(f"Tushare 历史基础指标获取失败: {e}")
        return pd.DataFrame()


def fetch_zt_pool(trade_date: Optional[date] = None) -> Optional[pd.DataFrame]:
    """
    通过 Tushare 获取当日涨停股池（stk_limit + daily 联合查询）。
    Tushare 没有直接对应东方财富 getTopicZTPool 的接口，
    通过 daily 表中 pct_chg >= 9.8 近似获取。
    """
    pro = _get_pro()
    if pro is None:
        return None

    try:
        if trade_date is None:
            target = datetime.now(BEIJING_TZ).date()
        else:
            target = trade_date
        date_str = target.strftime("%Y%m%d")

        # 获取当日所有A股行情，筛选涨跌幅 >= 9.8% 的
        df = pro.daily(trade_date=date_str, fields="ts_code,trade_date,close,pct_chg,vol,amount")
        if df is None or df.empty:
            return None

        zt_candidates = df[df["pct_chg"] >= 9.8].copy()
        if zt_candidates.empty:
            return None

        # 补充股票名称
        try:
            stock_basic = pro.stock_basic(exchange="", list_status="L",
                                          fields="ts_code,name,industry")
            if stock_basic is not None and not stock_basic.empty:
                zt_candidates = zt_candidates.merge(stock_basic, on="ts_code", how="left")
        except Exception:
            pass

        rows = []
        for _, row in zt_candidates.iterrows():
            ts_code = str(row["ts_code"])
            code = ts_code.split(".")[0]
            rows.append({
                "代码": code,
                "名称": str(row.get("name", "")),
                "最新价": float(row["close"]),
                "涨跌幅": float(row["pct_chg"]),
                "换手率": None,
                "流通市值": None,
                "封板时间": 0,
                "炸板次数": 0,
                "连板数": 0,
                "涨停统计": "",
                "所属行业": str(row.get("industry", "")),
            })

        df_result = pd.DataFrame(rows)
        df_result.attrs["source_meta"] = {
            "source": "tushare_daily",
            "date": date_str,
            "raw_count": len(zt_candidates),
            "final_count": len(df_result),
            "filtered_count": 0,
            "fetched_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "note": "Tushare daily 表 pct_chg >= 9.8 近似涨停池，补充东方财富 getTopicZTPool 做精确涨停判断",
        }
        return df_result
    except Exception as e:
        logger.warning(f"Tushare 涨停池获取失败: {e}")
        return None


def cross_validate_price(code: str, tushare_price: Optional[float],
                         eastmoney_price: Optional[float]) -> Dict:
    """
    交叉验证两个数据源的价格，差异 > 1% 时告警。
    返回验证结果字典。
    """
    result = {
        "code": code,
        "tushare": tushare_price,
        "eastmoney": eastmoney_price,
        "verified": False,
        "discrepancy_pct": None,
    }

    if tushare_price and eastmoney_price and tushare_price > 0:
        diff = abs(tushare_price - eastmoney_price)
        pct = diff / tushare_price * 100
        result["discrepancy_pct"] = round(pct, 4)
        result["verified"] = pct < 1.0
        if pct >= 1.0:
            logger.warning(f"  ⚠ {code} 价格交叉验证失败: Tushare={tushare_price:.2f}, "
                           f"东方财富={eastmoney_price:.2f}, 差异={pct:.2f}%")

    return result
