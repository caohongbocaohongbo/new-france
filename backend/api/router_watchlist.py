"""监控列表 API"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import logging
import pandas as pd

from ..services.watchlist_store import parse_watchlist, write_watchlist

router = APIRouter()
logger = logging.getLogger(__name__)

SORTABLE_FIELDS = {"drop_pct", "turnover", "vol_ratio", "pe"}


def _parse_watchlist():
    """解析 france.md 监控列表"""
    return parse_watchlist()


def _enrich_with_quotes(entries: list) -> list:
    """用东方财富实时行情补全回撤%、换手率、量比、PE、流通市值"""
    if not entries:
        return entries

    codes = [e["code"] for e in entries]
    try:
        from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes
        quotes = fetch_stock_quotes(codes)
    except Exception as e:
        logger.warning(f"行情拉取失败: {e}")
        quotes = None

    quote_map = {}
    if quotes is not None and not quotes.empty:
        for _, row in quotes.iterrows():
            quote_map[row["代码"]] = row

    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    today = datetime.now(BEIJING_TZ).date()

    result = []
    for e in entries:
        code = e["code"]
        ref = e["ref_price"]
        q = quote_map.get(code)
        if q is not None:
            current = q.get("最新价")
            if current is not None and current > 0:
                drop_pct = round((current - ref) / ref * 100, 2)
            else:
                drop_pct = None
            entry = {
                **e,
                "current_price": current,
                "drop_pct": drop_pct,
                "turnover": q.get("换手率"),
                "vol_ratio": q.get("量比"),
                "pe": q.get("市盈率"),
                "mcap_raw": q.get("流通市值"),
                "mcap": f"{q.get('流通市值', 0) / 1e8:.0f}亿" if q.get("流通市值") and q["流通市值"] > 0 else None,
            }
        else:
            entry = {
                **e,
                "current_price": None, "drop_pct": None,
                "turnover": None, "vol_ratio": None,
                "pe": None, "mcap_raw": None, "mcap": None,
            }

        # 状态判断（基于日期，不依赖行情）
        try:
            zt_date = datetime.strptime(e["zt_date"], "%Y-%m-%d").date()
        except Exception:
            zt_date = today
        days_since = (today - zt_date).days
        entry["status"] = "expired" if days_since > 30 else "active"

        result.append(entry)

    return result


def _parse_with_status(entries: list) -> list:
    """仅解析监控列表并标注状态（不含行情查询，速度快）"""
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    today = datetime.now(BEIJING_TZ).date()

    result = []
    for e in entries:
        try:
            zt_date = datetime.strptime(e["zt_date"], "%Y-%m-%d").date()
        except Exception:
            zt_date = today
        days_since = (today - zt_date).days
        result.append({
            **e,
            "status": "expired" if days_since > 30 else "active",
        })
    return result


def _as_sort_number(value):
    """行情字段可能来自 pandas/numpy；统一转成可排序数字。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _sort_watchlist(entries: list, sort_by: Optional[str], sort_order: str) -> list:
    if not sort_by:
        return entries
    if sort_by not in SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="不支持的排序字段")
    if sort_order not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="排序方向仅支持 asc 或 desc")

    reverse = sort_order == "desc"
    with_values = []
    without_values = []
    for entry in entries:
        value = _as_sort_number(entry.get(sort_by))
        if value is None:
            without_values.append(entry)
        else:
            with_values.append((value, entry))

    with_values.sort(key=lambda item: item[0], reverse=reverse)
    return [entry for _, entry in with_values] + without_values


@router.get("")
async def get_watchlist(status: Optional[str] = Query(None),
                        search: Optional[str] = Query(None),
                        sort_by: Optional[str] = Query(None),
                        sort_order: str = Query("asc"),
                        page: int = Query(1, ge=1),
                        size: int = Query(20, ge=1, le=500)):
    """获取监控列表（含实时行情补全）

    - size <= 50: 补全实时行情，用于展示与表头排序
    - size > 50: 仅标注状态不补全行情（用于全量统计）
    """
    entries = _parse_watchlist()

    # 大请求仅返回状态标注（不拉行情），小请求才补全行情
    if size > 50:
        entries = _parse_with_status(entries)
    else:
        entries = _enrich_with_quotes(entries)

    if search:
        entries = [e for e in entries
                   if search.lower() in e["code"] or search.lower() in e["name"]]

    if status:
        entries = [e for e in entries if e.get("status") == status]

    entries = _sort_watchlist(entries, sort_by, sort_order)

    total = len(entries)
    start = (page - 1) * size
    items = entries[start:start + size]

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/stats")
async def get_watchlist_stats():
    """监控列表统计"""
    entries = _parse_watchlist()
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    today = datetime.now(BEIJING_TZ)
    cutoff = today - timedelta(days=30)
    new_today = sum(1 for e in entries if e["zt_date"] == today.strftime("%Y-%m-%d"))
    return {
        "total": len(entries),
        "new_today": new_today,
        "codes": [e["code"] for e in entries],
    }


@router.delete("/{code}")
async def remove_from_watchlist(code: str):
    """从监控列表中移除股票"""
    entries = _parse_watchlist()
    entries = [e for e in entries if e["code"] != code]
    try:
        remaining = write_watchlist(entries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e}")
    return {"message": f"已移除 {code}", "remaining": remaining}


