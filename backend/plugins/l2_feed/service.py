"""12 真实 L2 升级路径：M1 账号/接口调研（产出调研报告，采集端后置）。"""
import logging
from datetime import datetime

from backend.plugins.common import BEIJING_TZ, read_snapshot, write_snapshot

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "l2_research"

# 候选数据源对比（来自产品方案 §3.1）
SOURCE_COMPARISON = [
    {"source": "QMT / miniQMT（迅投）", "capability": "逐笔委托/逐笔成交/十档/队列/撤单", "cost": "券商开户+资金门槛，本地运行", "fit": "高频/实盘自动交易"},
    {"source": "PTrade（券商）", "capability": "类似 QMT，策略托管", "cost": "券商申请", "fit": "量化托管"},
    {"source": "同花顺/东财 L2", "capability": "十档/逐笔/队列/大单统计", "cost": "订阅费+授权限制", "fit": "纯研究"},
    {"source": "Tushare Pro", "capability": "资金流/龙虎榜/日频财务（无 L2 逐笔）", "cost": "积分/订阅", "fit": "已作可选源"},
]


def build_research_report() -> dict:
    """返回 M1 调研结论与推荐架构。"""
    return {
        "milestone": "M1 账号/接口调研",
        "conclusion": "QMT/miniQMT 作为本地采集端接入，免费栈继续跑不替换",
        "recommended_architecture": "QMT 只作采集端，跑本地国内网络；新增 backend/plugins/l2_feed/ 采集后落本地 Parquet/SQLite，再聚合快照",
        "free_stack_relation": "免费栈继续跑：全市场资金流/涨停池/盘后筛选不变；L2 只覆盖重点观察池避免海量存储",
        "sources": SOURCE_COMPARISON,
        "storage": {"raw": "data/l2/{date}/{code}.parquet", "cloud": "只同步聚合快照，不同步原始 tick"},
        "risks": ["券商合规门槛", "L2 数据量大只限观察池", "数据授权可能禁再分发"],
        "next_milestone": "M2 采集端（确认需求后施工）",
    }


def run_l2_research(force: bool = False) -> dict:
    """产出/刷新 M1 调研报告快照。"""
    now = datetime.now(BEIJING_TZ)
    report = build_research_report()
    payload = {"status": "completed", "now": now.isoformat(), **report}
    write_snapshot(SNAPSHOT_NAME, payload)
    return payload


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "milestone": "M1", "sources": []}
