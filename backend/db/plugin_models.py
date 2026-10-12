"""新功能插件共享 SQLite 表（14 个方案的数据存储）。

所有表集中于此，由 init_db() -> Base.metadata.create_all 统一创建。
各插件通过 backend.plugins.common 的 db 辅助函数做最佳努力写入，
主读取路径为 reports/<name>_latest.json 快照（秒开），SQLite 供历史/明细查询。
"""
from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text, Index, UniqueConstraint

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


class TechIndicatorHit(Base):
    """18 经典技术指标选股命中。"""
    __tablename__ = "tech_indicator_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    price = Column(Float)
    macd_golden = Column(Integer, default=0)
    kdj_golden = Column(Integer, default=0)
    rsi_oversold = Column(Integer, default=0)
    boll_rebound = Column(Integer, default=0)
    hit_count = Column(Integer)
    tech_score = Column(Float)
    multi_hit = Column(Integer, default=0)
    macd_dif = Column(Float)
    macd_dea = Column(Float)
    kdj_k = Column(Float)
    kdj_d = Column(Float)
    kdj_j = Column(Float)
    rsi = Column(Float)
    boll_mb = Column(Float)
    boll_ub = Column(Float)
    boll_lb = Column(Float)
    __table_args__ = (Index("idx_ti_date", "date"), Index("idx_ti_code", "code", "date"), Index("idx_ti_multi", "date", "multi_hit"))


class TrendStrengthHit(Base):
    """19 趋势强度选股命中。"""
    __tablename__ = "trend_strength_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    price = Column(Float)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    ma60 = Column(Float)
    ma_aligned = Column(Integer, default=0)
    prev_high_60 = Column(Float)
    new_high = Column(Integer, default=0)
    high_break_pct = Column(Float)
    volume_ratio = Column(Float)
    trend_leader = Column(Integer, default=0)
    trend_score = Column(Float)
    __table_args__ = (Index("idx_ts_date", "date"), Index("idx_ts_code", "code", "date"), Index("idx_ts_leader", "date", "trend_leader"))


class PatternHit(Base):
    """20 形态突破选股命中。"""
    __tablename__ = "pattern_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    price = Column(Float)
    platform_high = Column(Float)
    break_pct = Column(Float)
    platform_break = Column(Integer, default=0)
    gap_hold = Column(Integer, default=0)
    dual_hit = Column(Integer, default=0)
    gap_pct = Column(Float)
    gap_type = Column(String(20))
    gap_low = Column(Float)
    volume_ratio = Column(Float)
    pattern_score = Column(Float)
    __table_args__ = (Index("idx_pattern_date", "date"), Index("idx_pattern_code", "code", "date"), Index("idx_pattern_dual", "date", "dual_hit"))


class ChipHit(Base):
    """21 筹码集中度与获利盘选股命中。"""
    __tablename__ = "chip_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    price = Column(Float)
    concentration_ratio = Column(Float)
    p05 = Column(Float)
    p50 = Column(Float)
    p95 = Column(Float)
    profit_ratio = Column(Float)
    ma20_slope = Column(Float)
    concentrated = Column(Integer, default=0)
    high_profit = Column(Integer, default=0)
    trend_up = Column(Integer, default=0)
    tight_control = Column(Integer, default=0)
    chip_score = Column(Float)
    __table_args__ = (Index("idx_chip_date", "date"), Index("idx_chip_code", "code", "date"), Index("idx_chip_tight", "date", "tight_control"))


class SmartPickerHit(Base):
    """22 智能选股聚合中枢：每日聚合命中（快照为主读，SQLite 供本地历史）。"""
    __tablename__ = "smart_picker_hits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    price = Column(Float)
    change_pct = Column(Float)
    total_amount = Column(Float)
    hub_score = Column(Float)
    hub_score_pct = Column(Float)
    hit_strategies = Column(Integer)
    resonance = Column(Integer, default=0)
    tech_hit = Column(Integer, default=0)
    trend_hit = Column(Integer, default=0)
    pattern_hit = Column(Integer, default=0)
    chip_hit = Column(Integer, default=0)
    badges_json = Column(Text)
    raw_json = Column(Text)
    __table_args__ = (Index("idx_sp_date", "date"), Index("idx_sp_code", "code", "date"), Index("idx_sp_reso", "date", "resonance"))


