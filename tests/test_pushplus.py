import uuid

import pytest

from app.accounts import create_user, create_user_session, issue_user_key
from app.db import upsert_signal_event
from app.engine import subscription_pusher as pusher_module
from app.push_channels import set_pushplus_token
from app.signal_subscriptions import create_signal_subscription


def _event(event_id, symbol="AU"):
    return {
        "event_id": event_id,
        "factor_key": "dimen_gate_signal",
        "symbol": symbol,
        "frequency": "5m",
        "status": "OPEN",
        "formation": "门下",
        "gate_price": 100.0,
        "current_price": 101.5,
        "open_at": "2026-08-29T10:00:00+08:00",
        "bar_time": "2026-08-29T10:00:00+08:00",
        "summary": "测试门信号",
        "payload_json": "{}",
    }


@pytest.mark.asyncio
async def test_subscription_pusher_dynamic_content(monkeypatch):
    phone = "135" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    key = issue_user_key(user["id"], "push-test-key", rate_limit_per_min=1000)
    record = {"id": key["id"], "_table": "user_keys", "_user_id": user["id"]}
    sub = create_signal_subscription(
        record,
        "dimen_gate_signal",
        {"frequencies": ["5m"], "symbols": ["PUSHAU", "PUSHAG"], "limit": 10},
        "PUSHAU 5分钟开门",
    )

    set_pushplus_token(user["id"], "user-own-pushplus-token")
    sent = []

    async def fake_send(title, content, token=""):
        assert token == "user-own-pushplus-token"
        sent.append({"title": title, "content": content})
        return True

    monkeypatch.setattr(pusher_module, "send_pushplus", fake_send)

    pusher = pusher_module.SubscriptionPusher()
    assert await pusher.run_once() == 0

    assert upsert_signal_event(_event("push-event-1", symbol="PUSHAU")) is True
    assert await pusher.run_once() == 1
    assert len(sent) == 1
    assert "PUSHAU 5分钟开门" in sent[0]["title"]
    assert "PUSHAU" in sent[0]["content"]
    assert "地门开" in sent[0]["content"]

    # 同一信号不重复推。
    sent.clear()
    assert await pusher.run_once() == 0

    # 新出现的不同信号，推送内容跟着订阅动态变化。
    assert upsert_signal_event(_event("push-event-2", symbol="PUSHAG")) is True
    assert await pusher.run_once() == 1
    assert "PUSHAG" in sent[0]["content"]
    assert "新增 1 条信号" in sent[0]["title"]


def test_pushplus_bind_status_unbind_api(client):
    phone = "134" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    session = create_user_session(user["id"])
    client.cookies.set("sanyi_user", session)

    resp = client.get("/api/v1/notifications/pushplus")
    assert resp.status_code == 200
    assert resp.json()["data"]["pushplus"] is None

    resp = client.post("/api/v1/notifications/pushplus", json={"token": "abcdef1234567890abcdef1234567890"})
    assert resp.status_code == 200
    channel = resp.json()["data"]["pushplus"]
    assert channel["tokenMasked"].startswith("abcdef")
    assert "7890" in channel["tokenMasked"]
    assert "abcdef1234567890abcdef1234567890" not in resp.text

    resp = client.get("/api/v1/notifications/pushplus")
    assert resp.json()["data"]["pushplus"]["tokenMasked"] == channel["tokenMasked"]

    resp = client.delete("/api/v1/notifications/pushplus")
    assert resp.status_code == 200
    assert client.get("/api/v1/notifications/pushplus").json()["data"]["pushplus"] is None
