"""币圈与期货因子对齐测试：币圈现在也有门条件 + 走法×破诀组合。"""
import importlib
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.accounts import create_user, issue_user_key, set_user_shadow_mode
from app.db import init_db
from app.factor_registry import registry
from app.factors.base import FactorContext
from app.factors.gate_condition import normalize_gate_datetimes

crypto_gate_module = importlib.import_module("app.factors.crypto_gate_condition")
crypto_signal_module = importlib.import_module("app.factors.crypto_gate_signal")
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
    assert keys == ["crypto_market", "crypto_gate_condition", "crypto_gate_signal", "crypto_wave_jue_combo"]
    futures = [d["factorKey"] for d in registry.descriptors(for_record=record, domain="futures")]
    assert futures == ["dimen_gate_signal", "futures_gate_signal", "jue_direction", "gate_condition", "wave_jue_combo"]


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


def test_gate_datetime_normalization_appends_explicit_timezone():
    payload = {
        "gates": [
            {"key": "a", "open_at": "2026-08-30 12:15:55", "close_at": None, "t1_raw": "2026-08-29 19:30:00"},
            {"key": "b", "open_at": "2026-08-30T12:15:55+00:00"},
        ]
    }
    normalized = normalize_gate_datetimes(payload, "+00:00")
    assert normalized["gates"][0]["open_at"] == "2026-08-30T12:15:55+00:00"
    assert normalized["gates"][0]["t1_raw"] == "2026-08-29T19:30:00+00:00"
    # 已经带时区的不重复处理。
    assert normalized["gates"][1]["open_at"] == "2026-08-30T12:15:55+00:00"

    shanghai = normalize_gate_datetimes(
        {
            "gates": [
                {"key": "a", "open_at": "2026-08-30 12:15:55", "close_at": None},
            ]
        },
        "+08:00",
    )
    assert shanghai["gates"][0]["open_at"] == "2026-08-30T12:15:55+08:00"


