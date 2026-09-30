import importlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.factors.base import FactorContext

gate_module = importlib.import_module("app.factors.gate_condition")
combo_module = importlib.import_module("app.factors.wave_jue_combo")
futures_signal_module = importlib.import_module("app.factors.futures_gate_signal")

GATE_PAYLOAD = {
    "updated": "13:15:52",
    "gates": [
        {"sym": "RB0", "name": "螺纹钢", "freq": "15m", "type": "di", "gate_price": 3600.0, "current_price": 3580.0, "live_status": "已开", "formation": "门下", "is_first": True, "key": "rb-di-1", "t1_str": "08/28 10:00", "t2_str": "08/28 11:00", "cross_str": "08/28 12:00", "x_above": False, "x_crosses": 1},
        {"sym": "AU0", "name": "黄金", "freq": "1h", "type": "tian", "gate_price": 900.0, "current_price": 910.0, "live_status": "无动作·门上", "formation": "门上", "is_first": False, "key": "au-tian-1", "t1_str": "08/28 10:00", "t2_str": "08/28 11:00", "cross_str": "08/28 12:00", "x_above": True, "x_crosses": 0},
        {"sym": "RB0", "name": "螺纹钢", "freq": "5m", "type": "di", "gate_price": 3700.0, "current_price": 3580.0, "live_status": "删除", "formation": None, "is_first": False, "key": "rb-di-del", "t1_str": "", "t2_str": "", "cross_str": "", "x_above": None, "x_crosses": None},
    ],
}

