"""
运行时策略配置服务。

配置来源优先级：
1. system_configs 表中由前端策略配置页保存的值
2. config/strategy_params.py 中的默认值
3. 环境变量中的敏感信息（仅 SMTP 密码，不写入数据库或配置文件）
"""
from __future__ import annotations

import importlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from ..db.database import SessionLocal
from ..db.models import SystemConfig

BEIJING_TZ = timezone(timedelta(hours=8))
CONFIG_KEY = "strategy_runtime"
PREVIOUS_KEY = "strategy_runtime_previous"
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
STRATEGY_FILE = PROJECT_DIR / "config" / "strategy_params.py"

FACTOR_NAMES = {
    "pullback": "回撤幅度",
    "volume_trend": "量能趋势",
    "ma_alignment": "均线多头",
    "strength": "强势确认",
    "entry_point": "尾盘买点",
    "market_cap": "流通市值",
    "volume_ratio": "量比",
    "turnover": "换手率",
    "pe": "市盈率",
    "zt_quality": "涨停质量",
    "principal_capital": "主力资金",
    "event_bonus": "事件驱动加分",
}

FACTOR_ORDER = [
    "pullback", "volume_trend", "ma_alignment", "strength", "entry_point",
    "market_cap", "volume_ratio", "turnover", "pe", "zt_quality",
    "principal_capital",
]


class ConfigValidationError(ValueError):
    """配置校验失败。"""


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


def _round_float(value: Any, digits: int = 4) -> float:
    return round(float(value), digits)


def _strategy_module():
    return importlib.import_module("config.strategy_params")


def _factor_params(default_factors) -> Dict[str, dict]:
    return {factor.key: dict(factor.params or {}) for factor in default_factors}


def _factor_weights(default_factors) -> Dict[str, float]:
    weights = {}
    for factor in default_factors:
        if factor.key == "event_bonus":
            continue
        weights[factor.key] = round(float(factor.weight) * 100, 2)
    return weights


def _default_config() -> Dict[str, Any]:
    sp = _strategy_module()
    params = _factor_params(sp.DEFAULT_FACTORS)
    pullback = params.get("pullback", {})
    volume_ratio = params.get("volume_ratio", {})
    turnover = params.get("turnover", {})
    market_cap = params.get("market_cap", {})
    pe = params.get("pe", {})
    zt_quality = params.get("zt_quality", {})
    notify = getattr(sp, "NOTIFY_CONFIG", {})
    sort_cfg = getattr(sp, "ZT_SORT_CONFIG", {})

    return {
        "strategy": {
            "dropMin": _round_float(pullback.get("ideal_min", 5.0), 2),
            "dropMax": _round_float(pullback.get("hard_max", 10.0), 2),
            "volMin": _round_float(volume_ratio.get("hard_min", 1.0), 2),
            "volMax": _round_float(volume_ratio.get("hard_max", 5.0), 2),
            "turnoverMin": _round_float(turnover.get("hard_min", 5.0), 2),
            "turnoverMax": _round_float(turnover.get("hard_max", 10.0), 2),
            "mcMin": _round_float(market_cap.get("hard_min", 50.0), 2),
            "mcMax": _round_float(market_cap.get("hard_max", 200.0), 2),
            "peMax": _round_float(pe.get("max_pe", 50.0), 2),
            "trackingDays": int(getattr(sp.StrategyConfig(), "tracking_days", 30)),
            "latestFbt": int(zt_quality.get("latest_fbt", 140000)),
            "maxZbc": int(zt_quality.get("max_zbc", 0)),
            "maxZtFrequency": int(zt_quality.get("max_zt_frequency", 2)),
        },
        "factorWeights": _factor_weights(sp.DEFAULT_FACTORS),
        "notification": {
            "emailEnabled": bool(notify.get("email_enabled", True)),
            "emailHost": str(notify.get("email_host", "smtp.qq.com")),
            "emailPort": int(notify.get("email_port", 465)),
            "emailUser": str(notify.get("email_user", "")),
            "emailTo": str(notify.get("email_to", "")),
            "recipients": list(notify.get("email_recipients", []) or []),
        },
        "ztSort": {
            "sortBy": str(sort_cfg.get("sort_by", "seal_time")),
            "sortOrder": str(sort_cfg.get("sort_order", "asc")),
        },
        "schedule": {
            "cronExpression": "10 15 * * 1-5",
            "runTime": "15:10",
        },
        # [邮件升级] 新增配置项（默认值）
        "actionPlan": {"enabled": True},
        "backtest": {"enabled": True, "holdDays": [1, 3, 5]},
        "llm": {"enabled": False, "model": "claude-haiku-4-5-20251001"},
    }


