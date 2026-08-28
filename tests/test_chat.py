import json

from app.agent.loop import _history_from
from app.db import issue_token


def _sse_events(text):
    events = []
    for part in text.split("\n\n"):
        if not part.strip():
            continue
        event, data = None, None
        for line in part.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if data is not None:
            events.append((event, data))
    return events


def test_chat_flow_with_mock_llm(client):
    token = issue_token(name="chat-test", quota_total=100000, rate_limit_per_min=1000)["token"]

    login = client.post("/api/chat/login", json={"token": token})
    assert login.status_code == 200

    session = client.get("/api/chat/session")
    assert session.status_code == 200

    resp = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "看看 15 分钟地门开做多信号"}]},
    )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    names = [name for name, _ in events]
    assert "meta" in names
    assert "tool_call" in names
    assert "tool_result" in names
    assert "done" in names

    me = client.get("/api/v1/me", headers={"X-API-Token": token}).json()["data"]["token"]
    assert me["quotaUsed"] == 0


def test_chat_history_includes_loaded_factor_context():
    history = _history_from(
        [{"role": "user", "content": "看看因子结果"}],
        factor_keys=["dimen_gate_signal", "no_such_factor"],
    )
    system_prompt = history[0]["content"]
    assert "dimen_gate_signal" in system_prompt
    assert "地门信号" in system_prompt
    assert "no_such_factor" not in system_prompt


def test_chat_accepts_factor_keys_payload(client):
    token = issue_token(name="chat-factor-keys", quota_total=100000, rate_limit_per_min=1000)["token"]
    assert client.post("/api/chat/login", json={"token": token}).status_code == 200
    resp = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "地门信号怎么样"}],
            "factorKeys": ["dimen_gate_signal"],
        },
    )
    assert resp.status_code == 200
    assert "tool_call" in [name for name, _ in _sse_events(resp.text)]


def test_chat_entry_token_is_exchanged_for_cookie(client):
    token = issue_token(name="entry-test", quota_total=100000)["token"]
    resp = client.get(f"/chat?token={token}", follow_redirects=False)
    assert resp.status_code == 303
    assert "sanyi_session" in client.cookies

    session = client.get("/api/chat/session")
    assert session.status_code == 200


def test_chat_rejects_without_session(client):
    resp = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "你好"}]},
    )
    assert resp.status_code == 401
