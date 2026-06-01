"""
数据库 ORM 模型 — SQLAlchemy
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import (Column, Integer, String, Float, Text, DateTime,
                        UniqueConstraint, ForeignKey, Index)
from .database import Base

BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now():
    return datetime.now(BEIJING_TZ)


class ScreeningTask(Base):
    """每日筛选任务"""
    __tablename__ = "screening_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_date = Column(String(10), unique=True, nullable=False)
    status = Column(String(20), default="pending")
    total_zt_count = Column(Integer, default=0)
    filtered_count = Column(Integer, default=0)
    scored_count = Column(Integer, default=0)
    strong_buy_count = Column(Integer, default=0)
    buy_count = Column(Integer, default=0)
    watch_count = Column(Integer, default=0)
    index_gain = Column(Float, default=0.0)
    report_md = Column(String(500))
    report_html = Column(String(500))
    error_message = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=beijing_now)


class StockScore(Base):
    """单只股票评分明细"""
    __tablename__ = "stock_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("screening_tasks.id"), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    zt_date = Column(String(10))
    ref_price = Column(Float)
    current_price = Column(Float)
    drop_pct = Column(Float)

    # 各因子得分
    score_pullback = Column(Float)
    score_volume_ratio = Column(Float)
    score_turnover = Column(Float)
    score_market_cap = Column(Float)
    score_pe = Column(Float)
    score_volume_trend = Column(Float)
    score_ma_alignment = Column(Float)
    score_strength = Column(Float)
    score_entry_point = Column(Float)
    score_zt_quality = Column(Float)
    score_event_bonus = Column(Float)

    total_score = Column(Float)
    adjusted_score = Column(Float)
    rank = Column(Integer)
    recommendation = Column(String(20))
    factor_details = Column(Text)  # JSON

    created_at = Column(DateTime, default=beijing_now)

    __table_args__ = (
        Index("idx_stock_scores_task", "task_id"),
        Index("idx_stock_scores_code", "code"),
    )


class WatchlistStock(Base):
    """监控列表"""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    zt_date = Column(String(10), nullable=False)
    ref_price = Column(Float, nullable=False)
    status = Column(String(20), default="active")
    added_at = Column(DateTime, default=beijing_now)
    expired_at = Column(DateTime)
    removed_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("code", "zt_date", name="uq_watchlist_code_date"),
        Index("idx_watchlist_status", "status"),
        Index("idx_watchlist_code", "code"),
    )


class StockEvent(Base):
    """事件数据"""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_date = Column(String(10), nullable=False)
    event_type = Column(String(30), nullable=False)
    title = Column(String(200))
    description = Column(Text)
    related_codes = Column(Text)  # JSON array
    impact = Column(String(20), default="neutral")
    impact_score = Column(Float, default=0)
    source = Column(String(100))
    source_url = Column(String(500))
    created_at = Column(DateTime, default=beijing_now)

    __table_args__ = (
        Index("idx_events_date", "event_date"),
        Index("idx_events_type", "event_type"),
    )


class SystemConfig(Base):
    """运行时配置表：保存前端策略页写入的真实业务参数"""
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(80), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)

    __table_args__ = (
        Index("idx_system_configs_key", "key"),
    )


def register_models():
    """确保所有模型已导入（供 init_db 使用）"""
    pass