JUE_PAYLOAD = {
    "updated_at": "2026-08-30T05:00:00+00:00",
    "state_rules": [],
    "sectors": [
        {
            "name": "黑色",
            "items": [
                {
                    "sym": "RB0",
                    "name": "螺纹钢",
                    "label": "螺纹钢 RB2610",
                    "cells": [
                        {"freq": "15m", "state": "20诀破诀", "direction": "long", "side": "多", "thr": 20, "price": 3600.0, "broken": True, "gap": False, "pending": False, "rsi3": 30.0, "walk_state": "走2", "walk_code": "2", "walk_mark": "20破·走2", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "1h", "state": "80诀破诀", "direction": "short", "side": "空", "thr": 80, "price": 3700.0, "broken": True, "gap": False, "pending": False, "rsi3": 70.0, "walk_state": "走B", "walk_code": "B", "walk_mark": "80破·走B", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "5m", "state": "20诀破诀", "direction": "long", "side": "多", "thr": 20, "price": 3590.0, "broken": True, "gap": False, "pending": False, "rsi3": 25.0, "walk_state": "走C", "walk_code": "C", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": False},
                    ],
                }
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_gate_condition_filters(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return GATE_PAYLOAD

    monkeypatch.setattr(gate_module, "_fetch_gates", fake_fetch)
    result = await gate_module.gate_condition.handler({}, FactorContext(token_id=1))
    assert result["details"]["matchedGates"] == 2  # 默认排除删除
    assert result["signal"] == "MIXED"

    result = await gate_module.gate_condition.handler(
        {"gateTypes": ["di"], "liveStatuses": ["已开"], "symbols": ["RB0"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedGates"] == 1
    assert result["details"]["gates"][0]["key"] == "rb-di-1"


@pytest.mark.asyncio
async def test_wave_jue_combo_current_snapshot(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return JUE_PAYLOAD

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_fetch)
    result = await combo_module.wave_jue_combo.handler({}, FactorContext(token_id=1))
    assert result["details"]["matchedCells"] == 3
    assert result["details"]["counts"]["walk2_break20"] == 1
    assert result["details"]["counts"]["walkB_break80"] == 1
    assert result["details"]["counts"]["walkC_break20"] == 1

    result = await combo_module.wave_jue_combo.handler(
        {"combos": ["walk2_break20"], "frequencies": ["15m"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 1
    assert result["details"]["cells"][0]["comboLabel"] == "走2·破20诀"



@pytest.mark.asyncio
async def test_gate_condition_ma208_filter(monkeypatch):
    payload = {
        "gates": [
            {
                "sym": "RB0", "name": "螺纹钢", "freq": "15m", "type": "di",
                "gate_price": 3600.0, "current_price": 3560.0, "ma208": 3500.0,
                "live_status": "已开", "key": "rb-above",
            },
            {
                "sym": "AU0", "name": "黄金", "freq": "15m", "type": "di",
                "gate_price": 900.0, "current_price": 906.0, "ma208": 910.0,
                "live_status": "已开", "key": "au-below",
            },
        ]
    }

    async def fake_fetch(*args, **kwargs):
        return payload

    monkeypatch.setattr(gate_module, "_fetch_gates", fake_fetch)

    # 默认锚点 gatePrice：RB 门价在 MA208 上方；AU 门价在 MA208 下方。
    result = await gate_module.gate_condition.handler(
        {"liveStatuses": ["已开"], "ma208Mode": "above"},
        FactorContext(token_id=1),
    )
    assert [g["key"] for g in result["details"]["gates"]] == ["rb-above"]
    assert result["details"]["gates"][0]["gateMa208DistancePct"] > 0

    # 附近或以上：门价口径仍只保留 RB；现价口径时 AU 也在 MA208 附近。
    near_or_above = await gate_module.gate_condition.handler(
        {"liveStatuses": ["已开"], "ma208Mode": "nearOrAbove", "ma208TolerancePct": 1},
        FactorContext(token_id=1),
    )
    assert [g["key"] for g in near_or_above["details"]["gates"]] == ["rb-above"]

    current_near = await gate_module.gate_condition.handler(
        {
            "liveStatuses": ["已开"],
            "ma208Mode": "near",
            "ma208Anchor": "currentPrice",
            "ma208TolerancePct": 1,
        },
        FactorContext(token_id=1),
    )
    assert [g["key"] for g in current_near["details"]["gates"]] == ["au-below"]

@pytest.mark.asyncio
async def test_futures_gate_signal_uses_current_15_cycle(monkeypatch):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    cycle_start = futures_signal_module._cycle_start(now)
    new_open = (cycle_start + timedelta(hours=2)).isoformat()
    old_open = (cycle_start - timedelta(hours=2)).isoformat()
    payload = {
        "gates": [
            {"sym": "OI0", "name": "菜油", "freq": "15m", "type": "tian", "gate_price": 10388.0, "current_price": 10380.0, "open_at": new_open, "close_at": None, "key": "new"},
            {"sym": "RB0", "name": "螺纹钢", "freq": "15m", "type": "tian", "gate_price": 3000.0, "current_price": 3010.0, "open_at": old_open, "close_at": None, "key": "old"},
        ]
    }

    async def fake_fetch(*args, **kwargs):
        return payload

    monkeypatch.setattr(futures_signal_module, "_fetch_gates", fake_fetch)
    result = await futures_signal_module.futures_gate_signal.handler(
        {"gateTypes": ["tian"], "actions": ["open"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedEvents"] == 1
    assert result["details"]["events"][0]["symbol"] == "OI0"


@pytest.mark.asyncio
async def test_futures_gate_signal_ma208_filter_uses_gate_price_by_default(monkeypatch):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    cycle_start = futures_signal_module._cycle_start(now)
    open_at = (cycle_start + timedelta(hours=1)).isoformat()
    payload = {
        "gates": [
            {
                "sym": "OI0", "name": "菜油", "freq": "15m", "type": "di",
                "gate_price": 10388.0, "current_price": 10200.0, "ma208": 10000.0,
                "open_at": open_at, "close_at": None, "key": "above",
            },
            {
                "sym": "RB0", "name": "螺纹钢", "freq": "15m", "type": "di",
                "gate_price": 3000.0, "current_price": 3080.0, "ma208": 3100.0,
                "open_at": open_at, "close_at": None, "key": "below",
            },
        ]
    }

    async def fake_fetch(*args, **kwargs):
        return payload

    monkeypatch.setattr(futures_signal_module, "_fetch_gates", fake_fetch)
    result = await futures_signal_module.futures_gate_signal.handler(
        {"gateTypes": ["di"], "actions": ["open"], "ma208Mode": "nearOrAbove"},
        FactorContext(token_id=1),
    )
    assert [e["symbol"] for e in result["details"]["events"]] == ["OI0"]
    assert result["details"]["events"][0]["gateMa208DistancePct"] > 0

    # 显式改 currentPrice 后，RB 现价也在 MA208 附近或以上。
    current = await futures_signal_module.futures_gate_signal.handler(
        {
            "gateTypes": ["di"],
            "actions": ["open"],
            "ma208Mode": "nearOrAbove",
            "ma208Anchor": "currentPrice",
        },
        FactorContext(token_id=1),
    )
    assert {e["symbol"] for e in current["details"]["events"]} == {"OI0", "RB0"}
