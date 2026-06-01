"""配置 API — 策略参数、因子权重、通知配置。"""
import logging

from fastapi import APIRouter, HTTPException

from ..services.runtime_config import (
    ConfigValidationError,
    get_effective_config,
    save_effective_config,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/strategy")
async def get_strategy_config():
    """读取当前生效配置和上一次保存值。"""
    return get_effective_config()


@router.put("/strategy")
async def update_strategy_config(payload: dict):
    """保存策略配置，写入数据库并同步 config/strategy_params.py。"""
    try:
        return save_effective_config(payload)
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"保存策略配置失败: {exc}")
        raise HTTPException(status_code=500, detail=f"保存策略配置失败: {exc}") from exc