def _deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result = deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_row(db, key: str) -> Optional[SystemConfig]:
    return db.query(SystemConfig).filter(SystemConfig.key == key).first()


def _read_json_row(db, key: str) -> Optional[dict]:
    row = _get_row(db, key)
    if not row:
        return None
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return None


def _save_json_row(db, key: str, value: dict) -> SystemConfig:
    row = _get_row(db, key)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if row is None:
        row = SystemConfig(key=key, value=payload)
        db.add(row)
    else:
        row.value = payload
        row.updated_at = datetime.now(BEIJING_TZ)
    return row


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ConfigValidationError(f"{label} 必须是数字")
    if not minimum <= num <= maximum:
        raise ConfigValidationError(f"{label} 必须在 {minimum:g}-{maximum:g} 之间")
    return round(num, 4)


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ConfigValidationError(f"{label} 必须是整数")
    if not minimum <= num <= maximum:
        raise ConfigValidationError(f"{label} 必须在 {minimum}-{maximum} 之间")
    return num


def _hhmmss(value: Any, label: str) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ConfigValidationError(f"{label} 必须是 HHMMSS 整数")
    if not 0 <= num <= 235959:
        raise ConfigValidationError(f"{label} 必须在 00:00:00-23:59:59 之间")
    hour = num // 10000
    minute = (num % 10000) // 100
    second = num % 100
    if hour > 23 or minute > 59 or second > 59:
        raise ConfigValidationError(f"{label} 必须是有效 HH:MM:SS 时间")
    return num


