"""国家队持仓服务：刷新、入库、摘要和 API 查询。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import desc

from ..agents.layer1_data_collector.sources import national_team as source
from ..db.database import SessionLocal
from ..db.models import (
    NationalTeamChange,
    NationalTeamEvent,
    NationalTeamHolding,
    NationalTeamSnapshot,
)

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))
CHANGE_TYPES = ("new", "increase", "decrease", "unchanged", "exit")
LATEST_SNAPSHOT_WINDOW = timedelta(minutes=5)


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _identity(item: Dict[str, Any]) -> tuple:
    return (
        item.get("stock_code"),
        item.get("shareholder_name"),
        item.get("holding_type"),
    )


def select_latest_current_holdings(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest = {}
    for item in items:
        key = _identity(item)
        current_sort = (item.get("report_period") or "", item.get("notice_date") or "")
        existing = latest.get(key)
        if existing is None:
            latest[key] = item
            continue
        existing_sort = (existing.get("report_period") or "", existing.get("notice_date") or "")
        if current_sort > existing_sort:
            latest[key] = item
    return list(latest.values())


def _capital_flow_identity(item: Dict[str, Any]) -> tuple:
    return (
        item.get("stock_code"),
        item.get("shareholder_name"),
    )


def select_latest_capital_flow_holdings(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest = {}
    for item in items:
        key = _capital_flow_identity(item)
        current_sort = (
            item.get("report_period") or "",
            item.get("notice_date") or "",
            1 if item.get("holding_type") == "top10" else 0,
        )
        existing = latest.get(key)
        if existing is None:
            latest[key] = item
            continue
        existing_sort = (
            existing.get("report_period") or "",
            existing.get("notice_date") or "",
            1 if existing.get("holding_type") == "top10" else 0,
        )
        if current_sort > existing_sort:
            latest[key] = item
    return list(latest.values())


def _capital_flow_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    shares_change = item.get("shares_change")
    shares = item.get("shares")
    market_value = item.get("market_value")
    if shares_change in (None, 0) or not shares or shares <= 0 or not market_value or market_value <= 0:
        return None
    direction = "buy" if shares_change > 0 else "sell"
    unit_value = market_value / shares
    amount = abs(shares_change) * unit_value
    if amount <= 0:
        return None
    return {
        "entity_key": item.get("entity_key"),
        "entity_name": item.get("entity_name"),
        "stock_code": item.get("stock_code"),
        "stock_name": item.get("stock_name"),
        "shareholder_name": item.get("shareholder_name"),
        "holding_type": item.get("holding_type"),
        "holding_type_name": item.get("holding_type_name"),
        "report_period": item.get("report_period"),
        "notice_date": item.get("notice_date"),
        "shares": shares,
        "shares_change": shares_change,
        "change_name": item.get("change_name"),
        "market_value": market_value,
        "change_amount": amount,
        "direction": direction,
        "source": item.get("source") or source.SOURCE_NAME,
        "source_url": item.get("source_url") or source.SOURCE_URL,
        "fetched_at": item.get("fetched_at"),
    }


def _holding_value_items(rows: Iterable[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    latest_rows = select_latest_capital_flow_holdings(rows)
    by_stock: Dict[str, Dict[str, Any]] = {}
    for item in latest_rows:
        market_value = item.get("market_value")
        if not market_value or market_value <= 0:
            continue
        code = item.get("stock_code") or ""
        if not code:
            continue
        row = by_stock.setdefault(code, {
            "stock_code": code,
            "stock_name": item.get("stock_name"),
            "market_value": 0.0,
            "shares": 0.0,
            "holder_count": 0,
            "shareholder_names": [],
            "holding_type_name": item.get("holding_type_name"),
            "report_period": item.get("report_period"),
            "notice_date": item.get("notice_date"),
            "source": item.get("source") or source.SOURCE_NAME,
            "source_url": item.get("source_url") or source.SOURCE_URL,
            "fetched_at": item.get("fetched_at"),
        })
        row["market_value"] += market_value
        row["shares"] += item.get("shares") or 0.0
        row["holder_count"] += 1
        holder_name = item.get("shareholder_name")
        if holder_name and holder_name not in row["shareholder_names"]:
            row["shareholder_names"].append(holder_name)
        current_sort = (item.get("report_period") or "", item.get("notice_date") or "")
        existing_sort = (row.get("report_period") or "", row.get("notice_date") or "")
        if current_sort > existing_sort:
            row["report_period"] = item.get("report_period")
            row["notice_date"] = item.get("notice_date")
            row["fetched_at"] = item.get("fetched_at")
    return sorted(by_stock.values(), key=lambda item: item["market_value"], reverse=True)[:limit]


def detect_changes(entity_key: str,
                   previous: Iterable[Dict[str, Any]],
                   current: Iterable[Dict[str, Any]],
                   include_exits: bool = True) -> List[Dict[str, Any]]:
    previous_map = {_identity(item): item for item in previous if item.get("entity_key", entity_key) == entity_key}
    current_map = {
        _identity(item): item
        for item in select_latest_current_holdings(
            item for item in current if item.get("entity_key", entity_key) == entity_key
        )
    }
    changes = []
    for key, item in current_map.items():
        prev = previous_map.get(key)
        current_shares = item.get("shares")
        previous_shares = prev.get("shares") if prev else None
        if prev is None:
            change_type = "new"
        elif current_shares is not None and previous_shares is not None and current_shares > previous_shares:
            change_type = "increase"
        elif current_shares is not None and previous_shares is not None and current_shares < previous_shares:
            change_type = "decrease"
        else:
            change_type = "unchanged"
        changes.append({
            **item,
            "change_type": change_type,
            "previous_report_period": prev.get("report_period") if prev else None,
            "previous_shares": previous_shares,
            "shares_delta": (
                current_shares - previous_shares
                if current_shares is not None and previous_shares is not None else None
            ),
        })
    if include_exits:
        for key, prev in previous_map.items():
            if key in current_map:
                continue
            changes.append({
                **prev,
                "change_type": "exit",
                "previous_report_period": prev.get("report_period"),
                "previous_shares": prev.get("shares"),
                "shares_delta": None,
            })
    return changes


def _model_to_dict(row) -> Dict[str, Any]:
    data = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[col.name] = value
    return data


def _latest_previous_holdings(db, entity_key: str, current_periods: set[str]) -> List[Dict[str, Any]]:
    q = db.query(NationalTeamHolding).filter(NationalTeamHolding.entity_key == entity_key)
    if current_periods:
        q = q.filter(~NationalTeamHolding.report_period.in_(current_periods))
    rows = q.order_by(desc(NationalTeamHolding.report_period), desc(NationalTeamHolding.notice_date)).limit(1000).all()
    latest = {}
    for row in rows:
        item = _model_to_dict(row)
        key = _identity(item)
        if key not in latest:
            latest[key] = item
    return list(latest.values())


def _upsert_holding(db, item: Dict[str, Any]) -> NationalTeamHolding:
    row = db.query(NationalTeamHolding).filter(
        NationalTeamHolding.entity_key == item["entity_key"],
        NationalTeamHolding.stock_code == item["stock_code"],
        NationalTeamHolding.shareholder_name == item["shareholder_name"],
        NationalTeamHolding.report_period == item["report_period"],
        NationalTeamHolding.holding_type == item["holding_type"],
    ).first()
    fetched_at = _parse_datetime(item.get("fetched_at"))
    fields = {
        "entity_name": item.get("entity_name"),
        "shareholder_type": item.get("shareholder_type"),
        "shares_type": item.get("shares_type"),
        "holder_rank": item.get("holder_rank"),
        "stock_name": item.get("stock_name"),
        "notice_date": item.get("notice_date"),
        "shares": item.get("shares"),
        "share_ratio": item.get("share_ratio"),
        "shares_change": item.get("shares_change"),
        "change_ratio": item.get("change_ratio"),
        "change_name": item.get("change_name"),
        "market_value": item.get("market_value"),
        "holding_type_name": item.get("holding_type_name"),
        "source": item.get("source") or source.SOURCE_NAME,
        "source_url": item.get("source_url") or source.SOURCE_URL,
        "fetched_at": fetched_at,
        "updated_at": beijing_now(),
    }
    if row is None:
        row = NationalTeamHolding(
            entity_key=item["entity_key"],
            shareholder_name=item["shareholder_name"],
            stock_code=item["stock_code"],
            report_period=item["report_period"],
            holding_type=item["holding_type"],
            **fields,
        )
        db.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    return row


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            pass
    return beijing_now()


def _coerce_datetime(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _latest_snapshot(db) -> Optional[NationalTeamSnapshot]:
    return db.query(NationalTeamSnapshot).order_by(desc(NationalTeamSnapshot.fetched_at)).first()


def _snapshot_to_dict(row: Optional[NationalTeamSnapshot]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "report_period": row.report_period,
        "matched_count": row.matched_count,
        "fetched_at": row.fetched_at,
    }


def filter_rows_to_latest_snapshot_window(rows: Iterable[Dict[str, Any]],
                                          snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """默认页面和邮件只展示最近一次刷新触达的记录，避免历史缓存被当作最新动向。"""
    if not snapshot or not snapshot.get("fetched_at"):
        return list(rows)
    fetched_at = _coerce_datetime(snapshot.get("fetched_at"))
    if not fetched_at:
        return list(rows)
    start = fetched_at - LATEST_SNAPSHOT_WINDOW
    end = fetched_at + timedelta(seconds=5)
    filtered = []
    for row in rows:
        row_time = _coerce_datetime(row.get("updated_at") or row.get("detected_at") or row.get("fetched_at"))
        if row_time and start <= row_time <= end:
            filtered.append(row)
    return filtered


def _save_changes(db, changes: List[Dict[str, Any]], report_period: str) -> int:
    db.query(NationalTeamChange).filter(NationalTeamChange.report_period == report_period).delete()
    saved = 0
    for item in changes:
        row = NationalTeamChange(
            entity_key=item["entity_key"],
            entity_name=item.get("entity_name") or "",
            stock_code=item.get("stock_code") or "",
            stock_name=item.get("stock_name"),
            shareholder_name=item.get("shareholder_name") or "",
            holding_type=item.get("holding_type") or "",
            report_period=report_period,
            previous_report_period=item.get("previous_report_period"),
            change_type=item.get("change_type") or "unchanged",
            shares=item.get("shares"),
            previous_shares=item.get("previous_shares"),
            shares_delta=item.get("shares_delta"),
            change_ratio=item.get("change_ratio"),
            source=item.get("source") or source.SOURCE_NAME,
            source_url=item.get("source_url") or source.SOURCE_URL,
            detected_at=beijing_now(),
        )
        db.add(row)
        saved += 1
    return saved


def _event_from_change(item: Dict[str, Any], report_period: str) -> Optional[Dict[str, Any]]:
    if item.get("change_type") not in {"new", "increase", "decrease", "exit"}:
        return None
    action = {
        "new": "首次记录",
        "increase": "增持",
        "decrease": "减持",
        "exit": "退出",
    }[item["change_type"]]
    event_date = item.get("notice_date") or beijing_now().strftime("%Y-%m-%d")
    disclosed_change = item.get("change_name") or action
    holder_name = item.get("shareholder_name") or item.get("entity_name")
    title = f"{item.get('entity_name')} {action} {item.get('stock_name')}({item.get('stock_code')}) · {holder_name}"
    return {
        "entity_key": item.get("entity_key"),
        "entity_name": item.get("entity_name"),
        "event_date": event_date,
        "title": title,
        "summary": f"基于{source.SOURCE_NAME}{report_period}披露，{item.get('shareholder_name')}在{item.get('holding_type_name', '股东名单')}中显示为{disclosed_change}。首次记录表示系统首次纳入快照，不等同于上市公司披露的新进。",
        "related_stock_code": item.get("stock_code"),
        "related_stock_name": item.get("stock_name"),
        "impact_level": "watch",
        "source": source.SOURCE_NAME,
        "source_url": item.get("source_url") or source.SOURCE_URL,
        "fetched_at": beijing_now(),
    }


def _dedupe_event_changes(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一主体/股票/股东/公告日只生成一条事件，避免十大股东和十大流通股东重复推送。"""
    deduped = {}
    for item in changes:
        key = (
            item.get("entity_key"),
            item.get("stock_code"),
            item.get("shareholder_name"),
            item.get("notice_date"),
            item.get("change_type"),
        )
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = item
            continue
        # 优先保留十大股东口径；若没有则保留先出现的流通股东口径。
        if existing.get("holding_type") != "top10" and item.get("holding_type") == "top10":
            deduped[key] = item
    return list(deduped.values())