class PickerPerfDaily(Base):
    """22 信号质量追踪：每日信号 T+1/T+3/T+5 收益（无前视回填）。"""
    __tablename__ = "picker_perf_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    strategies = Column(String(50))
    hub_score = Column(Float)
    close_entry = Column(Float)
    t1_close = Column(Float)
    t1_ret = Column(Float)
    t1_filled = Column(Integer, default=0)
    t3_close = Column(Float)
    t3_ret = Column(Float)
    t3_filled = Column(Integer, default=0)
    t5_close = Column(Float)
    t5_ret = Column(Float)
    t5_filled = Column(Integer, default=0)
    data_missing = Column(Integer, default=0)
    updated_at = Column(String(30))
    __table_args__ = (Index("idx_ppd_date", "signal_date"), Index("idx_ppd_code", "code", "signal_date"))

# ===================== 26 方案：五大功能新表 =====================

class PaperAccount(Base):
    """26 绩效模拟盘：虚拟账户。"""
    __tablename__ = "paper_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False, default="main")
    initial_capital = Column(Float, default=100000.0)
    cash = Column(Float, default=100000.0)
    updated_at = Column(String(30))


class PaperPosition(Base):
    """26 绩效模拟盘：持仓。"""
    __tablename__ = "paper_positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    level = Column(String(20))
    open_date = Column(String(10), nullable=False)
    open_price = Column(Float, nullable=False)
    shares = Column(Integer, nullable=False)
    cost = Column(Float)
    status = Column(String(20), default="open")
    close_date = Column(String(10))
    close_price = Column(Float)
    pnl = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)
    reason = Column(String(200))
    __table_args__ = (Index("idx_pp_acct", "account_id"), Index("idx_pp_status", "status"))


class PaperNav(Base):
    """26 绩效模拟盘：每日净值。"""
    __tablename__ = "paper_nav"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    trade_date = Column(String(10), nullable=False)
    nav = Column(Float)
    daily_return = Column(Float, default=0.0)
    drawdown = Column(Float, default=0.0)
    __table_args__ = (Index("idx_pn_acct_date", "account_id", "trade_date"),)


class PaperTrade(Base):
    """26 绩效模拟盘：交易流水。"""
    __tablename__ = "paper_trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    side = Column(String(10), nullable=False)
    price = Column(Float)
    shares = Column(Integer)
    trade_date = Column(String(10), nullable=False)
    reason = Column(String(200))
    __table_args__ = (Index("idx_pt_acct_date", "account_id", "trade_date"),)


class WatchRule(Base):
    """26 条件盯盘：监控规则。"""
    __tablename__ = "watch_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    product_type = Column(String(20), default="stock")
    condition = Column(Text, nullable=False)
    variables_json = Column(Text)
    channels_json = Column(Text)
    enabled = Column(Integer, default=1)
    last_trigger_key = Column(String(200))
    created_at = Column(String(30))
    __table_args__ = (Index("idx_wr_code", "code"), Index("idx_wr_enabled", "enabled"))


class WatchAlert(Base):
    """26 条件盯盘：触发记录。"""
    __tablename__ = "watch_alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    condition = Column(Text)
    triggered_at = Column(String(30))
    price = Column(Float)
    change_pct = Column(Float)
    message = Column(Text)
    dedup_key = Column(String(200), unique=True)
    notified = Column(Integer, default=0)
    __table_args__ = (Index("idx_wa_rule", "rule_id"), Index("idx_wa_code_date", "code", "triggered_at"))


