"""系统 API — 健康检查、状态、配置"""
from fastapi import APIRouter
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

BEIJING_TZ = timezone(timedelta(hours=8))


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(BEIJING_TZ).isoformat()}


@router.get("/status")
async def system_status():
    """系统运行状态（北京时间）"""
    now = datetime.now(BEIJING_TZ)
    is_trading = now.weekday() < 5 and 9 <= now.hour <= 15
    return {
        "is_trading_day": now.weekday() < 5,
        "is_trading_hours": is_trading,
        "next_execution": "15:10 (北京时间, 交易日)",
        "cron_expression": "每个交易日 15:10 (北京时间)",
        "timestamp": now.isoformat(),
    }


@router.post("/test-email")
async def test_email_endpoint():
    """发送测试邮件"""
    try:
        from ..agents.layer3_recommendation.notifier import test_email
        ok, msg = test_email()
        if ok:
            return {"status": "ok", "message": "测试邮件已发送，请检查收件箱"}
        else:
            return {"status": "error", "message": msg}
    except Exception as e:
        logger.error(f"测试邮件失败: {e}")
        return {"status": "error", "message": f"发送失败: {str(e)}"}


@router.get("/audit")
async def audit_watchlist():
    """对全量监控列表执行数据审核，返回问题清单"""
    try:
        from ..services.watchlist_store import parse_watchlist, FRANCE_FILE
        from ..agents.layer4_audit.validators import ALL_VALIDATORS, ValidationResult
        from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes

        entries = parse_watchlist(FRANCE_FILE)
        if not entries:
            return {"total_entries": 0, "issues": [], "pass_rate": 100.0,
                    "message": "监控列表为空"}

        codes = [e["code"] for e in entries]
        quotes_df = fetch_stock_quotes(codes)
        quote_map = {}
        if not quotes_df.empty:
            for _, row in quotes_df.iterrows():
                quote_map[row["代码"]] = row

        issues = []
        total_validations = 0
        passed_validations = 0

        for e in entries:
            code = e["code"]
            q = quote_map.get(code)
            current_price = q.get("最新价") if q is not None else None

            ctx = {
                "code": code,
                "name": e.get("name", ""),
                "current_price": current_price,
                "ref_price": e.get("ref_price", 0),
                "drop_pct": round((current_price - e["ref_price"]) / e["ref_price"] * 100, 2)
                if current_price and e.get("ref_price") else 0,
                "市盈率": q.get("市盈率") if q is not None else None,
                "量比": q.get("量比") if q is not None else None,
                "换手率": q.get("换手率") if q is not None else None,
                "zt_quality": {
                    "封板时间": int(e.get("seal_time", 0)) if e.get("seal_time", "0").isdigit() else 0,
                },
                "history": None,
            }

            for validator in ALL_VALIDATORS:
                try:
                    vr = validator(ctx)
                except Exception as ex:
                    vr = ValidationResult("系统", "warn", str(ex))

                total_validations += 1
                if vr.status == "pass":
                    passed_validations += 1

                if vr.status in ("warn", "fail"):
                    issues.append({
                        "code": code,
                        "name": e.get("name", ""),
                        "field": vr.name,
                        "issue": vr.detail,
                        "severity": vr.status,
                    })

        pass_rate = (passed_validations / total_validations * 100) if total_validations > 0 else 100.0

        return {
            "total_entries": len(entries),
            "total_validations": total_validations,
            "issues": issues,
            "pass_rate": round(pass_rate, 1),
            "error_count": sum(1 for i in issues if i["severity"] == "fail"),
            "warn_count": sum(1 for i in issues if i["severity"] == "warn"),
        }
    except Exception as e:
        logger.error(f"审计接口异常: {e}")
        return {"status": "error", "message": str(e)}
