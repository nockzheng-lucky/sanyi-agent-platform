import asyncio
import json
import sqlite3
from datetime import datetime, timedelta

from app.engine.gate_reader import read_signals
from app.engine.poller import SignalPoller
from app.engine.session_clock import in_trading_session

NOW = datetime(2026, 8, 21, 0, 30)


def test_trading_sessions():
    now = datetime(2026, 8, 20, 9, 30)
    sessions = "09:00-11:30,13:00-15:00,21:00-02:30"
    assert in_trading_session(now, sessions) is True
    assert in_trading_session(datetime(2026, 8, 20, 12, 0), sessions) is False
    assert in_trading_session(datetime(2026, 8, 20, 22, 30), sessions) is True
    assert in_trading_session(datetime(2026, 8, 21, 1, 0), sessions) is True
    assert in_trading_session(datetime(2026, 8, 20, 10, 20), sessions) is True
    assert in_trading_session(datetime(2026, 8, 20, 10, 0), "") is True


def _create_db(tmp_path):
    path = tmp_path / "gate_events.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE gate_event ("
        "event_seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        "event_id TEXT NOT NULL UNIQUE,"
        "event_kind TEXT NOT NULL,"
        "event_at TEXT NOT NULL,"
        "payload_json TEXT NOT NULL,"
        "tscode TEXT,"
        "source_kind TEXT"
        ")"
    )
    conn.commit()
    return str(path), conn


def _payload(**overrides):
    payload = {
        "gate_type": "地门",
        "freq": "5m",
        "sym": "AU0",
        "tscode": None,
        "formation": "",
        "first_action": None,
        "edge": None,
        "gate_price": 100.0,
        "current_price": None,
        "text": "",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_gate_reader_filters_today_sql_events(tmp_path):
    path, conn = _create_db(tmp_path)
    rows = [
        ("open-1", "first-action", "2026-08-20T22:05:00",
         _payload(freq="15m", sym="CU0", tscode="CU2610.SHF", edge="open",
                  formation="门下", text="沪铜 15m 地门 门下已开门 门价70000")),
        ("formation-above", "formation", "2026-08-20T23:15:00",
         _payload(freq="5m", sym="AU0", tscode="AU2610.SHF",
                  text="5m 地门 沪金 门上 100")),
        ("formation-below", "formation", "2026-08-20T23:20:00",
         _payload(freq="5m", sym="AG0", text="5m 地门 白银 门下 200")),
        ("close", "first-action", "2026-08-20T23:25:00",
         _payload(freq="15m", sym="RB0", first_action="close", formation="门上",
                  text="螺纹 15m 地门 门上已关门")),
        ("tian", "formation", "2026-08-20T23:26:00",
         _payload(gate_type="天门", freq="5m", sym="M0", text="5m 天门 豆粕 门下")),
        ("wrong-freq", "formation", "2026-08-20T23:27:00",
         _payload(freq="1d", sym="TA0", text="1d 地门 PTA 门上")),
        ("yesterday-before-night", "formation", "2026-08-19T20:00:00",
         _payload(freq="15m", sym="ZN0", text="15m 地门 沪锌 门上")),
    ]
    for event_id, kind, event_at, payload in rows:
        conn.execute(
            "INSERT INTO gate_event(event_id,event_kind,event_at,payload_json)"
            " VALUES (?,?,?,?)",
            (event_id, kind, event_at, payload),
        )
    conn.commit()

    events = read_signals(path, now=NOW)
    assert len(events) == 2
    by_id = {e["event_id"]: e for e in events}
    assert by_id["open-1"]["status"] == "OPEN"
    assert by_id["open-1"]["frequency"] == "15m"
    assert by_id["open-1"]["contract"] == "CU2610.SHF"
    assert by_id["formation-above"]["status"] == "FORMATION_ABOVE"
    assert by_id["formation-above"]["frequency"] == "5m"
    assert by_id["formation-above"]["contract"] == "AU2610.SHF"


def test_poller_dedupes_by_event_id(tmp_path):
    import uuid

    uid = uuid.uuid4().hex[:8]
    open_id = "open-" + uid
    formation_id = "formation-" + uid
    now = datetime.now()
    open_at = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    formation_at = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    path, conn = _create_db(tmp_path)
    conn.execute(
        "INSERT INTO gate_event(event_id,event_kind,event_at,payload_json)"
        " VALUES (?,?,?,?)",
        (open_id, "first-action", open_at,
         _payload(first_action="open", formation="门下", text="5m 地门 沪金 已开")),
    )
    conn.commit()

    poller = SignalPoller(path=path, interval_seconds=1, sessions="")
    first = asyncio.run(poller.scan_once())
    assert [e["event_id"] for e in first] == [open_id]

    second = asyncio.run(poller.scan_once())
    assert second == []

    conn.execute(
        "INSERT INTO gate_event(event_id,event_kind,event_at,payload_json)"
        " VALUES (?,?,?,?)",
        (formation_id, "formation", formation_at,
         _payload(freq="15m", sym="ZN0", text="15m 地门 沪锌 门上")),
    )
    conn.commit()
    third = asyncio.run(poller.scan_once())
    assert [e["event_id"] for e in third] == [formation_id]
