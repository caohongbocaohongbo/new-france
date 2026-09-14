"""影子运行观测聚合（§12.4 第5步，代码侧）离线单测。"""
import backend.services.data_backend.shadow_run as sr


def test_shadow_run_record_and_summarize(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "SHADOW_FILE", tmp_path / "shadow.json")
    monkeypatch.setenv("SHADOW_RUN_ENABLED", "1")

    sr.record("quotes", "sina", "ok", coverage={"received": 5, "truncated": False},
              age_seconds=3.0, deadline_met=True)
    sr.record("quotes", "sina", "ok", age_seconds=9.0, deadline_met=True)
    sr.record("quotes", "tencent", "error", error="timeout", deadline_met=False)

    s = sr.summarize()
    assert s["quotes@sina"]["runs"] == 2
    assert s["quotes@sina"]["error_rate"] == 0.0
    assert s["quotes@sina"]["age_p95_seconds"] == 9.0
    assert s["quotes@sina"]["deadline_met_rate"] == 1.0
    assert s["quotes@tencent"]["error_rate"] == 1.0
    assert "timeout" in s["quotes@tencent"]["error_types"]

    sr.reset()
    assert sr.summarize() == {}