class HotScan(Base):
    """26 舆情热度聚合：每日扫描结果。"""
    __tablename__ = "hot_scan"
    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_date = Column(String(10), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    score = Column(Integer, default=0)
    sources_json = Column(Text)
    rank = Column(Integer)
    in_watchlist = Column(Integer, default=0)
    created_at = Column(String(30))
    __table_args__ = (Index("idx_hs_date_rank", "scan_date", "rank"), Index("idx_hs_code", "code", "scan_date"))


class EventScan(Base):
    """26 事件驱动雷达：事件扫描结果。"""
    __tablename__ = "event_scan"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    event_type = Column(String(30), nullable=False)
    title = Column(String(200))
    source = Column(String(80))
    impact_score = Column(Integer, default=0)
    event_date = Column(String(10), nullable=False)
    url = Column(String(500))
    dedup_key = Column(String(200), unique=True)
    in_watchlist = Column(Integer, default=0)
    created_at = Column(String(30))
    __table_args__ = (Index("idx_es_date", "event_date"), Index("idx_es_code", "code", "event_date"), Index("idx_es_type", "event_type"))


# ===================== 28 方案：低位竞价雷达（端到端验收链） =====================

class AuctionRun(Base):
    """28 低位竞价雷达：每日 run 主状态 + 六里程碑明细。"""
    __tablename__ = "auction_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(String(10), nullable=False)
    strategy_version = Column(String(40), nullable=False)
    revision = Column(Integer, default=1, nullable=False)
    phase = Column(String(30))
    state = Column(String(30))
    pool_hash = Column(String(64))
    factor_spec_version = Column(String(40))
    config_hash = Column(String(64))
    code_commit = Column(String(64))
    snapshot_version = Column(String(64))
    started_at = Column(String(30))
    confirmed_at = Column(String(30))
    published_at = Column(String(30))
    milestones_json = Column(Text)
    last_error = Column(Text)
    __table_args__ = (
        UniqueConstraint("trade_date", "strategy_version", "revision", name="uq_auction_runs"),
        Index("idx_auction_runs_date_state", "trade_date", "state"),
    )


class AuctionCandidate(Base):
    """28 低位竞价雷达：每日候选评分与证据。"""
    __tablename__ = "auction_candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("auction_runs.id"), nullable=False)
    code = Column(String(6), nullable=False)
    name = Column(String(50))
    low_score = Column(Float)
    auction_score = Column(Float)
    total_score = Column(Float)
    quality = Column(String(20))
    coverage = Column(Float)
    tradability = Column(String(40))
    source = Column(String(30))
    source_timestamp = Column(String(30))
    excluded_reason = Column(Text)
    confirmed_fields_json = Column(Text)
    evidence_json = Column(Text)
    rank = Column(Integer)
    price = Column(Float)
    last_close = Column(Float)
    gap = Column(Float)
    volume_ratio = Column(Float)
    turnover = Column(Float)
    __table_args__ = (
        UniqueConstraint("run_id", "code", name="uq_auction_cand_code"),
        UniqueConstraint("run_id", "rank", name="uq_auction_cand_rank"),
        Index("idx_auction_cand_code", "code"),
    )


class AuctionOutbox(Base):
    """28 低位竞价雷达：邮件 Outbox 任务。"""
    __tablename__ = "auction_outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(120), nullable=False)
    run_id = Column(Integer, ForeignKey("auction_runs.id"), nullable=False)
    channel = Column(String(20), nullable=False)
    recipient_key = Column(String(120), nullable=False)
    status = Column(String(30), default="WAITING_PUBLISH")
    attempts = Column(Integer, default=0)
    next_attempt_at = Column(String(30))
    sending_started_at = Column(String(30))
    last_attempt_at = Column(String(30))
    accepted_at = Column(String(30))
    updated_at = Column(String(30))
    last_error = Column(Text)
    payload_json = Column(Text)
    created_at = Column(String(30))
    __table_args__ = (
        UniqueConstraint("event_key", "channel", "recipient_key", name="uq_auction_outbox"),
        Index("idx_auction_outbox_status", "status", "next_attempt_at"),
    )


