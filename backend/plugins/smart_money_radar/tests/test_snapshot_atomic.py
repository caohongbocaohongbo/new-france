"""P1-3 smart_money_radar 快照写读定向测试（调用真实业务写函数；离线、不联网、不动真实文件）。"""
import json
import threading

from backend.plugins.smart_money_radar import service as smr_service
from backend.plugins.smart_money_radar import orderbook as smr_orderbook


def test_write_latest_atomic_and_readable(monkeypatch, tmp_path):
    lat = tmp_path / "smart_money_radar_latest.json"
    monkeypatch.setattr(smr_service, "LATEST_FILE", lat)
    smr_service._write_latest({"status": "completed", "hits": [], "nan": float("nan")})
    assert lat.exists()
    data = json.loads(lat.read_text(encoding="utf-8"))
    assert data["status"] == "completed" and data["nan"] is None  # NaN 已清理
    assert not list(tmp_path.glob("*.tmp"))  # 原子写临时文件已清理
    assert smr_service.read_latest()["status"] == "completed"


def test_extension_snapshots_real_function(monkeypatch, tmp_path):
    """调用真实 _write_extension_snapshots：orderflow/auction 落盘为完整 JSON。"""
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(smr_service, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(smr_service, "_orderflow", smr_service._orderflow)
    store = type("S", (), {"minute_buckets": {"600001": {}}})()

    def _noop_summarize(buckets):
        return {"buy_vol": 1, "sell_vol": 0, "neutral_vol": 0, "count": 1}

    def _noop_wash(buckets):
        return 0

    monkeypatch.setattr(smr_service._orderflow, "summarize_minute", _noop_summarize)
    monkeypatch.setattr(smr_service._orderflow, "wash_trade_flag", _noop_wash)
    monkeypatch.setattr(smr_service._orderbook, "evaluate_pool", lambda *a, **k: [])
    monkeypatch.setattr(smr_service._orderbook, "write_orderbook_events", lambda rows, now: tmp_path / "events.json")
    monkeypatch.setattr(smr_service._orderbook, "write_orderbook_latest", lambda payload: None)
    now = datetime.now(timezone(timedelta(hours=8)))
    smr_service._write_extension_snapshots(store, now, [{"code": "600001", "name": "测试"}])
    of = json.loads((tmp_path / "orderflow_latest.json").read_text(encoding="utf-8"))
    assert of["status"] == "completed" and of["items"][0]["code"] == "600001"
    au = json.loads((tmp_path / "auction_latest.json").read_text(encoding="utf-8"))
    assert "status" in au  # 竞价快照完整 JSON
    assert not list(tmp_path.glob("*.tmp"))


def test_orderbook_real_writes_atomic(monkeypatch, tmp_path):
    monkeypatch.setattr(smr_orderbook, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(smr_orderbook, "EVENTS_DIR", tmp_path)
    monkeypatch.setattr(smr_orderbook, "ORDERBOOK_LATEST", tmp_path / "orderbook_latest.json")
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone(timedelta(hours=8)))
    path = smr_orderbook.write_orderbook_events([{"events": ["撤压"]}, {"events": []}], now)
    assert json.loads(path.read_text(encoding="utf-8"))["events"] == [{"events": ["撤压"]}]
    smr_orderbook.write_orderbook_latest({"status": "completed", "items": [1]})
    assert json.loads((tmp_path / "orderbook_latest.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_concurrent_read_during_write_always_valid_json(tmp_path):
    """P1-3 验收：写入过程中并发读取始终得到完整 JSON。"""
    from backend.services.snapshot_store import atomic_write_json

    path = tmp_path / "stress.json"
    atomic_write_json(path, {"v": "seed"})
    errors = []
    stop = threading.Event()

    def writer(n):
        while not stop.is_set():
            atomic_write_json(path, {"writer": n, "payload": "x" * 2000})

    def reader():
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(2)] + [threading.Thread(target=reader) for _ in range(4)]
    [t.start() for t in threads]
    import time

    time.sleep(0.5)
    stop.set()
    [t.join() for t in threads]
    assert not errors  # 任何时刻读到的都是完整 JSON
