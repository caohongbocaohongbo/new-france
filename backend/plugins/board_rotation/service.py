"""05 板块轮动编排服务。"""
import logging
from datetime import date, datetime

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, float_or, json_safe, read_snapshot, write_snapshot,
)
from backend.services.trading_calendar import is_trading_day, prev_trading_day

from .indicators import board_score_from_z, classify_stage, mainline_confirm, zscore

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "board_heat"


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(BEIJING_TZ).date()


def count_zt_by_industry(zt_pool) -> dict:
    """按涨停池所属行业统计涨停家数与最高连板。"""
    if zt_pool is None:
        return {}
    if hasattr(zt_pool, "to_dict"):
        raw = zt_pool.to_dict("records")
    else:
        raw = list(zt_pool)
    agg = {}
    for r in raw or []:
        industry = str(r.get("所属行业") or r.get("industry") or "").strip()
        if not industry:
            continue
        height = int(float_or(r.get("连板数") or r.get("height"), 1) or 1)
        entry = agg.setdefault(industry, {"zt_count": 0, "max_height": 0})
        entry["zt_count"] += 1
        entry["max_height"] = max(entry["max_height"], max(1, height))
    return agg


def build_board_scores(boards: list) -> list:
    """boards: [{name, change_pct, net_inflow, zt_count, max_height}] -> 打分排序。"""
    if not boards:
        return []
    z_change = zscore([b.get("change_pct") for b in boards])
    z_inflow = zscore([b.get("net_inflow") for b in boards])
    for i, b in enumerate(boards):
        b["score"] = board_score_from_z(z_change[i], z_inflow[i], b.get("zt_count"), b.get("max_height"), b.get("zt_ratio"))
    boards.sort(key=lambda b: b["score"], reverse=True)
    return boards


def fetch_industry_boards(target_date: date) -> list:
    """akShare 行业板块行情（涨幅/主力净流入），失败返回 []。"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
    except Exception as exc:  # noqa: BLE001
        logger.warning("行业板块拉取失败: %s", exc)
        return []
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "board_code": str(r.get("板块代码", "")),
            "name": str(r.get("板块名称", "")),
            "board_type": "industry",
            "change_pct": float_or(r.get("涨跌幅")),
            "net_inflow": float_or(r.get("主力净流入")),
        })
    return rows


def fetch_board_constituents(board_code: str) -> list:
    """akShare 板块成分股 -> [code]，失败返回 []。"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_cons_em(symbol=str(board_code))
    except Exception as exc:  # noqa: BLE001
        logger.debug("板块成分股拉取失败 %s: %s", board_code, exc)
        return []
    if df is None or df.empty:
        return []
    col = "代码" if "代码" in df.columns else ("code" if "code" in df.columns else None)
    if not col:
        return []
    return [str(c).zfill(6) for c in df[col].tolist() if str(c).strip()]


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def run_board_once(target_date=None, force: bool = False, constituents_fetcher=None) -> dict:
    """盘后执行一轮板块热度计算。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    try:
        from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
        zt_pool = fetch_zt_pool(target)
    except Exception:  # noqa: BLE001
        zt_pool = None
    zt_by_industry = count_zt_by_industry(zt_pool)
    boards = fetch_industry_boards(target)
    # 合并涨停家数/最高连板（东财行业名对齐，容错按前缀）
    for b in boards:
        zt_info = zt_by_industry.get(b["name"]) or _match_industry(b["name"], zt_by_industry)
        b["zt_count"] = (zt_info or {}).get("zt_count", 0)
        b["max_height"] = (zt_info or {}).get("max_height", 0)
        b["zt_ratio"] = round(b["zt_count"] / max(1, len(zt_by_industry)), 4) if b["zt_count"] else 0.0
    boards = build_board_scores(boards)
    prev_d = prev_trading_day(target)
    prev_zt = {}
    if prev_d:
        df = db_query("SELECT board_name, zt_count FROM board_daily WHERE date = :d", {"d": prev_d.isoformat()})
        prev_zt = {str(r.board_name): int(r.zt_count or 0) for r in df.itertuples()} if not df.empty else {}
    for b in boards[:50]:
        b["stage"] = classify_stage(b["score"], prev_score=None, zt_count=b["zt_count"], prev_zt_count=prev_zt.get(b["name"], 0))
        b["mainline"] = mainline_confirm(b["zt_count"], prev_zt.get(b["name"], 0), b.get("score", 0) >= 0.5, True)
    # 15/05 接口约定：每个板块条目写 codes 成分股列表，供 15 方案 D3 匹配
    for b in boards[:50]:
        b["codes"] = (constituents_fetcher or fetch_board_constituents)(b.get("board_code", ""))
    payload = {"status": "completed" if boards else "no_data", "date": target.isoformat(), "now": now.isoformat(), "count": len(boards), "items": boards[:50]}
    write_snapshot(SNAPSHOT_NAME, payload)
    db_append("board_daily", [
        {"date": target.isoformat(), "board_code": b.get("board_code", ""), "board_name": b["name"],
         "board_type": b.get("board_type", "industry"), "change_pct": b.get("change_pct"),
         "net_inflow": b.get("net_inflow"), "zt_count": b.get("zt_count", 0),
         "max_height": b.get("max_height", 0), "zt_ratio": b.get("zt_ratio", 0),
         "score": b.get("score"), "stage": b.get("stage")}
        for b in boards[:50]
    ])
    # 写成分股关系（供 15 方案 db 路径兜底）
    db_append("board_stock_daily", [
        {"date": target.isoformat(), "board_code": b.get("board_code", ""), "code": c,
         "name": "", "is_zt": 0, "is_leader": 0, "height": 0}
        for b in boards[:50]
        for c in (b.get("codes") or [])
    ])
    return payload


def _match_industry(name, agg):
    for key in agg:
        if key and (key in name or name in key):
            return agg[key]
    return None
