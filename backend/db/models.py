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


class NationalTeamHolding(Base):
    """国家队十大股东/十大流通股东披露持仓"""
    __tablename__ = "national_team_holdings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_key = Column(String(40), nullable=False)
    entity_name = Column(String(40), nullable=False)
    shareholder_name = Column(String(120), nullable=False)
    shareholder_type = Column(String(60))
    shares_type = Column(String(60))
    holder_rank = Column(Integer)
    stock_code = Column(String(6), nullable=False)
    stock_name = Column(String(50))
    report_period = Column(String(10), nullable=False)
    notice_date = Column(String(10))
    shares = Column(Float)
    share_ratio = Column(Float)
    shares_change = Column(Float)
    change_ratio = Column(Float)
    change_name = Column(String(30))
    market_value = Column(Float)
    holding_type = Column(String(30), nullable=False)
    holding_type_name = Column(String(30))
    source = Column(String(80), nullable=False)
    source_url = Column(String(500))
    fetched_at = Column(DateTime, default=beijing_now)
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)

    __table_args__ = (
        UniqueConstraint("entity_key", "stock_code", "shareholder_name", "report_period", "holding_type",
                         name="uq_national_team_holding"),
        Index("idx_national_team_entity", "entity_key"),
        Index("idx_national_team_period", "report_period"),
        Index("idx_national_team_stock", "stock_code"),
    )


class NationalTeamChange(Base):
    """国家队持仓披露变动"""
    __tablename__ = "national_team_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_key = Column(String(40), nullable=False)
    entity_name = Column(String(40), nullable=False)
    stock_code = Column(String(6), nullable=False)
    stock_name = Column(String(50))
    shareholder_name = Column(String(120), nullable=False)
    holding_type = Column(String(30), nullable=False)
    report_period = Column(String(10), nullable=False)
    previous_report_period = Column(String(10))
    change_type = Column(String(20), nullable=False)
    shares = Column(Float)
    previous_shares = Column(Float)
    shares_delta = Column(Float)
    change_ratio = Column(Float)
    source = Column(String(80), nullable=False)
    source_url = Column(String(500))
    detected_at = Column(DateTime, default=beijing_now)

    __table_args__ = (
        Index("idx_national_team_changes_entity", "entity_key"),
        Index("idx_national_team_changes_period", "report_period"),
    )


class NationalTeamEvent(Base):
    """国家队可溯源事件"""
    __tablename__ = "national_team_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_key = Column(String(40), nullable=False)
    entity_name = Column(String(40), nullable=False)
    event_date = Column(String(10), nullable=False)
    title = Column(String(200), nullable=False)
    summary = Column(Text)
    related_stock_code = Column(String(6))
    related_stock_name = Column(String(50))
    impact_level = Column(String(20), default="neutral")
    source = Column(String(80), nullable=False)
    source_url = Column(String(500))
    fetched_at = Column(DateTime, default=beijing_now)

    __table_args__ = (
        Index("idx_national_team_events_entity", "entity_key"),
        Index("idx_national_team_events_date", "event_date"),
    )


class NationalTeamSnapshot(Base):
    """国家队采集快照状态"""
    __tablename__ = "national_team_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_period = Column(String(10))
    raw_count = Column(Integer, default=0)
    matched_count = Column(Integer, default=0)
    source = Column(String(80), nullable=False)
    status = Column(String(20), default="success")
    error_message = Column(Text)
    fetched_at = Column(DateTime, default=beijing_now)

    __table_args__ = (
        Index("idx_national_team_snapshots_period", "report_period"),
        Index("idx_national_team_snapshots_status", "status"),
    )


def register_models():
    """确保所有模型已导入（供 init_db 使用）"""
    pass


# 新功能插件表集中注册（14 个方案的 SQLite 存储）。
# 注意：init_db 仅 import 本模块（不调用 register_models），故必须在模块级导入
# plugin_models，其 ORM 类才会注册到 Base.metadata 被 create_all 建表。
from . import plugin_models  # noqa: E402,F401
