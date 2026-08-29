import importlib
import uuid

import pytest

from app.accounts import create_user, create_user_session, issue_user_key, set_user_shadow_mode
from app.factor_registry import registry
from app.factors.base import FactorContext

crypto_module = importlib.import_module("app.factors.crypto_market")

PAYLOAD = {
    "updated_at": "2026-08-29T04:10:00+00:00",
    "contracts": 2,
    "matched_cells": 5,
    "state_rules": [
        {"state": "80诀", "thr": 80, "broken": False, "direction": "long", "side": "多"},
        {"state": "80诀破诀", "thr": 80, "broken": True, "direction": "short", "side": "空"},
        {"state": "20诀", "thr": 20, "broken": False, "direction": "short", "side": "空"},
        {"state": "20诀破诀", "thr": 20, "broken": True, "direction": "long", "side": "多"},
        {"state": "无诀", "thr": None, "broken": False, "direction": None, "side": None},
    ],
    "sectors": [
        {
            "name": "Layer1",
            "items": [
                {
                    "sym": "BTCUSDT",
                    "name": "BTC",
                    "label": "BTC",
                    "cells": [
                        {"freq": "15m", "state": "20诀破诀", "direction": "long", "side": "多", "thr": 20, "price": 77800.0, "broken": True, "gap": False, "pending": False, "rsi3": 31.0, "walk_state": "走2", "walk_code": "2", "walk_mark": "20破·走2", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "1h", "state": "80诀", "direction": "long", "side": "多", "thr": 80, "price": 78000.0, "broken": False, "gap": False, "pending": False, "rsi3": 42.0, "walk_state": "走1", "walk_code": "1", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": False},
                    ],
                },
                {
                    "sym": "ETHUSDT",
                    "name": "ETH",
                    "label": "ETH",
                    "cells": [
                        {"freq": "5m", "state": "80诀破诀", "direction": "short", "side": "空", "thr": 80, "price": 2900.0, "broken": True, "gap": False, "pending": False, "rsi3": 66.0, "walk_state": "走B", "walk_code": "B", "walk_mark": "80破·走B", "pair_confirm_prev": False, "pair_confirm_next": False},
                    ],
                },
            ],
        }
    ],
}


def _shadow_key_record():
    phone = "136" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    set_user_shadow_mode(user["id"], True)
    key = issue_user_key(user["id"], "shadow-key", rate_limit_per_min=1000)
    record = {
        "id": key["id"],
        "_table": "user_keys",
        "_user_id": user["id"],
        "_key_id": key["id"],
    }
    return user, key, record


def test_shadow_factor_hidden_from_normal_users(client, token_headers):
    keys = [f["factorKey"] for f in client.get("/api/v1/factors", headers=token_headers).json()["data"]["factors"]]
    assert "crypto_market" not in keys

    futures = [f["factorKey"] for f in client.get("/api/v1/factors?domain=futures", headers=token_headers).json()["data"]["factors"]]
    assert futures == ["dimen_gate_signal", "jue_direction"]

    assert client.get("/api/v1/factors?domain=crypto", headers=token_headers).status_code == 403
    assert client.get("/crypto").status_code == 200

    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "crypto_market", "params": {}},
    )
    assert resp.status_code == 404


def test_shadow_user_sees_and_evaluates_crypto(client, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return PAYLOAD

    monkeypatch.setattr(crypto_module, "_fetch_payload", fake_fetch)

    user, key, record = _shadow_key_record()
    raw_session = create_user_session(user["id"])
    client.cookies.set("sanyi_user", raw_session)

    listed = client.get("/api/v1/factors").json()["data"]["factors"]
    keys = [f["factorKey"] for f in listed]
    assert "crypto_market" in keys

    futures = [f["factorKey"] for f in client.get("/api/v1/factors?domain=futures").json()["data"]["factors"]]
    crypto = [f["factorKey"] for f in client.get("/api/v1/factors?domain=crypto").json()["data"]["factors"]]
    assert "crypto_market" not in futures
    assert crypto == ["crypto_market"]

    resp = client.post(
        "/api/v1/factors/evaluate",
        headers={"X-API-Token": key["token"]},
        json={
            "factorKey": "crypto_market",
            "params": {"frequencies": ["15m"], "states": ["20诀破诀"], "walkCodes": ["2"], "limit": 10},
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["factorKey"] == "crypto_market"
    assert data["details"]["matchedCells"] == 1
    assert data["details"]["cells"][0]["symbol"] == "BTCUSDT"
    assert data["details"]["cells"][0]["walkCode"] == "2"


@pytest.mark.asyncio
async def test_crypto_factor_filter_direction(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return PAYLOAD

    monkeypatch.setattr(crypto_module, "_fetch_payload", fake_fetch)
    result = await crypto_module.crypto_market.handler(
        {"directions": ["short"], "limit": 10},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 1
    assert result["details"]["cells"][0]["symbol"] == "ETHUSDT"


def test_shadow_subscription_creation(client, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return PAYLOAD

    monkeypatch.setattr(crypto_module, "_fetch_payload", fake_fetch)
    from app.signal_subscriptions import create_signal_subscription

    user, key, record = _shadow_key_record()
    created = create_signal_subscription(
        record,
        "crypto_market",
        {"frequencies": ["15m"], "states": ["20诀破诀"], "walkCodes": ["2"], "limit": 10},
        "BTC 20诀破诀·走2",
    )
    assert created["factorKey"] == "crypto_market"
    assert registry.get("crypto_market").shadow_only is True
