"""multi_hit_notifier 多命中🔥邮件推送单测（离线）。"""
from datetime import datetime, timedelta, timezone

from backend.plugins import multi_hit_notifier as mh

BEIJING_TZ = timezone(timedelta(hours=8))


def _now(hh=14, mm=30):
    return datetime(2026, 9, 4, hh, mm, tzinfo=BEIJING_TZ)


def _hits():
    return [
        {"code": "600000", "name": "浦发银行", "price": 10.5, "hit_count": 3, "tech_score": 88.0},
        {"code": "000001", "name": "平安银行", "price": 12.0, "hit_count": 2, "tech_score": 75.0},
    ]


def test_build_multi_hit_email():
    subject, text, html = mh.build_multi_hit_email(
        "经典技术指标", "多指标共振", _hits(), _now(),
        [("name", "名称"), ("hit_count", "命中数"), ("tech_score", "综合分")])
    assert "🔥" in subject
    assert "600000" in text and "浦发银行" in text
    assert "<table" in html and "600000" in html


def test_push_multi_hit_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "DATA_DIR", tmp_path)
    calls = []

    def fake_notifier(plugin_label, signal_label, hits, now, cols):
        calls.append(list(hits))
        return True, None

    cols = [("name", "名称"), ("tech_score", "综合分")]
    sent, notified, err = mh.push_multi_hit(
        "tech", "经典技术指标", "多指标共振", _hits(), _now(), 30, cols, notifier=fake_notifier)
    assert sent is True and err is None
    assert notified == {"600000", "000001"}
    assert len(calls) == 1 and len(calls[0]) == 2

    # 同一 code 30min 内重推 → should_notify False → 不再发送
    sent2, notified2, err2 = mh.push_multi_hit(
        "tech", "经典技术指标", "多指标共振", _hits(), _now(14, 40), 30, cols, notifier=fake_notifier)
    assert sent2 is False and notified2 == set() and err2 is None
    assert len(calls) == 1  # 未新增发送

    # 超 30min 后 → 再发送
    sent3, notified3, _ = mh.push_multi_hit(
        "tech", "经典技术指标", "多指标共振",
        [{"code": "600000", "name": "浦发银行", "price": 10.6, "tech_score": 90.0}],
        _now(15, 40), 30, cols, notifier=fake_notifier)
    assert sent3 is True and notified3 == {"600000"}
    assert len(calls) == 2


def test_push_multi_hit_no_new():
    sent, notified, err = mh.push_multi_hit(
        "tech", "经典技术指标", "多指标共振", [], _now(), 30, [("name", "名称")], notifier=None)
    assert sent is False and notified == set() and err is None


def test_notified_map_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "DATA_DIR", tmp_path)
    today = _now().date()
    mh.save_notified_map("tech", today, {"600000": ["2026-09-04T14:30:00+08:00"]})
    loaded = mh.load_notified_map("tech", today)
    assert loaded == {"600000": ["2026-09-04T14:30:00+08:00"]}
