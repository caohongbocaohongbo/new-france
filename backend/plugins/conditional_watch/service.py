"""26 条件盯盘编排服务。

规则存 SQLite（watch_rules），触发记录存 watch_alerts，快照 reports/conditional_watch_latest.json。
行情复用腾讯批量报价（fetch_tencent_quotes_for_codes）。
"""
import json
import logging
from datetime import datetime

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
)

from .config import SNAPSHOT_NAME
from .expr import build_values, evaluate

logger = logging.getLogger(__name__)


def list_rules() -> list:
    df = db_query("SELECT * FROM watch_rules ORDER BY id DESC")
    if df.empty:
        return []
    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))


def create_rule(code: str, condition: str, name: str = None, variables: dict = None,
                channels: list = None, product_type: str = "stock") -> dict:
    code = str(code).zfill(6)
    db_append("watch_rules", [{
        "code": code, "name": name or code, "product_type": product_type,
        "condition": condition, "variables_json": json.dumps(variables or {}, ensure_ascii=False),
        "channels_json": json.dumps(channels or ["openclaw"], ensure_ascii=False),
        "enabled": 1, "created_at": now_beijing().isoformat(),
    }])
    return {"status": "ok", "code": code, "condition": condition}


def delete_rule(rule_id: int) -> dict:
    db_delete = __import__("backend.plugins.common", fromlist=["db_delete"]).db_delete
    n = db_delete("watch_rules", {"id": int(rule_id)})
    return {"status": "ok", "deleted": n}


def toggle_rule(rule_id: int, enabled: bool) -> dict:
    from sqlalchemy import text
    from backend.db.database import engine
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE watch_rules SET enabled = :e WHERE id = :i"),
                         {"e": 1 if enabled else 0, "i": int(rule_id)})
        return {"status": "ok", "enabled": bool(enabled)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}


def _fetch_quotes(codes: list) -> dict:
    """腾讯批量报价，返回 {code: {最新价, 涨跌幅, 换手率, 成交量, 量比}}。"""
    if not codes:
        return {}
    try:
        from backend.agents.layer1_data_collector.sources.eastmoney_quote import fetch_tencent_quotes_for_codes
        df = fetch_tencent_quotes_for_codes(codes)
        if df is None or getattr(df, "empty", False):
            return {}
        out = {}
        for row in df.to_dict("records"):
            code = str(row.get("代码", "")).zfill(6)
            out[code] = row
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("盯盘行情拉取失败: %s", exc)
        return {}


def check_rules() -> dict:
    """对全部启用规则做一次求值，命中则去重落库。"""
    rules = [r for r in list_rules() if int(r.get("enabled", 1)) == 1]
    if not rules:
        return {"status": "ok", "checked": 0, "triggered": 0, "alerts": []}
    codes = sorted({r["code"] for r in rules})
    quotes = _fetch_quotes(codes)
    triggered = []
    for r in rules:
        md = quotes.get(r["code"])
        if not md:
            continue
        variables = json.loads(r.get("variables_json") or "{}")
        values = build_values(md, variables)
        if evaluate(r.get("condition", ""), values):
            price = values.get("price")
            change = values.get("change_percent")
            dedup_key = f"{r['id']}:{r['code']}:{price is not None and round(price, 2)}"
            # 同价去重：同一规则同一价位只告警一次
            if r.get("last_trigger_key") == dedup_key:
                continue
            db_append("watch_alerts", [{
                "rule_id": r["id"], "code": r["code"], "name": r.get("name"),
                "condition": r.get("condition"),
                "triggered_at": now_beijing().isoformat(),
                "price": price, "change_pct": change,
                "message": f"盯盘触发 {r['name'] or r['code']}：{r.get('condition')}",
                "dedup_key": dedup_key + ":" + now_beijing().strftime("%H%M"), "notified": 1,
            }])
            from sqlalchemy import text
            from backend.db.database import engine
            try:
                with engine.begin() as conn:
                    conn.execute(text("UPDATE watch_rules SET last_trigger_key = :k WHERE id = :i"),
                                 {"k": dedup_key, "i": int(r["id"])})
            except Exception:  # noqa: BLE001
                pass
            triggered.append({"code": r["code"], "name": r.get("name"), "condition": r.get("condition"),
                              "price": price, "change_pct": change})
    _refresh_snapshot()
    return {"status": "ok", "checked": len(rules), "triggered": len(triggered), "alerts": triggered}


def list_alerts(days: int = 7) -> list:
    df = db_query("SELECT * FROM watch_alerts ORDER BY id DESC LIMIT :n", {"n": int(days) * 50})
    if df.empty:
        return []
    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))


def _refresh_snapshot() -> dict:
    payload = json_safe({
        "status": "ok",
        "rules": list_rules(),
        "alerts": list_alerts(7),
        "generated_at": now_beijing().isoformat(),
    })
    write_snapshot(SNAPSHOT_NAME, payload)
    return payload


def read_latest() -> dict:
    snap = read_snapshot(SNAPSHOT_NAME)
    if snap:
        return snap
    return _refresh_snapshot()
