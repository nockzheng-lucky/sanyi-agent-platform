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


def test_match_line_gate_states_are_clean():
    """四态门信号的行文案必须自带门类型/形成侧，不能重复或追加矛盾 formation。"""
    above = pusher_module._match_line(
        {
            "symbol": "BTCUSDT",
            "name": "BTC",
            "frequency": "1h",
            "gateType": "tian",
            "status": "FORMATION_ABOVE",
            "formation": "门下",
            "eventAt": "2026-09-01T16:06:35+08:00",
        }
    )
    assert "天门形成·无动作门上" in above
    assert above.count("天门") == 1
    assert "门下" not in above

    closed = pusher_module._match_line(
        {
            "symbol": "ETHUSDT",
            "name": "ETH",
            "frequency": "15m",
            "gateType": "tian",
            "status": "CLOSED",
            "formation": "门上",
            "closeAt": "2026-09-01T14:45:40+08:00",
        }
    )
    assert "天门关" in closed
    assert "门上" not in closed



def test_match_line_ma208_distance_uses_above_below_wording():
    above = pusher_module._match_line(
        {
            "symbol": "BTCUSDT",
            "name": "BTC",
            "frequency": "1h",
            "gateType": "tian",
            "status": "OPEN",
            "gatePrice": 65000.0,
            "gateMa208DistancePct": 1.23,
        }
    )
    assert "门价高于MA208 1.23%" in above
    assert "距MA208" not in above

    below = pusher_module._match_line(
        {
            "symbol": "ETHUSDT",
            "name": "ETH",
            "frequency": "1h",
            "gateType": "di",
            "status": "OPEN",
            "gatePrice": 3000.0,
            "gateMa208DistancePct": -20.5,
        }
    )
    assert "门价低于MA208 20.5%" in below
    assert "距MA208" not in below

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
        return True, 200

    monkeypatch.setattr(pusher_module, "send_pushplus_checked", fake_send)

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
    assert "PUSHAG" in sent[0]["title"]




@pytest.mark.asyncio
async def test_subscription_pusher_batches_same_round_signals(monkeypatch):
    phone = "132" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    key = issue_user_key(user["id"], "push-one-by-one", rate_limit_per_min=1000)
    record = {"id": key["id"], "_table": "user_keys", "_user_id": user["id"]}
    sub = create_signal_subscription(
        record,
        "dimen_gate_signal",
        {"frequencies": ["5m"], "symbols": ["PUSHA", "PUSHB", "PUSHC", "PUSHD"], "limit": 10},
        "地门开门提醒",
    )
    set_pushplus_token(user["id"], "user-own-pushplus-token")
    sent = []

    async def fake_send(title, content, token=""):
        sent.append({"title": title, "content": content})
        return True, 200

    monkeypatch.setattr(pusher_module, "send_pushplus_checked", fake_send)
    monkeypatch.setattr(pusher_module, "list_all_active_signal_subscriptions", lambda: [sub])

    assert upsert_signal_event(_event("push-a", symbol="PUSHA")) is True
    assert upsert_signal_event(_event("push-b", symbol="PUSHB")) is True

    pusher = pusher_module.SubscriptionPusher()
    # 首轮只建 baseline，不推订阅时已存在的信号。
    assert await pusher.run_once() == 0
    assert sent == []

    from app.signal_subscriptions import list_signal_subscriptions

    sub = list_signal_subscriptions(record["_user_id"])[0]

    assert upsert_signal_event(_event("push-c", symbol="PUSHC")) is True
    assert upsert_signal_event(_event("push-d", symbol="PUSHD")) is True
    # 同一轮出现的两条信号合并成一条 Pushplus。
    assert await pusher.run_once() == 1
    assert len(sent) == 1
    assert "2 条新信号" in sent[0]["title"]
    assert "PUSHC" in sent[0]["content"]
    assert "PUSHD" in sent[0]["content"]


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


@pytest.mark.asyncio
async def test_agent_tool_reports_pushplus_binding_status():
    from app.agent.tools import build_tools, execute_tool

    phone = "133" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    key = issue_user_key(user["id"], "pushplus-agent-key", rate_limit_per_min=1000)
    record = {"id": key["id"], "_table": "user_keys", "_user_id": user["id"]}

    tools = build_tools(for_record=record)
    assert any(t["function"]["name"] == "sanyi_get_pushplus" for t in tools)

    result = await execute_tool("sanyi_get_pushplus", {}, record)
    assert result["bound"] is False
    assert "尚未绑定" in result["message"]

    set_pushplus_token(user["id"], "abcdef1234567890abcdef1234567890")
    result = await execute_tool("sanyi_get_pushplus", {}, record)
    assert result["bound"] is True
    assert result["pushplus"]["tokenMasked"].startswith("abcdef")
    assert "abcdef1234567890abcdef1234567890" not in str(result)
