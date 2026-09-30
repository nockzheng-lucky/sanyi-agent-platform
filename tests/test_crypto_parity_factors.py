"""币圈与期货因子对齐测试：币圈现在也有门条件 + 走法×破诀组合。"""
import importlib
import uuid

import pytest

from app.accounts import create_user, issue_user_key, set_user_shadow_mode
from app.db import init_db
from app.factor_registry import registry
from app.factors.base import FactorContext

crypto_gate_module = importlib.import_module("app.factors.crypto_gate_condition")
crypto_wave_module = importlib.import_module("app.factors.crypto_wave_jue_combo")

CRYPTO_GATE_PAYLOAD = {
    "updated": "14:10:00",
    "gates": [
        {
            "sym": "BTCUSDT",
            "name": "BTC",
            "freq": "1h",
            "type": "di",
            "gate_price": 60000.0,
            "current_price": 62000.0,
            "live_status": "已开",
            "formation": "门下",
            "is_first": True,
            "key": "jinmen2_BTCUSDT_1h_di_test",
            "t1_str": "08/30 10:00",
            "t2_str": "08/30 11:00",
            "cross_str": "08/30 12:00",
            "x_above": False,
            "x_crosses": 1,
        },
        {
            "sym": "ETHUSDT",
            "name": "ETH",
            "freq": "15m",
            "type": "tian",
            "gate_price": 3000.0,
            "current_price": 2900.0,
            "live_status": "已关",
            "formation": "门上",
            "is_first": True,
            "key": "jinmen2_ETHUSDT_15m_tian_test",
            "t1_str": "08/30 10:00",
            "t2_str": "08/30 11:00",
            "cross_str": "08/30 12:00",
            "x_above": True,
            "x_crosses": 0,
        },
        {
            "sym": "BTCUSDT",
            "name": "BTC",
            "freq": "15m",
            "type": "di",
            "gate_price": 61000.0,
            "current_price": 62000.0,
            "live_status": "删除",
            "formation": None,
            "is_first": False,
            "key": "jinmen2_BTCUSDT_15m_di_deleted",
        },
    ],
}

CRYPTO_JUE_PAYLOAD = {
    "updated_at": "2026-08-30T06:00:00+00:00",
    "state_rules": [],
    "sectors": [
        {
            "name": "Layer1",
            "items": [
                {
                    "sym": "BTCUSDT",
                    "name": "BTC",
                    "label": "BTC",
                    "cells": [
                        {
                            "freq": "15m",
                            "state": "20诀破诀",
                            "direction": "long",
                            "side": "多",
                            "thr": 20,
                            "price": 62000.0,
                            "broken": True,
                            "gap": False,
                            "pending": False,
                            "rsi3": 30.0,
                            "walk_state": "走2",
                            "walk_code": "2",
                            "walk_mark": "20破·走2",
                            "pair_confirm_prev": False,
                            "pair_confirm_next": False,
                        },
                        {
                            "freq": "1h",
                            "state": "80诀破诀",
                            "direction": "short",
                            "side": "空",
                            "thr": 80,
                            "price": 63000.0,
                            "broken": True,
                            "gap": False,
                            "pending": False,
                            "rsi3": 70.0,
                            "walk_state": "走B",
                            "walk_code": "B",
                            "walk_mark": "80破·走B",
                            "pair_confirm_prev": False,
                            "pair_confirm_next": False,
                        },
                    ],
                }
            ],
        }
    ],
}


def _shadow_record():
    init_db()
    phone = "138" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    set_user_shadow_mode(user["id"], True)
    key = issue_user_key(user["id"], "crypto-parity-key", rate_limit_per_min=1000)
    return user, key, {
        "id": key["id"],
        "_table": "user_keys",
        "_user_id": user["id"],
        "_key_id": key["id"],
    }


@pytest.mark.asyncio
async def test_crypto_gate_condition_parity(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return CRYPTO_GATE_PAYLOAD

    monkeypatch.setattr(crypto_gate_module, "_fetch_gates", fake_fetch)

    result = await crypto_gate_module.crypto_gate_condition.handler(
        {"gateTypes": ["di"], "liveStatuses": ["已开", "无动作·门上"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedGates"] == 1
    assert result["details"]["gates"][0]["key"] == "jinmen2_BTCUSDT_1h_di_test"
    assert result["details"]["gates"][0]["gatePrice"] == 60000.0


@pytest.mark.asyncio
async def test_crypto_wave_jue_combo_parity(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return CRYPTO_JUE_PAYLOAD

    monkeypatch.setattr(crypto_wave_module, "_fetch_payload", fake_fetch)

    result = await crypto_wave_module.crypto_wave_jue_combo.handler(
        {"combos": ["walk2_break20"], "frequencies": ["15m"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 1
    assert result["details"]["cells"][0]["symbol"] == "BTCUSDT"
    assert result["details"]["cells"][0]["comboKey"] == "walk2_break20"


def test_crypto_domain_descriptors_include_all_three():
    user, key, record = _shadow_record()
    keys = [d["factorKey"] for d in registry.descriptors(for_record=record, domain="crypto")]
    assert keys == ["crypto_market", "crypto_gate_condition", "crypto_wave_jue_combo"]
    futures = [d["factorKey"] for d in registry.descriptors(for_record=record, domain="futures")]
    assert futures == ["dimen_gate_signal", "jue_direction", "gate_condition", "wave_jue_combo"]


def test_crypto_parity_factors_hidden_from_normal_users():
    init_db()
    phone = "137" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    key = issue_user_key(user["id"], "normal-user-key", rate_limit_per_min=1000)
    record = {"id": key["id"], "_table": "user_keys", "_user_id": user["id"]}
    crypto_keys = [d["factorKey"] for d in registry.descriptors(for_record=record, domain="crypto")]
    assert crypto_keys == []
    for factor_key in ("crypto_gate_condition", "crypto_wave_jue_combo"):
        assert registry.get(factor_key).shadow_only is True
