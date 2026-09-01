"""12 L2 升级路径 M1 调研单测（离线）。"""
from backend.plugins.l2_feed.service import build_research_report


def test_research_report_structure():
    report = build_research_report()
    assert report["milestone"] == "M1 账号/接口调研"
    assert len(report["sources"]) == 4
    assert any(s["source"].startswith("QMT") for s in report["sources"])
    assert "data/l2/{date}/{code}.parquet" in report["storage"]["raw"]