def _save_events_from_changes(db, changes: List[Dict[str, Any]], report_period: str) -> int:
    saved = 0
    for item in _dedupe_event_changes(changes):
        event = _event_from_change(item, report_period)
        if not event:
            continue
        exists = db.query(NationalTeamEvent).filter(
            NationalTeamEvent.entity_key == event["entity_key"],
            NationalTeamEvent.event_date == event["event_date"],
            NationalTeamEvent.title == event["title"],
            NationalTeamEvent.source == event["source"],
        ).first()
        if exists:
            continue
        db.add(NationalTeamEvent(**event))
        saved += 1
    return saved


def refresh_national_team_data(periods: Optional[List[str]] = None,
                               max_pages_per_filter: int = 1,
                               page_size: int = 30) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        holdings, meta = source.fetch_holdings(
            periods=periods,
            max_pages_per_filter=max_pages_per_filter,
            page_size=page_size,
        )
        report_periods = sorted({item["report_period"] for item in holdings if item.get("report_period")}, reverse=True)
        for item in holdings:
            _upsert_holding(db, item)

        all_changes = []
        include_exits = bool(periods)
        for entity in source.entity_definitions():
            current = [item for item in holdings if item.get("entity_key") == entity["key"]]
            previous = _latest_previous_holdings(db, entity["key"], set(report_periods))
            all_changes.extend(detect_changes(entity["key"], previous, current, include_exits=include_exits))

        saved_changes = 0
        saved_events = 0
        # 这些事件是由东方财富持仓变动派生出来的摘要。刷新时重建，避免旧文案或旧口径残留。
        db.query(NationalTeamEvent).filter(NationalTeamEvent.source == source.SOURCE_NAME).delete()
        for period in report_periods or [""]:
            period_changes = [item for item in all_changes if item.get("report_period") == period]
            saved_changes += _save_changes(db, period_changes, period)
            saved_events += _save_events_from_changes(db, period_changes, period)

        snapshot = NationalTeamSnapshot(
            report_period=",".join(report_periods),
            raw_count=meta.get("raw_count", 0),
            matched_count=len(holdings),
            source=meta.get("source") or source.SOURCE_NAME,
            status="success" if not meta.get("errors") else "partial",
            error_message="\n".join(meta.get("errors", [])[:10]) or None,
            fetched_at=beijing_now(),
        )
        db.add(snapshot)
        db.commit()
        has_errors = bool(meta.get("errors"))
        return {
            "ok": not has_errors,
            "periods": report_periods,
            "raw_count": meta.get("raw_count", 0),
            "holding_count": len(holdings),
            "change_count": saved_changes,
            "event_count": saved_events,
            "source_status": {
                "ok": not has_errors,
                "source": meta.get("source") or source.SOURCE_NAME,
                "source_url": meta.get("source_url") or source.SOURCE_URL,
                "fetched_at": meta.get("fetched_at"),
                "errors": meta.get("errors", []),
                "note": meta.get("note"),
                "scope_note": "默认刷新为最近更新样本；若需完整报告期，请指定 periods 并完整分页采集。",
            },
        }
    except Exception as exc:
        db.rollback()
        db.add(NationalTeamSnapshot(
            report_period=",".join(periods or []),
            raw_count=0,
            matched_count=0,
            source=source.SOURCE_NAME,
            status="failed",
            error_message=str(exc),
            fetched_at=beijing_now(),
        ))
        db.commit()
        logger.exception("国家队数据刷新失败: %s", exc)
        return {
            "ok": False,
            "periods": periods or [],
            "raw_count": 0,
            "holding_count": 0,
            "change_count": 0,
            "event_count": 0,
            "source_status": {
                "ok": False,
                "source": source.SOURCE_NAME,
                "source_url": source.SOURCE_URL,
                "fetched_at": beijing_now().isoformat(),
                "errors": [str(exc)],
            },
        }
    finally:
        db.close()


