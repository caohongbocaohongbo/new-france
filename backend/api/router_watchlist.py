"""监控列表 API"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import logging
import pandas as pd

from ..services.watchlist_store import parse_watchlist, write_watchlist
from ..services.runtime_config import get_effective_config

router = APIRouter()
logger = logging.getLogger(__name__)

QUOTE_SORT_FIELDS = {"drop_pct", "turnover", "vol_ratio", "pe"}
LOCAL_SORT_FIELDS = {"zt_count"}
SORTABLE_FIELDS = QUOTE_SORT_FIELDS | LOCAL_SORT_FIELDS


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
    try:
        tracking_days = int(get_effective_config()["config"]["strategy"].get("trackingDays", 30))
    except Exception:
        tracking_days = 30

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
        entry["status"] = "expired" if days_since > tracking_days else "active"

        result.append(entry)

    return result


def _parse_with_status(entries: list) -> list:
    """仅解析监控列表并标注状态（不含行情查询，速度快）"""
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    today = datetime.now(BEIJING_TZ).date()
    try:
        tracking_days = int(get_effective_config()["config"]["strategy"].get("trackingDays", 30))
    except Exception:
        tracking_days = 30

    result = []
    for e in entries:
        try:
            zt_date = datetime.strptime(e["zt_date"], "%Y-%m-%d").date()
        except Exception:
            zt_date = today
        days_since = (today - zt_date).days
        result.append({
            **e,
            "status": "expired" if days_since > tracking_days else "active",
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


def _has_sort_values(entries: list, sort_by: str) -> bool:
    return any(_as_sort_number(entry.get(sort_by)) is not None for entry in entries)


def _finite_series(values: list) -> bool:
    return any(_as_sort_number(value) is not None for value in values)


def _normalize_date_key(value) -> str:
    """统一日期 key，支持 20260527 / 2026-05-27 两类真实源格式。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) >= 10 and "-" in text:
        text = text[:10]
    return text.replace("-", "")