@pytest.mark.asyncio
async def test_crypto_gate_signal_supports_four_states_and_precise_filter(monkeypatch):
    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))
    event_date = now_bj.strftime("%m/%d")
    event_time = now_bj.strftime("%H:%M:%S")
    events_payload = {
        "events": [
            {
                "date": event_date,
                "time": event_time,
                "timezone": "CST",
                "type": "open",
                "edge": "open",
                "first_action": True,
                "key": "jinmen2_BTCUSDT_15m_di_0830_0100",
                "sym": "BTCUSDT",
                "freq": "15m",
                "text": "BTC 15m 地门 门下已开门",
            },
            {
                "date": event_date,
                "time": event_time,
                "timezone": "CST",
                "type": "close",
                "edge": "close",
                "first_action": True,
                "key": "jinmen2_ETHUSDT_15m_tian_0830_0200",
                "sym": "ETHUSDT",
                "freq": "15m",
                "text": "ETH 15m 天门 门上已关门",
            },
            {
                "date": event_date,
                "time": event_time,
                "timezone": "CST",
                "type": "new",
                "key": "jinmen2_SANDUSDT_15m_di_0830_0300",
                "sym": "SANDUSDT",
                "freq": "15m",
                "text": "15m 地门 SAND 门上 0.0382",
            },
            {
                "date": event_date,
                "time": event_time,
                "timezone": "CST",
                "type": "new",
                "key": "jinmen2_XPLUSDT_15m_tian_0830_0400",
                "sym": "XPLUSDT",
                "freq": "15m",
                "text": "5m 天门 XPL 门下 0.0846",
            },
        ]
    }
    gates_payload = {
        "gates": [
            {
                "key": "jinmen2_BTCUSDT_15m_di_0830_0100",
                "sym": "BTCUSDT",
                "name": "BTC",
                "freq": "15m",
                "type": "di",
                "gate_price": 60000.0,
                "current_price": 61000.0,
            },
            {
                "key": "jinmen2_SANDUSDT_15m_di_0830_0300",
                "sym": "SANDUSDT",
                "name": "SAND",
                "freq": "15m",
                "type": "di",
                "gate_price": 0.0382,
                # 门池 formation 是当前状态；事件当时是“门上”，门池后来翻成“门下”，
                # 因子输出必须跟随事件 text，不能回退门池造成 status/formation 自相矛盾。
                "formation": "门下",
                "current_price": 0.03817,
                "ma208": 0.0370,
            },
        ]
    }

    async def fake_gates(*args, **kwargs):
        return gates_payload

    monkeypatch.setattr(crypto_signal_module, "_fetch_gates", fake_gates)
    crypto_signal_module._CACHE["payload"] = events_payload
    crypto_signal_module._CACHE["at"] = time.monotonic()

    result = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedEvents"] == 4
    by_status = {e["status"]: e for e in result["details"]["events"]}
    assert set(by_status) == {"OPEN", "CLOSED", "FORMATION_ABOVE", "FORMATION_BELOW"}
    assert by_status["OPEN"]["gatePrice"] == 60000.0
    assert by_status["OPEN"]["openAt"]
    assert by_status["OPEN"]["closeAt"] is None
    assert by_status["CLOSED"]["closeAt"]
    assert by_status["FORMATION_ABOVE"]["gateType"] == "di"
    assert by_status["FORMATION_ABOVE"]["formation"] == "门上"
    assert by_status["FORMATION_BELOW"]["gateType"] == "tian"
    assert by_status["FORMATION_BELOW"]["formation"] == "门下"
    assert all(e["signalKind"] == "event" for e in result["details"]["events"])

    assert by_status["FORMATION_ABOVE"]["ma208"] == 0.0370
    assert by_status["FORMATION_ABOVE"]["gateMa208DistancePct"] > 0

    ma208_filtered = await crypto_signal_module.crypto_gate_signal.handler(
        {
            "frequencies": ["15m"],
            "eventTypes": ["formationAbove"],
            "ma208Mode": "nearOrAbove",
        },
        FactorContext(token_id=1),
    )
    assert [e["symbol"] for e in ma208_filtered["details"]["events"]] == ["SANDUSDT"]

    above = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"], "eventTypes": ["formationAbove"]},
        FactorContext(token_id=1),
    )
    assert [e["status"] for e in above["details"]["events"]] == ["FORMATION_ABOVE"]

    below = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"], "eventTypes": ["formationBelow"]},
        FactorContext(token_id=1),
    )
    assert [e["status"] for e in below["details"]["events"]] == ["FORMATION_BELOW"]

    actions = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"], "eventTypes": ["open", "close"]},
        FactorContext(token_id=1),
    )
    assert {e["status"] for e in actions["details"]["events"]} == {"OPEN", "CLOSED"}

    legacy_formation = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"], "eventTypes": ["formation"]},
        FactorContext(token_id=1),
    )
    assert {e["status"] for e in legacy_formation["details"]["events"]} == {
        "FORMATION_ABOVE",
        "FORMATION_BELOW",
    }



