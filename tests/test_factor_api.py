import uuid

from app.accounts import create_user, create_user_session


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_factors_requires_token(client):
    resp = client.get("/api/v1/factors")
    assert resp.status_code == 401


def test_factors_page_is_served(client):
    resp = client.get("/factors")
    assert resp.status_code == 200
    assert "因子列表" in resp.text
    assert "/chat/factors.js" in resp.text


def test_factor_list_visible_to_user_session_without_key(client):
    """页面右侧栏需要在用户还没有 Key 时也能浏览因子元信息。"""
    phone = "138" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    session = create_user_session(user["id"])
    client.cookies.set("sanyi_user", session)

    resp = client.get("/api/v1/factors")
    assert resp.status_code == 200
    keys = [f["factorKey"] for f in resp.json()["data"]["factors"]]
    assert "dimen_gate_signal" in keys

    # 元信息可读，但执行因子的 REST 入口仍只接受 X-API-Token/Bearer；
    # 页面 Agent 走 /api/chat，由 get_actor 校验 Key 与订阅。
    resp = client.post(
        "/api/v1/factors/evaluate",
        json={"factorKey": "dimen_gate_signal", "params": {}},
    )
    assert resp.status_code == 401


def test_factor_flow(client, token_headers):
    resp = client.get("/api/v1/factors", headers=token_headers)
    assert resp.status_code == 200
    body = resp.json()
    keys = [f["factorKey"] for f in body["data"]["factors"]]
    assert "dimen_gate_signal" in keys

    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "dimen_gate_signal", "params": {"maxAgeMinutes": 120}},
    )
    assert resp.status_code == 200
    result = resp.json()["data"]
    assert result["factorKey"] == "dimen_gate_signal"
    assert isinstance(result["details"]["events"], list)
    assert result["generatedAt"]

    # 月费制：因子调用不逐次扣额度，只审计。
    me = client.get("/api/v1/me", headers=token_headers).json()["data"]["token"]
    assert me["quotaUsed"] == 0


def test_factor_invalid_params(client, token_headers):
    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "dimen_gate_signal", "params": {"maxAgeMinutes": -1}},
    )
    assert resp.status_code == 422


def test_unknown_factor(client, token_headers):
    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "no_such_factor", "params": {}},
    )
    assert resp.status_code == 404
