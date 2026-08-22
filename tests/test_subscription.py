import random


def _phone():
    return "135%08d" % random.randint(0, 99999999)


def test_subscription_request_and_admin_confirm(client):
    from app.accounts import find_user_by_phone, set_user_role

    phone = _phone()
    code = client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}).json()["data"]["debugCode"]
    client.post("/api/v1/auth/register", json={"phone": phone, "code": code, "password": "password123", "agree": True})
    user = find_user_by_phone(phone)
    set_user_role(user["id"], "admin")

    resp = client.post("/api/v1/subscription/requests", json={"plan": "monthly", "paymentNote": "wx transferred"})
    assert resp.status_code == 200
    req_id = resp.json()["data"]["id"]

    resp = client.get("/api/v1/subscription/requests")
    assert resp.status_code == 200
    assert resp.json()["data"]["requests"][0]["status"] == "pending_payment"

    resp = client.post(
        f"/api/v1/admin/subscription-requests/{req_id}/confirm",
        json={"note": "confirmed"},
    )
    assert resp.status_code == 200

    sub = client.get("/api/v1/subscription").json()["data"]
    assert sub["plan"] == "monthly"
    assert sub["expiresAt"]

    admin_list = client.get("/api/v1/admin/subscription-requests")
    assert admin_list.status_code == 200
    assert admin_list.json()["data"]["requests"][0]["status"] == "paid"


def test_gift_card_redeem_accumulates(client):
    from datetime import datetime

    phone = _phone()
    code = client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": "register"}).json()["data"]["debugCode"]
    client.post("/api/v1/auth/register", json={"phone": phone, "code": code, "password": "password123", "agree": True})

    first = client.post("/api/v1/subscription/redeem", json={"code": "MOCK30"})
    assert first.status_code == 200
    expires1 = datetime.fromisoformat(client.get("/api/v1/subscription").json()["data"]["expiresAt"])

    second = client.post("/api/v1/subscription/redeem", json={"code": "MOCK30"})
    assert second.status_code == 200
    expires2 = datetime.fromisoformat(client.get("/api/v1/subscription").json()["data"]["expiresAt"])
    assert (expires2 - expires1).days == 30

    invalid = client.post("/api/v1/subscription/redeem", json={"code": "BADCODE"})
    assert invalid.status_code == 422
