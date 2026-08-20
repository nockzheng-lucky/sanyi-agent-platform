import json
from datetime import datetime

from app.engine.gate_reader import read_signals
from app.engine.poller import SignalPoller
from app.engine.session_clock import in_trading_session


def test_trading_sessions():
    now = datetime(2026, 8, 20, 9, 30)
    sessions = "09:00-10:15,10:30-11:30,13:30-15:00,21:00-02:30"
    assert in_trading_session(now, sessions) is True
    assert in_trading_session(datetime(2026, 8, 20, 12, 0), sessions) is False
    assert in_trading_session(datetime(2026, 8, 20, 22, 30), sessions) is True
    assert in_trading_session(datetime(2026, 8, 21, 1, 0), sessions) is True
    assert in_trading_session(datetime(2026, 8, 20, 10, 20), sessions) is False
    assert in_trading_session(datetime(2026, 8, 20, 10, 0), "") is True


def _write_registry(tmp_path, gates):
    path = tmp_path / "gate_registry.json"
    path.write_text(json.dumps({"gates": gates}, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_gate_reader_filters_target_signals(tmp_path):
    path = _write_registry(
        tmp_path,
        {
            "di_open": {
                "key": "di_open", "type": "di", "freq": "5m", "sym": "AU",
                "name": "黄金", "live_status": "已开", "formation": "门下",
                "gate_price": 100, "open_at": "2026-08-20 09:35:30",
                "t2_time": "2026-08-20 09:20:00",
            },
            "di_formation_above": {
                "key": "di_formation_above", "type": "di", "freq": "15m", "sym": "CU",
                "name": "沪铜", "live_status": "无动作·门上", "formation": "门上",
                "gate_price": 70000, "t2_time": "2026-08-20 09:30:00",
            },
            "di_wrong_freq": {
                "key": "di_wrong_freq", "type": "di", "freq": "1d", "sym": "AU",
                "live_status": "已开",
            },
            "tian": {
                "key": "tian", "type": "tian", "freq": "5m", "sym": "AU",
                "live_status": "已开",
            },
            "di_below": {
                "key": "di_below", "type": "di", "freq": "5m", "sym": "AU",
                "live_status": "无动作·门下",
            },
        },
    )

    events = read_signals(path)
    assert len(events) == 2
    statuses = {e["symbol"]: e["status"] for e in events}
    assert statuses == {"AU": "OPEN", "CU": "FORMATION_ABOVE"}
    assert events[0]["factor_key"] == "dimen_gate_signal"


def test_poller_baseline_then_new_event(tmp_path):
    path = _write_registry(
        tmp_path,
        {
            "baseline": {
                "key": "baseline", "type": "di", "freq": "5m", "sym": "AU",
                "live_status": "已开", "open_at": "2026-08-20 09:35:30",
            },
        },
    )
    poller = SignalPoller(path=path, interval_seconds=1, sessions="")
    import asyncio

    first = asyncio.run(poller.scan_once())
    assert first == []

    data = json.loads(open(path, encoding="utf-8").read())
    data["gates"]["new_open"] = {
        "key": "new_open", "type": "di", "freq": "15m", "sym": "CU",
        "live_status": "无动作·门上", "t2_time": "2026-08-20 10:15:30",
    }
    open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))

    second = asyncio.run(poller.scan_once())
    assert len(second) == 1
    assert second[0]["symbol"] == "CU"
    assert second[0]["status"] == "FORMATION_ABOVE"