def _validate_email(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
        raise ConfigValidationError(f"{label} 格式不正确")
    return text


def validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化完整配置。"""
    cfg = _deep_merge(_default_config(), config)
    strategy = cfg["strategy"]
    weights = cfg["factorWeights"]
    notify = cfg["notification"]
    zt_sort = cfg["ztSort"]

    normalized_strategy = {
        "dropMin": _number(strategy.get("dropMin"), "回撤下限", 0, 20),
        "dropMax": _number(strategy.get("dropMax"), "回撤上限", 0, 20),
        "volMin": _number(strategy.get("volMin"), "量比下限", 0, 20),
        "volMax": _number(strategy.get("volMax"), "量比上限", 0, 20),
        "turnoverMin": _number(strategy.get("turnoverMin"), "换手率下限", 0, 80),
        "turnoverMax": _number(strategy.get("turnoverMax"), "换手率上限", 0, 80),
        "mcMin": _number(strategy.get("mcMin"), "流通市值下限", 0, 10000),
        "mcMax": _number(strategy.get("mcMax"), "流通市值上限", 0, 10000),
        "peMax": _number(strategy.get("peMax"), "PE 上限", 1, 500),
        "trackingDays": _integer(strategy.get("trackingDays"), "监控周期", 1, 365),
        "latestFbt": _hhmmss(strategy.get("latestFbt"), "最晚封板时间"),
        "maxZbc": _integer(strategy.get("maxZbc"), "最大炸板次数", 0, 20),
        "maxZtFrequency": _integer(strategy.get("maxZtFrequency"), "30天最大涨停次数", 0, 30),
    }
    if normalized_strategy["dropMin"] > normalized_strategy["dropMax"]:
        raise ConfigValidationError("回撤下限不能大于上限")
    if normalized_strategy["volMin"] > normalized_strategy["volMax"]:
        raise ConfigValidationError("量比下限不能大于上限")
    if normalized_strategy["turnoverMin"] > normalized_strategy["turnoverMax"]:
        raise ConfigValidationError("换手率下限不能大于上限")
    if normalized_strategy["mcMin"] > normalized_strategy["mcMax"]:
        raise ConfigValidationError("流通市值下限不能大于上限")

    normalized_weights = {}
    for key in FACTOR_ORDER:
        normalized_weights[key] = _number(weights.get(key), f"{FACTOR_NAMES[key]}权重", 0, 100)
    total_weight = round(sum(normalized_weights.values()), 4)
    if abs(total_weight - 100.0) > 0.01:
        raise ConfigValidationError(f"因子权重总和必须为 100%，当前为 {total_weight:g}%")

    normalized_notify = {
        "emailEnabled": bool(notify.get("emailEnabled", True)),
        "emailHost": str(notify.get("emailHost") or "smtp.qq.com").strip(),
        "emailPort": _integer(notify.get("emailPort", 465), "SMTP端口", 1, 65535),
        "emailUser": _validate_email(notify.get("emailUser", ""), "发件邮箱"),
        "emailTo": _validate_email(notify.get("emailTo", ""), "收件邮箱"),
    }
    recipients = []
    for item in notify.get("recipients") or []:
        email = _validate_email(item, "收件邮箱")
        if email and email not in recipients:
            recipients.append(email)
    total_recipients = []
    for addr in [normalized_notify["emailUser"], normalized_notify["emailTo"], *recipients]:
        if addr and addr not in total_recipients:
            total_recipients.append(addr)
    if len(total_recipients) > 10:
        raise ConfigValidationError("收件箱数量不能超过 10 个")
    normalized_notify["recipients"] = recipients
    if (
        normalized_notify["emailEnabled"]
        and not normalized_notify["emailTo"]
        and not normalized_notify["emailUser"]
        and not normalized_notify["recipients"]
    ):
        raise ConfigValidationError("启用邮件时必须填写收件邮箱")

    allowed_sort = {"change_pct", "turnover", "vol_ratio", "pe", "mcap", "seal_time"}
    sort_by = str(zt_sort.get("sortBy", "seal_time"))
    sort_order = str(zt_sort.get("sortOrder", "asc"))
    if sort_by not in allowed_sort:
        raise ConfigValidationError("涨停列表排序字段不合法")
    if sort_order not in {"asc", "desc"}:
        raise ConfigValidationError("涨停列表排序方向必须为 asc 或 desc")

    return {
        "strategy": normalized_strategy,
        "factorWeights": normalized_weights,
        "notification": normalized_notify,
        "ztSort": {"sortBy": sort_by, "sortOrder": sort_order},
        "schedule": {
            "cronExpression": str(cfg.get("schedule", {}).get("cronExpression") or "10 15 * * 1-5"),
            "runTime": str(cfg.get("schedule", {}).get("runTime") or "15:10"),
        },
        # [邮件升级] 新增配置项（归一化，保持向后兼容，不参与权重/阈值校验）
        "actionPlan": {
            "enabled": bool((cfg.get("actionPlan") or {}).get("enabled", True)),
        },
        "backtest": {
            "enabled": bool((cfg.get("backtest") or {}).get("enabled", True)),
            "holdDays": [int(d) for d in ((cfg.get("backtest") or {}).get("holdDays") or [1, 3, 5])],
        },
        "llm": {
            "enabled": bool((cfg.get("llm") or {}).get("enabled", False)),
            "model": str((cfg.get("llm") or {}).get("model") or "claude-haiku-4-5-20251001"),
        },
    }


def get_effective_config() -> Dict[str, Any]:
    """读取当前生效配置和上一次保存值。"""
    db = SessionLocal()
    try:
        defaults = _default_config()
        saved = _read_json_row(db, CONFIG_KEY)
        previous = _read_json_row(db, PREVIOUS_KEY)
        current = validate_config(_deep_merge(defaults, saved or {}))
        prev_config = validate_config(_deep_merge(defaults, previous or saved or {}))
        row = _get_row(db, CONFIG_KEY)
        return {
            "config": current,
            "previous_config": prev_config,
            "saved_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "source": "database" if saved else "config/strategy_params.py",
        }
    finally:
        db.close()


def save_effective_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存前端配置，写入数据库并同步 strategy_params.py。"""
    db = SessionLocal()
    try:
        defaults = _default_config()
        current_row = _get_row(db, CONFIG_KEY)
        current_value = json.loads(current_row.value) if current_row else defaults
        merged = validate_config(_deep_merge(current_value, payload))
        previous = validate_config(_deep_merge(defaults, current_value))

        _save_json_row(db, PREVIOUS_KEY, previous)
        row = _save_json_row(db, CONFIG_KEY, merged)
        db.commit()
        db.refresh(row)
        sync_strategy_params_file(merged)

        return {
            "config": merged,
            "previous_config": previous,
            "saved_at": row.updated_at.isoformat() if row.updated_at else _now_iso(),
            "source": "database",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def resolve_screening_params(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """生成筛选流水线实参；显式传入值优先，其次使用运行时配置。"""
    cfg = get_effective_config()["config"]
    strategy = cfg["strategy"]
    params = {
        "drop_min": strategy["dropMin"],
        "drop_max": strategy["dropMax"],
        "vol_min": strategy["volMin"],
        "vol_max": strategy["volMax"],
        "turnover_min": strategy["turnoverMin"],
        "turnover_max": strategy["turnoverMax"],
        "mc_min": strategy["mcMin"],
        "mc_max": strategy["mcMax"],
        "pe_max": strategy["peMax"],
    }
    for key, value in (overrides or {}).items():
        if value is not None:
            params[key] = float(value)
    return params


def get_factor_weights_decimal(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    cfg = config or get_effective_config()["config"]
    return {key: round(value / 100.0, 6) for key, value in cfg["factorWeights"].items()}


def build_skill_params(strategy: Dict[str, Any]) -> Dict[str, dict]:
    drop_min = float(strategy["dropMin"])
    drop_max = float(strategy["dropMax"])
    vol_min = float(strategy["volMin"])
    vol_max = float(strategy["volMax"])
    turnover_min = float(strategy["turnoverMin"])
    turnover_max = float(strategy["turnoverMax"])
    mc_min = float(strategy["mcMin"])
    mc_max = float(strategy["mcMax"])

    return {
        "pullback": {
            "hard_min": drop_min,
            "ideal_min": drop_min,
            "ideal_max": min(drop_max, drop_min + max(1.0, (drop_max - drop_min) * 0.6)),
            "hard_max": drop_max,
        },
        "volume_ratio": {
            "hard_min": vol_min,
            "ideal_min": vol_min + (vol_max - vol_min) * 0.125,
            "ideal_max": vol_min + (vol_max - vol_min) * 0.5,
            "hard_max": vol_max,
        },
        "turnover": {
            "hard_min": turnover_min,
            "ideal_min": turnover_min,
            "ideal_max": turnover_min + (turnover_max - turnover_min) * 0.6,
            "hard_max": turnover_max,
        },
        "market_cap": {
            "hard_min": mc_min,
            "ideal_min": mc_min,
            "ideal_max": mc_min + (mc_max - mc_min) / 3,
            "hard_max": mc_max,
        },
        "pe": {"max_pe": float(strategy["peMax"])},
        "zt_quality": {
            "latest_fbt": int(strategy["latestFbt"]),
            "max_zbc": int(strategy["maxZbc"]),
            "max_zt_frequency": int(strategy["maxZtFrequency"]),
        },
        "principal_capital": {
            "buy_threshold": 50.0,
            "sell_threshold": 30.0,
            "exclude_star": True,
            "min_amount": 10_000_000,
        },
    }


def sync_strategy_params_file(config: Dict[str, Any]) -> None:
    """把当前运行时配置同步为 Python 默认配置文件。"""
    strategy = config["strategy"]
    weights = get_factor_weights_decimal(config)
    skill_params = build_skill_params(strategy)
    notify = config["notification"]
    zt_sort = config["ztSort"]

    factor_rows = []
    for key in FACTOR_ORDER:
        params = skill_params.get(key, {})
        params_text = f",\n                 params={params!r}" if params else ""
        factor_rows.append(
            f'    FactorConfig(name="{FACTOR_NAMES[key]}", key="{key}", '
            f"weight={weights[key]:.6f}{params_text}),"
        )
    factor_rows.append(
        '    FactorConfig(name="事件驱动加分", key="event_bonus", weight=0.000000,'
        '\n                 params={"max_bonus": 8.0, "max_penalty": -8.0}),'
    )

    content = f'''"""
策略参数 — 多因子权重 + 阈值配置

此文件会在前端“策略配置”保存后由后端同步更新。
运行时业务仍优先读取 system_configs 表中的配置值。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FactorConfig:
    """单个因子的配置"""
    name: str
    key: str
    weight: float          # 权重 (0.0 - 1.0)
    enabled: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class StrategyConfig:
    """完整策略配置"""
    factors: List[FactorConfig] = field(default_factory=list)
    hard_gates: dict = field(default_factory=dict)

    # 推荐等级阈值
    strong_buy_threshold: float = 70.0
    buy_threshold: float = 55.0
    watch_threshold: float = 40.0

    # 监控周期
    tracking_days: int = {int(strategy["trackingDays"])}

    # 涨停质量过滤
    latest_fbt: int = {int(strategy["latestFbt"])}      # 最晚封板时间 (HHMMSS)
    max_zbc: int = {int(strategy["maxZbc"])}              # 最大炸板次数
    max_zt_frequency: int = {int(strategy["maxZtFrequency"])}     # 30天内最大涨停次数

    def active_factors(self) -> List[FactorConfig]:
        return [f for f in self.factors if f.enabled]

    def total_weight(self) -> float:
        return sum(f.weight for f in self.active_factors())

    def validate_weights(self) -> bool:
        return abs(self.total_weight() - 1.0) < 0.001


# ---- 默认因子配置 ----
DEFAULT_FACTORS = [
{chr(10).join(factor_rows)}
]


# ---- 通知配置 ----
NOTIFY_CONFIG = {{
    "email_enabled": {bool(notify["emailEnabled"])!r},
    "email_host": {notify["emailHost"]!r},
    "email_port": {int(notify["emailPort"])!r},
    "email_user": {notify["emailUser"]!r},
    "email_to": {notify["emailTo"]!r},
    "email_recipients": {list(notify.get("recipients") or [])!r},
    "wechat_enabled": False,
    "pushplus_enabled": False,
}}

# ---- 邮件涨停列表排序配置 ----
# sort_by: change_pct(涨幅) / turnover(换手) / vol_ratio(量比) / pe(PE) / mcap(流通市值) / seal_time(封板)
# sort_order: asc / desc
ZT_SORT_CONFIG = {{
    "sort_by": {zt_sort["sortBy"]!r},
    "sort_order": {zt_sort["sortOrder"]!r},
}}

DATA_DIR = str(PROJECT_DIR := __import__("pathlib").Path(__file__).resolve().parent.parent / "data")
'''
    STRATEGY_FILE.write_text(content, encoding="utf-8")
