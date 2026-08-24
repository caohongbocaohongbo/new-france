from datetime import datetime, timezone, timedelta
import math

from backend.plugins.smart_money_radar.replay import RadarReplay


def test_replay_dump_and_query_are_ordered_and_json_safe(tmp_path):
    replay = RadarReplay(tmp_path / "radar.sqlite3")
    replay.dump([
        {"time": "2026-08-18T10:31:00+08:00", "code": "600002", "stage": "潜伏", "smart_money_score": 70, "metrics": {"x": 1}},
        {"time": "2026-08-18T10:30:00+08:00", "code": "600001", "stage": "吸筹确认", "smart_money_score": 80, "metrics": {"x": 2}},
    ])
    rows = replay.query(code="600001")
    assert len(rows) == 1
    assert rows[0]["stage"] == "吸筹确认"
    assert rows[0]["metrics"]["x"] == 2


def test_replay_empty_dump_does_not_create_rows(tmp_path):
    replay = RadarReplay(tmp_path / "radar.sqlite3")
    replay.dump([])
    assert replay.query() == []


def test_replay_dump_converts_non_finite_metric_values_to_null(tmp_path):
    replay = RadarReplay(tmp_path / "radar.sqlite3")
    replay.dump([{
        "time": "2026-08-18T10:31:00+08:00",
        "code": "600001",
        "metrics": {"nan": math.nan, "positive_inf": math.inf, "negative_inf": -math.inf},
    }])

    metrics = replay.query(code="600001")[0]["metrics"]

    assert metrics == {"nan": None, "positive_inf": None, "negative_inf": None}
