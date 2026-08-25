"""邮件升级 · LLM 智能解读（Claude API + 规则降级）。

仅对 STRONG_BUY / BUY 生成。无 ANTHROPIC_API_KEY / 异常 / 超时 / JSON 解析失败
一律回落到规则文案，保证每只股都有解读。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# 同日同 code 缓存
_CACHE: dict = {}


def _cache_key(code: str) -> str:
    return f"{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}:{str(code).zfill(6)}"


def _top_factors(stock) -> list:
    factors = []
    for key, r in (getattr(stock, "factor_scores", {}) or {}).items():
        if key == "event_bonus":
            continue
        factors.append((getattr(r, "name", key), float(getattr(r, "score", 0) or 0)))
    factors.sort(key=lambda item: item[1], reverse=True)
    return factors


def rule_based_insight(stock) -> dict:
    """规则降级文案：用因子得分 top3 + action_plan 拼模板。"""
    top = _top_factors(stock)
    top_names = "、".join(name for name, _ in top[:3]) or "多因子"
    extra = getattr(stock, "extra", {}) or {}
    plan = extra.get("action_plan") or {}
    score = float(getattr(stock, "adjusted_score", 0) or 0)

    reason = f"多头因子{top_names}占优，综合评分{score:.0f}。"
    if plan.get("position"):
        reason += f"建议{plan.get('position')}关注。"

    stop = plan.get("stop_loss")
    stop_text = f"止损{stop}" if stop is not None else "严格止损"
    risk = f"回撤{getattr(stock, 'drop_pct', 0):+.2f}%，{stop_text}，破位即离场。"

    operation = "分批逢低关注，不追高；跌破止损无条件离场。"
    return {"reason": reason, "risk": risk, "operation": operation}


def _build_prompt(stock, backtest) -> str:
    lines = [
        "你是A股短线交易助手。根据以下因子数据给出简评，只输出 JSON，不含其他文字。",
        "",
        f"股票: {stock.name}({stock.code})",
        f"回撤: {getattr(stock, 'drop_pct', 0):+.2f}%  评分: {getattr(stock, 'adjusted_score', 0):.0f}  推荐: {getattr(stock, 'recommendation', '')}",
        "因子明细:",
    ]
    for key, r in (getattr(stock, "factor_scores", {}) or {}).items():
        if key == "event_bonus":
            continue
        lines.append(f"- {getattr(r, 'name', key)}: {getattr(r, 'score', 0):.1f}/10 {getattr(r, 'detail', '')}")
    extra = getattr(stock, "extra", {}) or {}
    plan = extra.get("action_plan")
    if plan:
        lines.append(
            f"操作计划: 买入区间{plan.get('buy_low')}-{plan.get('buy_high')} | "
            f"止损{plan.get('stop_loss')} | 目标{plan.get('target_1')}/{plan.get('target_2')} | "
            f"仓位{plan.get('position')}"
        )
    if backtest:
        win = backtest.get("win_rate") or {}
        lines.append(
            f"策略回测: 样本{backtest.get('sample_count', 0)} | "
            f"胜率 T+1={win.get(1)}% T+3={win.get(3)}% T+5={win.get(5)}%"
        )
    lines.append("")
    lines.append('请输出严格 JSON：{"reason":"推荐理由(≤60字)","risk":"风险提示(≤60字)","operation":"操作逻辑(≤60字)"}')
    return "\n".join(lines)


def _parse_json(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    result = {}
    for key in ("reason", "risk", "operation"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        result[key] = value.strip()
    return result


async def _call_llm(client, stock, backtest, model: str) -> Optional[dict]:
    prompt = _build_prompt(stock, backtest)
    try:
        resp = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=15,
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        return _parse_json(text)
    except Exception as exc:  # noqa: BLE001 任何异常都降级为规则文案
        logger.warning("[邮件升级] LLM 解读失败 %s: %s", stock.code, exc)
        return None


async def generate_insights(stocks: list, backtest: Optional[dict] = None) -> dict:
    """仅对 STRONG_BUY / BUY 生成 {code: {"reason","risk","operation"}}。"""
    targets = [s for s in stocks if getattr(s, "recommendation", "") in ("STRONG_BUY", "BUY")]
    if not targets:
        return {}

    model = DEFAULT_MODEL
    try:
        from .runtime_config import get_effective_config
        llm_cfg = get_effective_config()["config"].get("llm", {})
        model = str(llm_cfg.get("model") or DEFAULT_MODEL)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[邮件升级] 读取 llm.model 失败，使用默认模型: %s", exc)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.info("[邮件升级] 未配置 ANTHROPIC_API_KEY，LLM 解读降级为规则文案")
        return {s.code: rule_based_insight(s) for s in targets}

    try:
        import anthropic
    except ImportError as exc:
        logger.warning("[邮件升级] anthropic 未安装，降级为规则文案: %s", exc)
        return {s.code: rule_based_insight(s) for s in targets}

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=15.0, max_retries=0)
    semaphore = asyncio.Semaphore(3)
    stock_map = {s.code: s for s in targets}

    async def _one(stock):
        ck = _cache_key(stock.code)
        if ck in _CACHE:
            return stock.code, _CACHE[ck]
        async with semaphore:
            insight = await _call_llm(client, stock, backtest, model)
        if insight is None:
            insight = rule_based_insight(stock)
        _CACHE[ck] = insight
        return stock.code, insight

    results = await asyncio.gather(*(_one(s) for s in targets), return_exceptions=True)
    insights: dict = {}
    for item in results:
        if isinstance(item, Exception):
            continue
        code, insight = item
        insights[code] = insight
    # 兜底：并发结果缺失的（异常被 return_exceptions 吞掉的）补规则文案
    for s in targets:
        if s.code not in insights:
            insights[s.code] = rule_based_insight(s)
    return insights