def _safe_metric_float(value, digits: int = 2, positive_only: bool = False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    if positive_only and number <= 0:
        return None
    return round(number, digits)


def _align_metric_series_by_date(dates: list, df: pd.DataFrame, date_col: str, value_col: str) -> list:
    """按交易日对齐真实指标序列；没有对应日期的数据保持 None，不做填充。"""
    if df is None or df.empty or date_col not in df.columns or value_col not in df.columns:
        return [None for _ in dates]

    value_map = {}
    for _, row in df.iterrows():
        key = _normalize_date_key(row.get(date_col))
        if not key:
            continue
        metric = _safe_metric_float(row.get(value_col))
        if metric is not None:
            value_map[key] = metric

    return [value_map.get(_normalize_date_key(day)) for day in dates]


def _fill_latest_metric_value(series: list, value) -> list:
    """仅把实时行情中的真实单点放到最新交易日，不复制成历史趋势。"""
    filled = list(series or [])
    metric = _safe_metric_float(value)
    if metric is not None and filled:
        filled[-1] = metric
    return filled


@router.get("")
def get_watchlist(status: Optional[str] = Query(None),
                  search: Optional[str] = Query(None),
                  sort_by: Optional[str] = Query(None),
                  sort_order: str = Query("asc"),
                  page: int = Query(1, ge=1),
                  size: int = Query(15, ge=1, le=500)):
    """获取监控列表（含实时行情补全）

    - 普通展示：先筛选/分页，再补全当前页实时行情，避免全量行情请求阻塞页面
    - 表头排序：先补全过滤后的全集行情，再排序分页
    - size > 50: 仅标注状态不补全行情（用于全量统计）
    """
    entries = _parse_with_status(_parse_watchlist())

    if search:
        entries = [e for e in entries
                   if search.lower() in e["code"] or search.lower() in e["name"]]

    if status:
        entries = [e for e in entries if e.get("status") == status]

    total = len(entries)
    if size > 50:
        items = entries[:size]
    elif sort_by in QUOTE_SORT_FIELDS:
        enriched = _enrich_with_quotes(entries)
        if not _has_sort_values(enriched, sort_by):
            raise HTTPException(status_code=503, detail=f"实时行情暂不可用，无法按 {sort_by} 排序，请稍后重试")
        sorted_entries = _sort_watchlist(enriched, sort_by, sort_order)
        start = (page - 1) * size
        items = sorted_entries[start:start + size]
    elif sort_by in LOCAL_SORT_FIELDS:
        sorted_entries = _sort_watchlist(entries, sort_by, sort_order)
        start = (page - 1) * size
        page_entries = sorted_entries[start:start + size]
        items = _enrich_with_quotes(page_entries)
    else:
        start = (page - 1) * size
        page_entries = entries[start:start + size]
        items = _enrich_with_quotes(page_entries)

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/stats")
def get_watchlist_stats():
    """监控列表统计"""
    entries = _parse_with_status(_parse_watchlist())
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    today = datetime.now(BEIJING_TZ)
    new_today = sum(1 for e in entries if e["zt_date"] == today.strftime("%Y-%m-%d"))
    status_counts = {
        "active": sum(1 for e in entries if e.get("status") == "active"),
        "recommended": sum(1 for e in entries if e.get("status") == "recommended"),
        "expired": sum(1 for e in entries if e.get("status") == "expired"),
    }
    return {
        "total": len(entries),
        "new_today": new_today,
        "status_counts": status_counts,
        "codes": [e["code"] for e in entries],
    }


@router.delete("/{code}")
def remove_from_watchlist(code: str):
    """从监控列表中移除股票"""
    entries = _parse_watchlist()
    entries = [e for e in entries if e["code"] != code]
    try:
        remaining = write_watchlist(entries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e}")
    return {"message": f"已移除 {code}", "remaining": remaining}


@router.get("/{code}/detail")
def get_stock_detail(code: str):
    """获取单只股票的详细数据（K线走势 + 回撤序列 + 均线 + 成交量）

    返回 ECharts 可直接消费的数据格式。
    """
    entries = _parse_watchlist()
    stock = next((e for e in entries if e["code"] == code), None)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"监控列表中未找到 {code}")

    # ---- 1. 基础信息 ----
    ref_price = stock["ref_price"]
    if ref_price <= 0:
        raise HTTPException(status_code=400, detail=f"参考价异常({ref_price})，请检查监控列表数据")
    zt_date = stock["zt_date"]

    # ---- 2. 拉取历史K线（60日）----
    hist_df = None
    kline_source = None
    current_price = None
    current_change_pct = None
    quote_source = None
    quote_row = None
    basic_source = None

    # 尝试 Tushare（主数据源）
    try:
        from ..agents.layer1_data_collector.sources.tushare_source import fetch_historical as ts_hist, is_available
        if is_available():
            ts_result = ts_hist([code], days=60)
            if code in ts_result:
                hist_df = ts_result[code]
                kline_source = "Tushare Pro daily"
    except Exception as e:
        logger.warning(f"  Tushare历史K线失败: {e}")

    # 降级到东方财富
    if hist_df is None:
        try:
            from ..agents.layer1_data_collector.sources.historical_kline import fetch_historical_with_source
            hist_result = fetch_historical_with_source(code, 60)
            if hist_result is not None:
                hist_df, kline_source = hist_result
        except Exception as e:
            logger.warning(f"  东方财富历史K线失败: {e}")

    # 拉取实时行情
    try:
        from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes
        quotes = fetch_stock_quotes([code])
        if not quotes.empty:
            q = quotes.iloc[0]
            quote_row = q
            current_price = float(q.get("最新价", 0)) if q.get("最新价") else None
            current_change_pct = float(q.get("涨跌幅", 0)) if q.get("涨跌幅") else None
            quote_source = "东方财富实时行情"
    except Exception as e:
        logger.warning(f"  实时行情获取失败: {e}")

    # 降级：从K线最后一条取收盘价
    if current_price is None and hist_df is not None and not hist_df.empty:
        close_col = "收盘" if "收盘" in hist_df.columns else "close"
        try:
            current_price = float(hist_df.iloc[-1][close_col])
            quote_source = kline_source
        except Exception as e:
            logger.warning(f"  K线收盘价提取失败: {e}")

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
    turnover_series = []
    vol_ratio_series = []
    pe_series = []
    seal_time_series = []
    turnover_source = None
    vol_ratio_source = None
    pe_source = None

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
            if "换手率" in hist_df.columns:
                data_rows["换手率"] = hist_df["换手率"]

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
                turnover_series.append(None)
                vol_ratio_series.append(None)
                pe_series.append(None)
                seal_time_series.append(None)

                if "换手率" in row.index:
                    try:
                        turnover_val = float(row.get("换手率"))
                        if pd.notna(turnover_val) and turnover_val > 0:
                            turnover_series[-1] = round(turnover_val, 2)
                    except Exception:
                        pass

            if _finite_series(turnover_series):
                turnover_source = f"{kline_source} 换手率字段"

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

    # ---- 3.1 补充真实日频指标（仅 Tushare 可用时返回，失败保持空序列）----
    try:
        from ..agents.layer1_data_collector.sources.tushare_source import fetch_daily_basic_history
        basic_df = fetch_daily_basic_history(code, 60)
    except Exception as e:
        logger.warning(f"  历史基础指标失败: {e}")
        basic_df = None

    if basic_df is not None and not basic_df.empty and dates:
        basic_source = "Tushare Pro daily_basic"
        basic_map = {str(row["日期"])[:10].replace("-", ""): row for _, row in basic_df.iterrows()}
        for index, day in enumerate(dates):
            key = str(day).replace("-", "")
            row = basic_map.get(key)
            if row is None:
                continue

            def _safe_float(col):
                val = row.get(col)
                try:
                    return round(float(val), 2) if pd.notna(val) else None
                except Exception:
                    return None

            turnover_val = _safe_float("换手率")
            if turnover_val is not None:
                turnover_series[index] = turnover_val
            vol_ratio_series[index] = _safe_float("量比")
            pe_val = _safe_float("PE_TTM")
            if pe_val is None:
                pe_val = _safe_float("PE")
            pe_series[index] = pe_val

        if _finite_series(turnover_series):
            turnover_source = basic_source
        if _finite_series(vol_ratio_series):
            vol_ratio_source = basic_source
        if _finite_series(pe_series):
            pe_source = basic_source

    if dates and not _finite_series(pe_series):
        try:
            import akshare as ak
            pe_df = ak.stock_zh_valuation_baidu(
                symbol=code,
                indicator="市盈率(TTM)",
                period="近一年",
            )
        except Exception as e:
            logger.warning(f"  百度股市通PE历史失败: {e}")
            pe_df = None

        baidu_pe_series = _align_metric_series_by_date(dates, pe_df, "date", "value")
        if _finite_series(baidu_pe_series):
            pe_series = baidu_pe_series
            pe_source = "百度股市通估值数据(市盈率TTM)"

    if quote_row is not None and dates:
        if not _finite_series(turnover_series):
            turnover_series = _fill_latest_metric_value(turnover_series, quote_row.get("换手率"))
            if _finite_series(turnover_series):
                turnover_source = "东方财富实时行情(最新交易日单点)"

        if not _finite_series(vol_ratio_series):
            vol_ratio_series = _fill_latest_metric_value(vol_ratio_series, quote_row.get("量比"))
            if _finite_series(vol_ratio_series):
                vol_ratio_source = "东方财富实时行情(最新交易日单点)"

        if not _finite_series(pe_series):
            pe_series = _fill_latest_metric_value(pe_series, quote_row.get("市盈率"))
            if _finite_series(pe_series):
                pe_source = "东方财富实时行情(最新交易日单点)"

    has_historical_kline = bool(dates)

    # 回撤分布统计
    drawdown_distribution = {}
    if drawdowns:
        buckets = [("≤-10%", -100, -10), ("-10%~-5%", -10, -5), ("-5%~-3%", -5, -3),
                   ("-3%~0%", -3, 0), ("0%~+5%", 0, 5), ("+5%+", 5, 1000)]
        for label, lo, hi in buckets:
            count = sum(1 for d in drawdowns if lo <= d < hi)
            if count > 0:
                drawdown_distribution[label] = count

    added_date = stock.get("added_date", stock.get("zt_date", ""))
    stored_zt_count = stock.get("zt_count", "0")
    limit_events = []
    limit_event_sources = set()
    for index, (day, change) in enumerate(zip(dates, changes)):
        if added_date and day < added_date:
            continue
        try:
            pct = float(change)
        except (TypeError, ValueError):
            continue
        if pct >= 9.8:
            seal_for_day = stock.get("seal_time", "0") if day == zt_date else "0"
            if seal_for_day not in {"0", 0, None, ""} and index < len(seal_time_series):
                seal_time_series[index] = int(seal_for_day)
            limit_events.append({
                "date": day,
                "seal_time": seal_for_day,
                "label": f"{stock.get('consecutive', '1') or '1'}板" if day == zt_date and str(stock.get("consecutive", "1")) not in {"0", ""} else "涨停",
                "source": "historical_kline_change_pct",
                "change_pct": round(pct, 2),
            })
            limit_event_sources.add("historical_kline_change_pct")

    if zt_date and not any(event.get("date") == zt_date for event in limit_events):
        limit_events.append({
            "date": zt_date,
            "seal_time": stock.get("seal_time", "0"),
            "label": f"{stock.get('consecutive', '1') or '1'}板" if str(stock.get("consecutive", "1")) not in {"0", ""} else "观察",
            "source": "watchlist_record",
        })
        limit_event_sources.add("watchlist_record")

    stored_seal = stock.get("seal_time", "0")
    if stored_seal not in {"0", 0, None, ""} and zt_date in dates:
        seal_index = dates.index(zt_date)
        try:
            seal_time_series[seal_index] = int(stored_seal)
        except (TypeError, ValueError):
            pass

    if "historical_kline_change_pct" in limit_event_sources and "watchlist_record" in limit_event_sources:
        limit_events_source = "历史K线涨跌幅>=9.8% + data/france.md 监控记录"
        limit_events_note = "涨停日期由历史K线涨跌幅识别，监控记录补充封板时间"
    elif "historical_kline_change_pct" in limit_event_sources:
        limit_events_source = "历史K线涨跌幅>=9.8%"
        limit_events_note = "涨停日期由历史K线涨跌幅识别"
    elif "watchlist_record" in limit_event_sources:
        limit_events_source = "data/france.md 监控记录"
        limit_events_note = "历史K线不可用或未识别到涨停日时，仅展示监控列表中的可查记录"
    else:
        limit_events_source = None
        limit_events_note = "接口未返回可查涨停日期序列"

    follow_limit_up_count = len(limit_events)
    try:
        stored_count_int = int(stored_zt_count)
        follow_limit_up_count = max(follow_limit_up_count, stored_count_int)
    except (TypeError, ValueError):
        pass

    data_sources = {
        "kline": {
            "source": kline_source,
            "available": has_historical_kline,
            "note": "价格、成交量、涨跌幅、均线、回撤分布均由历史K线计算" if has_historical_kline else "历史K线数据源不可用；实时行情仅用于顶部现价，不回填为趋势图",
        },
        "quote": {
            "source": quote_source,
            "available": current_price is not None,
            "note": "现价、实时回撤来自东方财富实时行情" if quote_source == "东方财富实时行情" else (
                "东方财富实时行情暂不可用，现价使用历史K线最后收盘价降级" if current_price is not None else "实时行情接口暂不可用，且K线无可用收盘价"
            ),
        },
        "turnover": {
            "source": turnover_source,
            "available": _finite_series(turnover_series),
            "note": "换手率序列来自真实行情字段；缺失日期保持空值" if _finite_series(turnover_series) else "当前可用数据源未返回真实换手率序列，未使用推算值",
        },
        "vol_ratio": {
            "source": vol_ratio_source,
            "available": _finite_series(vol_ratio_series),
            "note": "量比来自真实数据源；若为实时行情则仅展示最新交易日单点" if _finite_series(vol_ratio_series) else "当前可用数据源未返回真实量比序列，未使用推算值",
        },
        "pe": {
            "source": pe_source,
            "available": _finite_series(pe_series),
            "note": "PE序列来自真实估值接口；缺失日期保持空值" if _finite_series(pe_series) else "当前可用数据源未返回真实PE序列，未回填假数据",
        },
        "seal_time": {
            "source": "data/france.md 监控记录 + 东方财富涨停池封板时间",
            "available": _finite_series(seal_time_series),
            "note": "仅记录监控列表中可查到的涨停日封板时间；非涨停日不生成封板时间",
        },
        "limit_events": {
            "source": limit_events_source,
            "available": bool(limit_events),
            "note": limit_events_note,
        },
    }

    return {
        "code": code,
        "name": stock["name"],
        "zt_date": zt_date,
        "ref_price": ref_price,
        "current_price": current_price,
        "drop_pct": round((current_price - ref_price) / ref_price * 100, 2) if current_price and current_price > 0 else None,
        "seal_time": stock.get("seal_time", "0"),
        "consecutive": stock.get("consecutive", "0"),
        "break_count": stock.get("break_count", "0"),
        "zt_count": stored_zt_count,
        "follow_limit_up_count": follow_limit_up_count,
        "added_date": added_date,
        "data_sources": data_sources,
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
            "turnover": turnover_series,
            "vol_ratio": vol_ratio_series,
            "pe": pe_series,
            "seal_times": seal_time_series,
            "limit_events": limit_events,
            "drawdown_distribution": drawdown_distribution,
        },
    }
