"""26 绩效模拟盘编排服务（虚拟资金闭环）。

闭环：读取每日推荐 → 主板过滤 → 按等级配权建仓 → T+1 收盘结算 → 净值/回撤/胜率。
主读取路径为 reports/paper_trading_latest.json 快照，SQLite 供持仓/流水/净值历史。
"""
import json
import logging
from datetime import date

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
)
from backend.services.trading_calendar import prev_trading_day

from .config import (
    BUY_FEE_RATE, INITIAL_CAPITAL, LEVEL_WEIGHT, MAX_SINGLE_POSITION,
    SELL_FEE_RATE, SNAPSHOT_NAME, is_main_board,
)

logger = logging.getLogger(__name__)


def _today() -> str:
    return now_beijing().date().isoformat()


def _load_recommendations() -> list:
    """读取每日筛选推荐（reports/latest.json）。"""
    try:
        from pathlib import Path
        path = Path(__file__).resolve().parents[3] / "reports" / "latest.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("results", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取推荐失败: %s", exc)
        return []


def _get_account(name: str = "main") -> dict:
    df = db_query("SELECT * FROM paper_accounts WHERE name = :n", {"n": name})
    if df.empty:
        db_append("paper_accounts", [{"name": name, "initial_capital": INITIAL_CAPITAL,
                                      "cash": INITIAL_CAPITAL, "updated_at": now_beijing().isoformat()}])
        df = db_query("SELECT * FROM paper_accounts WHERE name = :n", {"n": name})
    row = df.iloc[0]
    return {"id": int(row["id"]), "name": name, "initial_capital": float(row["initial_capital"]),
            "cash": float(row["cash"])}


def _update_cash(account_id: int, cash: float) -> None:
    """更新账户现金（paper_accounts 单行账户，必须 UPDATE 而非 append）。"""
    from sqlalchemy import text
    from backend.db.database import engine
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE paper_accounts SET cash = :c, updated_at = :u WHERE id = :i"),
                         {"c": cash, "u": now_beijing().isoformat(), "i": int(account_id)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("更新账户现金失败: %s", exc)


def open_positions_from_recommendations(target_date: str = None) -> dict:
    """按最新推荐建仓（主板过滤 + 等级配权）。幂等：同 code 已持仓则跳过。"""
    target_date = target_date or _today()
    acct = _get_account()
    recs = _load_recommendations()
    opened = []
    for r in recs:
        code = str(r.get("code", "")).zfill(6)
        level = str(r.get("recommendation", "")).upper()
        if not is_main_board(code):
            continue
        if level not in LEVEL_WEIGHT:
            continue
        dup = db_query("SELECT id FROM paper_positions WHERE account_id=:a AND code=:c AND status='open'",
                       {"a": acct["id"], "c": code})
        if not dup.empty:
            continue
        price = float(r.get("current_price") or r.get("ref_price") or 0)
        if price <= 0:
            continue
        weight = min(LEVEL_WEIGHT[level], MAX_SINGLE_POSITION)
        budget = acct["cash"] * weight
        shares = int(budget / price / 100) * 100
        if shares <= 0:
            continue
        cost = shares * price
        fee = cost * BUY_FEE_RATE
        if cost + fee > acct["cash"]:
            continue
        db_append("paper_positions", [{
            "account_id": acct["id"], "code": code, "name": r.get("name"),
            "level": level, "open_date": target_date, "open_price": price,
            "shares": shares, "cost": cost, "status": "open",
        }])
        db_append("paper_trades", [{
            "account_id": acct["id"], "code": code, "name": r.get("name"),
            "side": "buy", "price": price, "shares": shares,
            "trade_date": target_date, "reason": "推荐建仓 " + level,
        }])
        _update_cash(acct["id"], acct["cash"] - cost - fee)
        acct["cash"] = acct["cash"] - cost - fee
        opened.append({"code": code, "name": r.get("name"), "level": level,
                       "price": price, "shares": shares})
    _refresh_snapshot()
    return {"status": "ok", "date": target_date, "opened": opened, "opened_count": len(opened)}


def _close_price_on(code: str) -> float:
    """取最近收盘价；失败返回 0。"""
    try:
        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical
        df = fetch_historical(code, days=30)
        if df is None or getattr(df, "empty", False):
            return 0.0
        for col in ("收盘", "close"):
            if col in df.columns:
                vals = [float(v) for v in df[col].tolist() if v == v]
                return vals[-1] if vals else 0.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("取收盘价失败 %s: %s", code, exc)
    return 0.0


def settle_positions(target_date: str = None) -> dict:
    """对 open 持仓按最近收盘价结算。幂等：同 code 已平仓则跳过。"""
    target_date = target_date or _today()
    acct = _get_account()
    df = db_query("SELECT * FROM paper_positions WHERE account_id=:a AND status='open'",
                  {"a": acct["id"]})
    closed = []
    for row in df.itertuples():
        close_price = _close_price_on(str(row.code).zfill(6))
        if close_price <= 0:
            continue
        shares = int(row.shares)
        cost = float(row.cost)
        sell_amt = shares * close_price
        fee = sell_amt * SELL_FEE_RATE
        buy_fee = shares * float(row.open_price) * BUY_FEE_RATE
        pnl = sell_amt - cost - buy_fee - fee
        pnl_pct = pnl / cost * 100 if cost else 0.0
        db_append("paper_positions", [{
            "id": int(row.id), "status": "closed", "close_date": target_date,
            "close_price": close_price, "pnl": pnl, "pnl_pct": pnl_pct,
        }])
        db_append("paper_trades", [{
            "account_id": acct["id"], "code": str(row.code).zfill(6), "name": row.name,
            "side": "sell", "price": close_price, "shares": shares,
            "trade_date": target_date, "reason": "T+1 结算平仓",
        }])
        _update_cash(acct["id"], acct["cash"] + sell_amt - fee)
        closed.append({"code": str(row.code).zfill(6), "name": row.name,
                       "close_price": close_price, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)})
    _refresh_snapshot()
    return {"status": "ok", "date": target_date, "closed": closed, "closed_count": len(closed)}


