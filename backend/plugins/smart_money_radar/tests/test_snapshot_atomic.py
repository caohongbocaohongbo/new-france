"""P1 迁移后 smart_money_radar 快照写入/读取定向测试（离线，不联网、不动真实文件）。"""
import json

from backend.plugins.smart_money_radar import service as smr_service


def test_write_latest_atomic_and_readable(monkeypatch, tmp_path):
    lat = tmp_path / "smart_money_radar_latest.json"
    monkeypatch.setattr(smr_service, "LATEST_FILE", lat)
    smr_service._write_latest({"status": "completed", "hits": [], "nan": float("nan")})
    assert lat.exists()
    data = json.loads(lat.read_text(encoding="utf-8"))
    assert data["status"] == "completed" and data["nan"] is None  # NaN 已清理
    assert not list(tmp_path.glob("*.tmp"))  # 原子写临时文件已清理
    monkeypatch.setattr(smr_service, "LATEST_FILE", lat)
    assert smr_service.read_latest()["status"] == "completed"


def test_extension_snapshot_writes_atomic(monkeypatch, tmp_path):
    """07/08/13 扩展快照（orderflow/auction）经 atomic_write_json 落盘，永不半写。"""
    from backend.services.snapshot_store import atomic_write_json

    of = tmp_path / "orderflow_latest.json"
    atomic_write_json(of, {"status": "no_data", "items": []})
    assert json.loads(of.read_text(encoding="utf-8"))["status"] == "no_data"
