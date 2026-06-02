"""国家队动向 API"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..services.national_team_service import (
    get_capital_flow_stats,
    get_holding_value_stats,
    get_top_holder_stats,
    get_summary,
    list_events,
    list_holdings,
    refresh_national_team_data,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/summary")
def national_team_summary():
    return get_summary()


@router.get("/holdings")
def national_team_holdings(entity: Optional[str] = Query(None),
                           period: Optional[str] = Query(None),
                           page: int = Query(1, ge=1),
                           size: int = Query(20, ge=1, le=200)):
    return list_holdings(entity=entity, period=period, page=page, size=size)


@router.get("/top-holders")
def national_team_top_holders(limit: int = Query(10, ge=1, le=10)):
    return get_top_holder_stats(limit=limit)


@router.get("/capital-flows")
def national_team_capital_flows(limit: int = Query(10, ge=1, le=10)):
    return get_capital_flow_stats(limit=limit)


@router.get("/holding-values")
def national_team_holding_values(limit: int = Query(10, ge=1, le=10)):
    return get_holding_value_stats(limit=limit)


@router.get("/events")
def national_team_events(entity: Optional[str] = Query(None),
                         date_from: Optional[str] = Query(None),
                         date_to: Optional[str] = Query(None),
                         limit: int = Query(50, ge=1, le=200),
                         offset: int = Query(0, ge=0)):
    return list_events(entity=entity, date_from=date_from, date_to=date_to, limit=limit, offset=offset)


@router.post("/refresh")
def national_team_refresh():
    result = refresh_national_team_data()
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return result