@pytest.mark.asyncio
async def test_crypto_gate_signal_dedupes_open_and_formation_same_key(monkeypatch):
    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))
    date = now_bj.strftime("%m/%d")
    clock = now_bj.strftime("%H:%M:%S")
    key = "jinmen2_DUPUSDT_15m_di_0902_0245"
    key_above = "jinmen2_DUPUSDT_15m_di_0902_0300"
    events_payload = {
        "events": [
            {
                "date": date, "time": clock, "timezone": "CST", "type": "new",
                "key": key, "sym": "DUPUSDT", "freq": "15m",
                "text": "15m \u5730\u95e8 DUP \u95e8\u4e0b 0.10",
            },
            {
                "date": date, "time": clock, "timezone": "CST", "type": "open",
                "key": key, "sym": "DUPUSDT", "freq": "15m", "edge": "open",
                "first_action": True, "text": "DUP 15m \u5730\u95e8 \u95e8\u4e0b\u5df2\u5f00\u95e8",
            },
            {
                "date": date, "time": clock, "timezone": "CST", "type": "new",
                "key": key_above, "sym": "DUPUSDT", "freq": "15m",
                "text": "15m \u5730\u95e8 DUP \u95e8\u4e0a 0.11",
            },
            {
                "date": date, "time": clock, "timezone": "CST", "type": "open",
                "key": key_above, "sym": "DUPUSDT", "freq": "15m", "edge": "open",
                "first_action": True, "text": "DUP 15m \u5730\u95e8 \u95e8\u4e0b\u5df2\u5f00\u95e8",
            },
        ]
    }
    gates_payload = {"gates": [
        {
            "key": key, "sym": "DUPUSDT", "name": "DUP", "freq": "15m", "type": "di",
            "gate_price": 0.10, "current_price": 0.101, "ma208": 0.09,
        },
        {
            "key": key_above, "sym": "DUPUSDT", "name": "DUP", "freq": "15m", "type": "di",
            "gate_price": 0.11, "current_price": 0.111, "ma208": 0.09,
        },
    ]}

    async def fake_gates(*args, **kwargs):
        return gates_payload

    monkeypatch.setattr(crypto_signal_module, "_fetch_gates", fake_gates)
    crypto_signal_module._CACHE["payload"] = events_payload
    crypto_signal_module._CACHE["at"] = time.monotonic()

    both = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"], "eventTypes": ["open", "formation"]},
        FactorContext(token_id=1),
    )
    assert both["details"]["matchedEvents"] == 2
    by_key = {e["key"]: e for e in both["details"]["events"]}
    assert by_key[key]["status"] == "OPEN"
    assert by_key[key_above]["status"] == "FORMATION_ABOVE"
    assert by_key[key]["eventId"] == "crypto-gate:%s:signal" % key
    assert by_key[key_above]["eventId"] == "crypto-gate:%s:signal" % key_above

    formation_only = await crypto_signal_module.crypto_gate_signal.handler(
        {"frequencies": ["15m"], "eventTypes": ["formation"]},
        FactorContext(token_id=1),
    )
    assert {e["key"]: e["status"] for e in formation_only["details"]["events"]} == {
        key: "FORMATION_BELOW",
        key_above: "FORMATION_ABOVE",
    }


def test_crypto_gate_signal_precise_event_types_are_subscribable():
    """新订阅必须能精确选择四态和 MA208 门价条件。"""
    from app.agent.tools import build_tools
    from app.signal_subscriptions import create_signal_subscription, list_signal_subscriptions

    user, key, record = _shadow_record()
    create_tool = next(
        tool for tool in build_tools(for_record=record)
        if tool["function"]["name"] == "sanyi_create_subscription"
    )
    filters = create_tool["function"]["parameters"]["properties"]["filters"]["properties"]
    event_enum = filters["eventTypes"]["items"]["enum"]
    assert event_enum == ["open", "close", "formation", "formationAbove", "formationBelow"]
    assert filters["ma208Anchor"]["enum"] == ["gatePrice", "currentPrice"]
    assert filters["ma208Mode"]["enum"] == ["near", "above", "nearOrAbove"]

    created = create_signal_subscription(
        record,
        "crypto_gate_signal",
        {
            "frequencies": ["15m", "1h"],
            "gateTypes": ["tian"],
            "eventTypes": ["formationAbove"],
            "ma208Mode": "nearOrAbove",
            "ma208Anchor": "gatePrice",
            "ma208TolerancePct": 1,
        },
        "币圈天门形成门上",
    )
    stored = list_signal_subscriptions(user["id"])[0]
    assert stored["conditions"][0]["filters"]["eventTypes"] == ["formationAbove"]
    assert stored["conditions"][0]["filters"]["ma208Anchor"] == "gatePrice"