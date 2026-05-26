"""
Layer 3: RecommendationAgent — 输出推荐 + 通知
职责: 写入数据库、生成报告、发送邮件通知
约束: 唯一切面可写DB/文件/网络的Agent
"""
import os
import logging
from datetime import datetime, date
from typing import List, Optional
from pathlib import Path

from .notifier import send_notification, test_email
from .report_generator import generate_markdown_report, generate_html_report
from ..layer2_signal_engine.scoring import ScoredStock

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
REPORTS_DIR = PROJECT_DIR / "reports"


class RecommendationAgent:
    """输出推荐Agent — 负责持久化和通知"""

    async def execute(self, scored_stocks: List[ScoredStock],
                      target_date: date, index_gain: float = 0.0,
                      zt_list: list = None,
                      dry_run: bool = False,
                      audit_results: dict = None,
                      zt_meta: dict = None) -> dict:
        """
        执行完整输出流程
        Returns: {report_path, html_path, notify_result, summary}
        """
        if zt_list is None:
            zt_list = []
        if audit_results is None:
            audit_results = {}
        if zt_meta is None:
            zt_meta = {}
        date_str = target_date.strftime("%Y-%m-%d")
        logger.info(f"RecommendationAgent: 开始输出 {date_str}...")

        # 1. 生成 Markdown 报告
        md_path = self._ensure_path(REPORTS_DIR / f"{date_str}.md")
        generate_markdown_report(scored_stocks, target_date, index_gain, md_path,
                                 audit_results=audit_results)

        # 2. 生成 HTML 报告
        html_path = self._ensure_path(REPORTS_DIR / f"{date_str}.html")
        generate_html_report(scored_stocks, target_date, index_gain, html_path,
                             audit_results=audit_results)

        # 3. 发送邮件（无论有无推荐都发）
        notify_ok = True
        if not dry_run:
            notify_ok = send_notification(scored_stocks, target_date,
                                          index_gain, str(md_path),
                                          zt_list=zt_list,
                                          audit_results=audit_results,
                                          zt_meta=zt_meta)

        # 4. 统计摘要
        summary = {
            "date": date_str,
            "total_scored": len(scored_stocks),
            "strong_buy": sum(1 for s in scored_stocks if s.recommendation == "STRONG_BUY"),
            "buy": sum(1 for s in scored_stocks if s.recommendation == "BUY"),
            "watch": sum(1 for s in scored_stocks if s.recommendation == "WATCH"),
            "report_md": str(md_path),
            "report_html": str(html_path),
            "notified": notify_ok,
        }
        logger.info(f"  报告: {md_path}")
        logger.info(f"  通知: {'已发送' if notify_ok else '未发送/失败'}")
        return summary

    def _ensure_path(self, path: Path) -> Path:
        os.makedirs(path.parent, exist_ok=True)
        return path