@router.get("/{code}/detail")
async def get_stock_detail(code: str):
    """获取单只股票的详细数据（K线走势 + 回撤序列 + 均线 + 成交量）

    返回 ECharts 可直接消费的数据格式。
    """
    entries = _parse_watchlist()
    stock = next((e for e in entries if e["code"] == code), None)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"监控列表中未找到 {code}")

    # ---- 1. 基础信息 ----
    ref_price = stock["ref_price"]
    zt_date = stock["zt_date"]

    # ---- 2. 拉取历史K线（60日）----
    hist_df = None
    current_price = None
    current_change_pct = None

    # 尝试 Tushare（主数据源）
    try:
        from ..agents.layer1_data_collector.sources.tushare_source import fetch_historical as ts_hist, is_available
        if is_available():
            ts_result = ts_hist([code], days=60)
            if code in ts_result:
                hist_df = ts_result[code]
    except Exception:
        pass

    # 降级到东方财富
    if hist_df is None:
        try:
            from ..agents.layer1_data_collector.sources.historical_kline import fetch_historical
            hist_df = fetch_historical(code, 60)
        except Exception:
            pass

    # 拉取实时行情
    try:
        from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes
        quotes = fetch_stock_quotes([code])
        if not quotes.empty:
            q = quotes.iloc[0]
            current_price = float(q.get("最新价", 0)) if q.get("最新价") else None
            current_change_pct = float(q.get("涨跌幅", 0)) if q.get("涨跌幅") else None
    except Exception:
        pass

    # 降级：从K线最后一条取收盘价
    if current_price is None and hist_df is not None and not hist_df.empty:
        close_col = "收盘" if "收盘" in hist_df.columns else "close"
        try:
            current_price = float(hist_df.iloc[-1][close_col])
        except Exception:
            pass

    # ---- 3. 构建图表数据序列 ----
    dates = []
    closes = []
    volumes = []
    changes = []
    drawdowns = []
    ma5 = []
    ma10 = []
    ma20 = []
    ma60 = []

    if hist_df is not None and not hist_df.empty:
        date_col = "日期" if "日期" in hist_df.columns else "date"
        close_col = "收盘" if "收盘" in hist_df.columns else "close"
        vol_col = "成交量" if "成交量" in hist_df.columns else "vol"
        pct_col = "涨跌幅" if "涨跌幅" in hist_df.columns else "pct_chg"

        if date_col in hist_df.columns and close_col in hist_df.columns:
            data_rows = hist_df[[date_col, close_col]].copy()
            if vol_col in hist_df.columns:
                data_rows[vol_col] = hist_df[vol_col]
            if pct_col in hist_df.columns:
                data_rows[pct_col] = hist_df[pct_col]

            for _, row in data_rows.iterrows():
                day = str(row[date_col])[:10]
                close = float(row[close_col]) if pd.notna(row[close_col]) else 0
                vol = float(row.get(vol_col, 0)) if vol_col in row.index and pd.notna(row.get(vol_col, 0)) else 0
                chg = float(row.get(pct_col, 0)) if pct_col in row.index and pd.notna(row.get(pct_col, 0)) else 0

                if close <= 0:
                    continue

                dates.append(day)
                closes.append(round(close, 2))
                volumes.append(int(vol))
                changes.append(round(chg, 2))
                drawdowns.append(round((close - ref_price) / ref_price * 100, 2))

            # 计算均线
            if len(closes) >= 5:
                for i in range(len(closes)):
                    ma5.append(round(sum(closes[max(0, i - 4):i + 1]) / min(i + 1, 5), 2))
            if len(closes) >= 10:
                for i in range(len(closes)):
                    ma10.append(round(sum(closes[max(0, i - 9):i + 1]) / min(i + 1, 10), 2))
            if len(closes) >= 20:
                for i in range(len(closes)):
                    ma20.append(round(sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20), 2))
            if len(closes) >= 60:
                for i in range(len(closes)):
                    ma60.append(round(sum(closes[max(0, i - 59):i + 1]) / min(i + 1, 60), 2))

    # 回撤分布统计
    drawdown_distribution = {}
    if drawdowns:
        buckets = [("≤-10%", -100, -10), ("-10%~-5%", -10, -5), ("-5%~-3%", -5, -3),
                   ("-3%~0%", -3, 0), ("0%~+5%", 0, 5), ("+5%+", 5, 1000)]
        for label, lo, hi in buckets:
            count = sum(1 for d in drawdowns if lo <= d < hi)
            if count > 0:
                drawdown_distribution[label] = count

    # 添加今日价格到序列
    if current_price and current_price > 0:
        from datetime import datetime, timezone, timedelta
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        if not dates or dates[-1] < today_str:
            dates.append(today_str)
            closes.append(round(current_price, 2))
            changes.append(round(current_change_pct or 0, 2))
            volumes.append(0)
            dd = round((current_price - ref_price) / ref_price * 100, 2)
            drawdowns.append(dd)

    return {
        "code": code,
        "name": stock["name"],
        "zt_date": zt_date,
        "ref_price": ref_price,
        "current_price": current_price,
        "drop_pct": round((current_price - ref_price) / ref_price * 100, 2) if current_price and current_price > 0 else None,
        "seal_time": stock.get("seal_time", "0"),
        "consecutive": stock.get("consecutive", "0"),
        "added_date": stock.get("added_date", stock.get("zt_date", "")),
        # 图表数据
        "chart_data": {
            "dates": dates,
            "closes": closes,
            "volumes": volumes,
            "changes": changes,
            "drawdowns": drawdowns,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "drawdown_distribution": drawdown_distribution,
        },
    }
