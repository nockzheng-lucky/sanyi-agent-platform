import uuid

from app.db import upsert_signal_event


def _event(symbol="AU", event_id=None):
    return {
        "event_id": event_id or ("t-" + uuid.uuid4().hex[:12]),
        "factor_key": "dimen_gate_signal",
        "symbol": symbol,
        "frequency": "5m",
        "status": "OPEN",
        "formation": "门下",
        "gate_price": 100.0,
        "current_price": 101.5,
        "open_at": "2026-08-20T09:35:30+08:00",
        "bar_time": "2026-08-20T09:35:00+08:00",
        "summary": "测试信号",
        "payload_json": "{}",
    }


def test_latest_requires_auth(client):
    resp = client.get("/api/v1/signal-events/latest")
    assert resp.status_code == 401


def test_latest_returns_events(client, token_headers):
    ev = _event()
    assert upsert_signal_event(ev) is True
    assert upsert_signal_event(ev) is False  # 去重

    resp = client.get("/api/v1/signal-events/latest?limit=10", headers=token_headers)
    assert resp.status_code == 200
    events = resp.json()["data"]["events"]
    assert any(e["eventId"] == ev["event_id"] for e in events)