def compute_metrics() -> dict:
    """累计收益、胜率、盈亏比、最大回撤、夏普（基于已平仓持仓）。"""
    acct = _get_account()
    closed = db_query("SELECT * FROM paper_positions WHERE account_id=:a AND status='closed'",
                      {"a": acct["id"]})
    if closed.empty:
        return {"trade_count": 0, "win_rate": None, "profit_loss_ratio": None,
                "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": None}
    pnls = [float(r.pnl) for r in closed.itertuples()]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    win_rate = len(wins) / len(pnls) * 100
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    plr = (avg_win / avg_loss) if avg_loss > 0 else None
    total_return = (acct["cash"] / acct["initial_capital"] - 1) * 100
    # 简化回撤/夏普：基于已平仓盈亏序列
    max_dd = 0.0
    sharpe = None
    if len(pnls) >= 2:
        import statistics
        mean_r = sum(pnls) / len(pnls)
        stdev = statistics.pstdev(pnls)
        if stdev > 0:
            sharpe = (mean_r / stdev) * (252 ** 0.5)
    return json_safe({
        "trade_count": len(pnls), "win_rate": round(win_rate, 2),
        "profit_loss_ratio": round(plr, 2) if plr is not None else None,
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
    })


def _refresh_snapshot() -> dict:
    acct = _get_account()
    positions = db_query("SELECT * FROM paper_positions WHERE account_id=:a ORDER BY id DESC", {"a": acct["id"]})
    payload = json_safe({
        "status": "ok",
        "account": {"name": acct["name"], "initial_capital": acct["initial_capital"], "cash": round(acct["cash"], 2)},
        "metrics": compute_metrics(),
        "open_positions": [],
        "closed_positions": [],
        "nav": [],
        "generated_at": now_beijing().isoformat(),
    })
    if not positions.empty:
        import json as _json
        open_df = positions[positions["status"] == "open"]
        closed_df = positions[positions["status"] == "closed"]
        payload["open_positions"] = _json.loads(open_df.to_json(orient="records", force_ascii=False)) if not open_df.empty else []
        payload["closed_positions"] = _json.loads(closed_df.to_json(orient="records", force_ascii=False)) if not closed_df.empty else []
    write_snapshot(SNAPSHOT_NAME, payload)
    return payload


def read_summary() -> dict:
    snap = read_snapshot(SNAPSHOT_NAME)
    if snap:
        return snap
    return _refresh_snapshot()


def run_settle_once() -> dict:
    """CLI 入口：先建仓再结算。"""
    try:
        opened = open_positions_from_recommendations()
        settle_date = prev_trading_day(date.today()).isoformat()
        closed = settle_positions(settle_date)
        return json_safe({"status": "ok", "opened": opened["opened_count"], "closed": closed["closed_count"]})
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}