def _latest_source_status(db) -> Dict[str, Any]:
    row = _latest_snapshot(db)
    return _source_status_from_snapshot(row)


def _source_status_from_snapshot(row) -> Dict[str, Any]:
    if not row:
        return {
            "ok": False,
            "source": source.SOURCE_NAME,
            "source_url": source.SOURCE_URL,
            "fetched_at": None,
            "note": "尚未刷新国家队持仓数据",
        }
    return {
        "ok": row.status == "success",
        "source": row.source,
        "source_url": source.SOURCE_URL,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "status": row.status,
        "error": row.error_message,
    }


def build_summary_payload(holdings: List[Dict[str, Any]],
                          changes: List[Dict[str, Any]],
                          events: List[Dict[str, Any]],
                          generated_at: Optional[datetime] = None,
                          source_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    generated_at = generated_at or beijing_now()
    entities = []
    for entity in source.entity_definitions():
        entity_holdings = [item for item in holdings if item.get("entity_key") == entity["key"]]
        entity_changes = [item for item in changes if item.get("entity_key") == entity["key"]]
        entity_events = sorted(
            [item for item in events if item.get("entity_key") == entity["key"]],
            key=lambda item: item.get("event_date") or "",
            reverse=True,
        )
        counts = {key: 0 for key in CHANGE_TYPES}
        for item in entity_changes:
            if item.get("change_type") in counts:
                counts[item["change_type"]] += 1
        periods = sorted({item.get("report_period") for item in entity_holdings if item.get("report_period")}, reverse=True)
        notices = sorted({item.get("notice_date") for item in entity_holdings if item.get("notice_date")}, reverse=True)
        entities.append({
            "entity_key": entity["key"],
            "entity_name": entity["name"],
            "holding_count": len(entity_holdings),
            "latest_report_period": periods[0] if periods else None,
            "latest_notice_date": notices[0] if notices else None,
            "change_counts": counts,
            "latest_event": entity_events[0] if entity_events else None,
        })
    return {
        "generated_at": generated_at.isoformat(),
        "total_holdings": len(holdings),
        "total_events": len(events),
        "entities": entities,
        "source_status": source_status or {"ok": False, "source": source.SOURCE_NAME},
        "scope_note": "默认展示最近一次刷新命中的公开披露样本；十大股东/十大流通股东不是每日实时持仓。",
    }


def get_summary() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        snapshot = _snapshot_to_dict(_latest_snapshot(db))
        holdings = filter_rows_to_latest_snapshot_window(
            [_model_to_dict(row) for row in db.query(NationalTeamHolding).all()],
            snapshot,
        )
        changes = filter_rows_to_latest_snapshot_window(
            [_model_to_dict(row) for row in db.query(NationalTeamChange).all()],
            snapshot,
        )
        event_query = db.query(NationalTeamEvent)
        events = [_model_to_dict(row) for row in event_query.order_by(desc(NationalTeamEvent.event_date)).limit(50).all()]
        summary = build_summary_payload(holdings, changes, events, source_status=_latest_source_status(db))
        summary["total_events"] = event_query.count()
        return summary
    finally:
        db.close()


def list_holdings(entity: Optional[str] = None,
                  period: Optional[str] = None,
                  page: int = 1,
                  size: int = 20) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        q = db.query(NationalTeamHolding)
        if entity:
            q = q.filter(NationalTeamHolding.entity_key == entity)
        if period:
            q = q.filter(NationalTeamHolding.report_period == source.normalize_period(period))
        else:
            snapshot = _latest_snapshot(db)
            if snapshot and snapshot.fetched_at:
                q = q.filter(
                    NationalTeamHolding.updated_at >= snapshot.fetched_at - LATEST_SNAPSHOT_WINDOW,
                    NationalTeamHolding.updated_at <= snapshot.fetched_at + timedelta(seconds=5),
                )
        total = q.count()
        rows = q.order_by(
            desc(NationalTeamHolding.report_period),
            desc(NationalTeamHolding.notice_date),
            NationalTeamHolding.entity_key,
            NationalTeamHolding.stock_code,
        ).offset((page - 1) * size).limit(size).all()
        return {"total": total, "page": page, "size": size, "items": [_model_to_dict(row) for row in rows]}
    finally:
        db.close()


def get_top_holder_stats(limit: int = 10) -> Dict[str, Any]:
    """按最近一次刷新命中的十大股东披露样本，返回三类国家队主体 Top 持仓。"""
    db = SessionLocal()
    try:
        snapshot = _latest_snapshot(db)
        entities = []
        for entity in source.entity_definitions():
            q = db.query(NationalTeamHolding).filter(
                NationalTeamHolding.entity_key == entity["key"],
                NationalTeamHolding.holding_type == "top10",
            )
            if snapshot and snapshot.fetched_at:
                q = q.filter(
                    NationalTeamHolding.updated_at >= snapshot.fetched_at - LATEST_SNAPSHOT_WINDOW,
                    NationalTeamHolding.updated_at <= snapshot.fetched_at + timedelta(seconds=5),
                )
            latest_rows = select_latest_current_holdings(_model_to_dict(row) for row in q.all())
            latest_rows = sorted(
                latest_rows,
                key=lambda item: (
                    -(item.get("market_value") or 0),
                    -(item.get("shares") or 0),
                    item.get("stock_code") or "",
                ),
            )
            entities.append({
                "entity_key": entity["key"],
                "entity_name": entity["name"],
                "total": len(latest_rows),
                "items": latest_rows[:limit],
            })
        return {
            "limit": limit,
            "holding_type": "top10",
            "entities": entities,
            "source_status": _latest_source_status(db),
            "scope_note": "最近一次刷新命中的十大股东公开披露样本",
        }
    finally:
        db.close()


def get_capital_flow_stats(limit: int = 10) -> Dict[str, Any]:
    """按公开披露的增减持股数估算买入/卖出资金量，返回三类国家队主体 Top10。"""
    db = SessionLocal()
    try:
        snapshot = _latest_snapshot(db)
        entities = []
        for entity in source.entity_definitions():
            q = db.query(NationalTeamHolding).filter(
                NationalTeamHolding.entity_key == entity["key"],
            )
            if snapshot and snapshot.fetched_at:
                q = q.filter(
                    NationalTeamHolding.updated_at >= snapshot.fetched_at - LATEST_SNAPSHOT_WINDOW,
                    NationalTeamHolding.updated_at <= snapshot.fetched_at + timedelta(seconds=5),
                )
            rows = select_latest_capital_flow_holdings(_model_to_dict(row) for row in q.all())
            flow_items = [_capital_flow_item(row) for row in rows]
            flow_items = [item for item in flow_items if item]
            buy_items = sorted(
                [item for item in flow_items if item["direction"] == "buy"],
                key=lambda item: item["change_amount"],
                reverse=True,
            )
            sell_items = sorted(
                [item for item in flow_items if item["direction"] == "sell"],
                key=lambda item: item["change_amount"],
                reverse=True,
            )
            entities.append({
                "entity_key": entity["key"],
                "entity_name": entity["name"],
                "full_name": entity.get("full_name"),
                "english_name": entity.get("english_name"),
                "buy": {
                    "total": len(buy_items),
                    "items": buy_items[:limit],
                },
                "sell": {
                    "total": len(sell_items),
                    "items": sell_items[:limit],
                },
            })
        return {
            "limit": limit,
            "entities": entities,
            "source_status": _latest_source_status(db),
            "scope_note": "买入=公开披露增持；卖出=公开披露减持；资金量按披露持仓市值/持股数×变动股数估算。",
        }
    finally:
        db.close()


def get_holding_value_stats(limit: int = 10) -> Dict[str, Any]:
    """按上市公司聚合国家队公开披露持股市值，返回三类主体持股金额 Top10。"""
    db = SessionLocal()
    try:
        snapshot = _latest_snapshot(db)
        entities = []
        for entity in source.entity_definitions():
            q = db.query(NationalTeamHolding).filter(
                NationalTeamHolding.entity_key == entity["key"],
            )
            if snapshot and snapshot.fetched_at:
                q = q.filter(
                    NationalTeamHolding.updated_at >= snapshot.fetched_at - LATEST_SNAPSHOT_WINDOW,
                    NationalTeamHolding.updated_at <= snapshot.fetched_at + timedelta(seconds=5),
                )
            items = _holding_value_items((_model_to_dict(row) for row in q.all()), limit)
            entities.append({
                "entity_key": entity["key"],
                "entity_name": entity["name"],
                "full_name": entity.get("full_name"),
                "english_name": entity.get("english_name"),
                "items": items,
                "total": len(items),
            })
        return {
            "limit": limit,
            "entities": entities,
            "source_status": _latest_source_status(db),
            "scope_note": "按最近一次刷新命中的公开披露样本，将同一上市公司的多个国家队股东账户持股市值合并排序。",
        }
    finally:
        db.close()


def list_events(entity: Optional[str] = None,
                date_from: Optional[str] = None,
                date_to: Optional[str] = None,
                limit: int = 50,
                offset: int = 0) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        q = db.query(NationalTeamEvent)
        if entity:
            q = q.filter(NationalTeamEvent.entity_key == entity)
        if date_from:
            q = q.filter(NationalTeamEvent.event_date >= date_from)
        if date_to:
            q = q.filter(NationalTeamEvent.event_date <= date_to)
        total = q.count()
        rows = (
            q.order_by(desc(NationalTeamEvent.event_date), desc(NationalTeamEvent.fetched_at))
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_model_to_dict(row) for row in rows],
            "source_status": _latest_source_status(db),
        }
    finally:
        db.close()


def get_email_summary() -> Dict[str, Any]:
    summary = get_summary()
    events = list_events(limit=8)
    holdings = list_holdings(size=8)
    return {
        **summary,
        "events": events["items"],
        "holdings": holdings["items"],
    }
