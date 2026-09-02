"""新功能插件共享 SQLite 表（14 个方案的数据存储）。

所有表集中于此，由 init_db() -> Base.metadata.create_all 统一创建。
各插件通过 backend.plugins.common 的 db 辅助函数做最佳努力写入，
主读取路径为 reports/<name>_latest.json 快照（秒开），SQLite 供历史/明细查询。
"""
from sqlalchemy import Column, Float, Integer, String, Text, Index

from backend.db.database import Base


class LimitUpDaily(Base):
    """01 情绪周期：每日涨停池明细。"""
    __tablename__ = "limit_up_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    board_height = Column(Integer, default=0)
    seal_time = Column(Integer)
    break_count = Column(Integer, default=0)
    first_seal_time = Column(Integer)
    industry = Column(String(50))
    concept = Column(String(200))
    is_trap = Column(Integer, default=0)
    __table_args__ = (Index("idx_lu_date", "date"), Index("idx_lu_height", "date", "board_height"))


class EmotionDaily(Base):
    """01 情绪周期：每日情绪指标与温度。"""
    __tablename__ = "emotion_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), unique=True, nullable=False)
    metrics_json = Column(Text, nullable=False)
    score = Column(Float)
    regime = Column(String(20))
    created_at = Column(String(30))


class LhbDaily(Base):
    """02 龙虎榜：每日上榜个股。"""
    __tablename__ = "lhb_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    reason = Column(String(200))
    net_buy = Column(Float)
    buy_inst = Column(Integer)
    sell_inst = Column(Integer)
    lhb_type = Column(String(30))
    __table_args__ = (Index("idx_lhb_date", "date"), Index("idx_lhb_code_date", "code", "date"))


class LhbSeats(Base):
    """02 龙虎榜：席位明细。"""
    __tablename__ = "lhb_seats"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    seat_name = Column(String(120), nullable=False)
    normalized_name = Column(String(120))
    side = Column(String(10))
    amount = Column(Float)
    seat_type = Column(String(30))
    match_confidence = Column(Float)
    __table_args__ = (Index("idx_lhb_seats_date", "date"), Index("idx_lhb_seats_norm", "normalized_name"))


class SeatStats(Base):
    """02 龙虎榜：席位画像统计。"""
    __tablename__ = "seat_stats"
    id = Column(Integer, primary_key=True, autoincrement=True)
    seat_name = Column(String(120), unique=True, nullable=False)
    win_rate_t1 = Column(Float)
    win_rate_t3 = Column(Float)
    win_rate_t5 = Column(Float)
    avg_return = Column(Float)
    prefer_board = Column(String(50))
    prefer_theme = Column(String(200))
    updated_at = Column(String(30))


class OaSample(Base):
    """03 T+1 溢价样本库。"""
    __tablename__ = "oa_sample"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    features_json = Column(Text, nullable=False)
    label_t1_open = Column(Float)
    label_t1_high = Column(Float)
    label_t1_close = Column(Float)
    label_t1_low = Column(Float)
    score_bucket = Column(String(20))
    __table_args__ = (Index("idx_oa_sample_date", "date"), Index("idx_oa_sample_code", "code"))


class OaCalibration(Base):
    """03 校准结果版本化。"""
    __tablename__ = "oa_calibration"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    model_version = Column(String(30))
    metrics_json = Column(Text, nullable=False)


class IntradayQuoteSnapshot(Base):
    """04 尾盘抢筹：盘中 5 分钟行情快照。"""
    __tablename__ = "intraday_quote_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    hhmm = Column(String(5), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    change_pct = Column(Float)
    amount = Column(Float)
    volume_ratio = Column(Float)
    turnover = Column(Float)
    main_inflow = Column(Float)
    main_inflow_ratio = Column(Float)
    __table_args__ = (Index("idx_iq", "date", "hhmm"), Index("idx_iq_code_date", "code", "date"))


class BoardDaily(Base):
    """05 板块轮动：每日板块热度。"""
    __tablename__ = "board_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    board_code = Column(String(30), nullable=False)
    board_name = Column(String(60))
    board_type = Column(String(20))
    change_pct = Column(Float)
    net_inflow = Column(Float)
    zt_count = Column(Integer, default=0)
    max_height = Column(Integer, default=0)
    zt_ratio = Column(Float)
    score = Column(Float)
    stage = Column(String(20))
    __table_args__ = (Index("idx_board_date", "board_code", "date"),)


class BoardStockDaily(Base):
    """05 板块轮动：板块-成分股关系。"""
    __tablename__ = "board_stock_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    board_code = Column(String(30), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    is_zt = Column(Integer, default=0)
    is_leader = Column(Integer, default=0)
    height = Column(Integer, default=0)
    __table_args__ = (Index("idx_board_stock", "code"),)


