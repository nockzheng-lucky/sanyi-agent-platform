import random


def _phone():
    return "139%08d" % random.randint(0, 99999999)


def _send_code(client, phone):
    resp = client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"})
    assert resp.status_code == 200
    return resp.json()["data"]["debugCode"]


def test_register_login_key_and_chat_flow(client):
    phone = _phone()
    code = _send_code(client, phone)

    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "code": code, "password": "password123", "agree": True},
    )
    assert resp.status_code == 200

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["phoneMasked"].startswith("139")

    # 未申请 Key 时聊天页仍可打开，但标记 needsKey 引导申请
    chat_session = client.get("/api/chat/session")
    assert chat_session.status_code == 200
    assert chat_session.json()["data"]["needsKey"] is True

    created = client.post(
        "/api/v1/keys",
        json={"name": "my-agent", "expiresInDays": None, "allowIps": ""},
    )
    assert created.status_code == 200
    token = created.json()["data"]["token"]
    assert token.startswith("sk-sanyi-")

    keys = client.get("/api/v1/keys")
    assert keys.status_code == 200
    assert len(keys.json()["data"]["keys"]) == 1

    reveal = client.post(f"/api/v1/keys/{created.json()['data']['id']}/reveal", json={})
    assert reveal.status_code == 200
    assert reveal.json()["data"]["token"] == token

    # 有 Key 后聊天会话可用
    chat_session = client.get("/api/chat/session")
    assert chat_session.status_code == 200
    assert chat_session.json()["data"]["user"]["phoneMasked"].startswith("139")
    assert chat_session.json()["data"]["key"]["name"] == "my-agent"

    # 用户自己的 Agent 直接用 Key 调因子
    resp = client.post(
        "/api/v1/factors/evaluate",
        headers={"X-API-Token": token},
        json={"factorKey": "dimen_gate_signal", "params": {}},
    )
    assert resp.status_code == 200

    # 撤销后 Key 立即失效
    key_id = keys.json()["data"]["keys"][0]["id"]
    revoke = client.post(f"/api/v1/keys/{key_id}/revoke", json={})
    assert revoke.status_code == 200
    keys_after = client.get("/api/v1/keys").json()["data"]["keys"]
    assert keys_after == []
    resp = client.get("/api/v1/factors", headers={"X-API-Token": token})
    assert resp.status_code == 401


def test_admin_flow(client):
    from app.accounts import find_user_by_phone, set_user_role

    phone = _phone()
    code = _send_code(client, phone)
    client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "code": code, "password": "password123", "agree": True},
    )
    user = find_user_by_phone(phone)
    set_user_role(user["id"], "admin")

    resp = client.get("/api/v1/admin/users")
    assert resp.status_code == 200
    assert len(resp.json()["data"]["users"]) >= 1

    client.post("/api/v1/keys", json={"name": "admin-sees-me"})
    keys = client.get("/api/v1/admin/keys")
    assert keys.status_code == 200
    assert any(k["name"] == "admin-sees-me" for k in keys.json()["data"]["keys"])


def test_login_wrong_password(client):
    phone = _phone()
    code = _send_code(client, phone)
    client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "code": code, "password": "password123", "agree": True},
    )
    client.post("/api/v1/auth/logout", json={})
    resp = client.post("/api/v1/auth/login", json={"phone": phone, "password": "wrong"})
    assert resp.status_code == 401
    resp = client.post("/api/v1/auth/login", json={"phone": phone, "password": "password123"})
    assert resp.status_code == 200
