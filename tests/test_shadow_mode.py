import importlib
import uuid

import pytest

from app.accounts import create_user, create_user_session, issue_user_key, set_user_shadow_mode
from app.factor_registry import registry
from app.factors.base import FactorContext

crypto_module = importlib.import_module("app.factors.crypto_market")

TICKERS = [
    {"currency_pair": "BTC_USDT", "last": "65000", "change_percentage": "4.2", "quote_volume": "1000000", "high_24h": "66000", "low_24h": "62000", "base_volume": "15", "highest_bid": "64999", "lowest_ask": "65001"},
    {"currency_pair": "ETH_USDT", "last": "3200", "change_percentage": "-2.1", "quote_volume": "500000", "high_24h": "3300", "low_24h": "3100", "base_volume": "160", "highest_bid": "3199", "lowest_ask": "3201"},
    {"currency_pair": "DOGE_USDT", "last": "0.12", "change_percentage": "0", "quote_volume": "10000", "high_24h": "0.13", "low_24h": "0.11", "base_volume": "80000", "highest_bid": "0.119", "lowest_ask": "0.121"},
]


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
        return TICKERS

    monkeypatch.setattr(crypto_module, "_fetch_tickers", fake_fetch)

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
        json={"factorKey": "crypto_market", "params": {"quotes": ["USDT"], "limit": 10}},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["factorKey"] == "crypto_market"
    assert data["details"]["matchedSymbols"] == 3
    assert data["details"]["coins"][0]["symbol"] == "BTC_USDT"  # 按绝对涨跌幅排序


@pytest.mark.asyncio
async def test_crypto_factor_filter_direction(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return TICKERS

    monkeypatch.setattr(crypto_module, "_fetch_tickers", fake_fetch)
    result = await crypto_module.crypto_market.handler(
        {"directions": ["down"], "limit": 10},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedSymbols"] == 1
    assert result["details"]["coins"][0]["symbol"] == "ETH_USDT"


def test_shadow_subscription_creation(client, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return TICKERS

    monkeypatch.setattr(crypto_module, "_fetch_tickers", fake_fetch)
    from app.signal_subscriptions import create_signal_subscription

    user, key, record = _shadow_key_record()
    created = create_signal_subscription(record, "crypto_market", {"quotes": ["USDT"], "limit": 10}, "BTC 异动")
    assert created["factorKey"] == "crypto_market"
    assert registry.get("crypto_market").shadow_only is True