class FactorDaily(Base):
    """06 因子实验室：因子面板。"""
    __tablename__ = "factor_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    factor_name = Column(String(40), nullable=False)
    factor_value = Column(Float)
    forward_ret_t1 = Column(Float)
    forward_ret_t3 = Column(Float)
    forward_ret_t5 = Column(Float)
    __table_args__ = (Index("idx_factor", "date", "factor_name"), Index("idx_factor_code", "code", "factor_name"))


class OrderflowFeatures(Base):
    """08 逐笔成交：分钟级行为聚合。"""
    __tablename__ = "orderflow_features"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    minute = Column(String(5), nullable=False)
    buy_vol = Column(Float)
    sell_vol = Column(Float)
    neutral_vol = Column(Float)
    large_buy = Column(Float)
    large_sell = Column(Float)
    attack_buy_count = Column(Integer)
    split_large_flag = Column(Integer)
    __table_args__ = (Index("idx_orderflow", "date", "code", "minute"),)


class TierFlowSnapshot(Base):
    """09 大单分层资金流快照。"""
    __tablename__ = "tier_flow_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    hhmm = Column(String(5), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    super_net = Column(Float)
    big_net = Column(Float)
    mid_net = Column(Float)
    small_net = Column(Float)
    smart_ratio = Column(Float)
    vwap_large = Column(Float)
    state = Column(String(20))
    __table_args__ = (Index("idx_tier_flow", "date", "hhmm"), Index("idx_tier_flow_code", "code", "date"))


class ZtSealSnapshot(Base):
    """10 涨停封单快照。"""
    __tablename__ = "zt_seal_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    hhmm = Column(String(5), nullable=False)
    code = Column(String(6), nullable=False)
    seal_amount = Column(Float)
    seal_vol = Column(Float)
    break_count = Column(Integer)
    first_seal_time = Column(Integer)
    last_seal_time = Column(Integer)
    is_sealed = Column(Integer)
    __table_args__ = (Index("idx_zt_seal", "date", "hhmm"), Index("idx_zt_seal_code", "code", "date"))


class PriceDistribution(Base):
    """11 分价成交分布。"""
    __tablename__ = "price_distribution"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    price_level = Column(Float, nullable=False)
    volume = Column(Float)
    cumulative_ratio = Column(Float)
    __table_args__ = (Index("idx_price_dist", "date", "code"),)


class L2OrderbookSnapshot(Base):
    """12 真实 L2：十档快照。"""
    __tablename__ = "l2_orderbook_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(String(30), nullable=False)
    code = Column(String(6), nullable=False)
    levels_json = Column(Text, nullable=False)
    __table_args__ = (Index("idx_l2_ob", "code", "ts"),)


class L2Tick(Base):
    """12 真实 L2：逐笔成交。"""
    __tablename__ = "l2_tick"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(String(30), nullable=False)
    code = Column(String(6), nullable=False)
    price = Column(Float)
    vol = Column(Float)
    side = Column(Integer)
    order_id = Column(String(40))
    __table_args__ = (Index("idx_l2_tick", "code", "ts"),)


class ResonanceSnapshot(Base):
    """15 四维共振信号快照。"""
    __tablename__ = "resonance_snapshot"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    hhmm = Column(String(5), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    resonance_score = Column(Float)
    signal = Column(String(10))
    d1_score = Column(Float)
    d1_state = Column(String(20))
    d2_score = Column(Float)
    d3_score = Column(Float)
    d4_score = Column(Float)
    active_source = Column(String(30))
    degraded = Column(Integer, default=0)
    # 17 盘中实时化：1=用了实时价计算 D2，0=日线；1=已推送盘中通知
    d2_realtime = Column(Integer, default=0)
    notified = Column(Integer, default=0)
    __table_args__ = (Index("idx_resonance", "date", "signal"), Index("idx_resonance_code", "code", "date"))


class LowPositionHit(Base):
    """16 低位涨停选股器命中。"""
    __tablename__ = "low_position_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    price = Column(Float)
    pullback_pct = Column(Float)
    price_percentile = Column(Float)
    below_ma20 = Column(Integer)
    last_zt_date = Column(String(10))
    zt_count_250d = Column(Integer)
    market_cap = Column(Float)
    turnover = Column(Float)
    total_amount = Column(Float)
    low_score = Column(Float)
    zt_source = Column(String(30))
    __table_args__ = (Index("idx_low_pos", "date"), Index("idx_low_pos_code", "code", "date"))

