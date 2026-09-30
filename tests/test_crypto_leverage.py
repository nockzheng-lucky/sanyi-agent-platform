"""建议杠杆换算测试。"""
import importlib

import pytest

from app.crypto_leverage import (
    _CACHE,
    _VOLATILITY_MAP_CACHE,
    annotate_matches,
    suggested_leverage,
    volatility_from_bars,
)

module = importlib.import_module("app.crypto_leverage")


def _bars(last_close=100.0, day_high=110.0, day_low=90.0, count=30):
    rows = []
    for index in range(count):
        rows.append(
            {
                "open": last_close - 1,
                "high": day_high if index >= count - 24 else last_close + 1,
                "low": day_low if index >= count - 24 else last_close - 1,
                "close": last_close,
            }
        )
    return rows


def test_volatility_metrics_from_bars():
    metrics = volatility_from_bars(_bars())
    assert metrics is not None
    assert metrics["dayRangePct"] == 20.0  # (110-90)/100
    assert metrics["volatilityPct"] == metrics["dayRangePct"]
    assert metrics["atrPct"] > 0


def test_suggested_leverage_is_eth_anchor_scaled_and_clamped():
    eth_vol = 1.54
    assert suggested_leverage(1.54, eth_vol) == 50
    assert suggested_leverage(2.27, eth_vol) == 33  # 50*1.54/2.27=33.9 -> 33
    assert suggested_leverage(12.23, eth_vol) == 10  # 6.3 -> 下限 10
    assert suggested_leverage(0.5, eth_vol) == 50  # 上限 50
    assert suggested_leverage(0, eth_vol) == 10


@pytest.mark.asyncio
async def test_annotate_filters_gates_formed_before_symbol_entered_list(monkeypatch):
    async def fake_fetch_volatility_map():
        return [
            {
                "sym": "ETHUSDT",
                "atrPct": 1.0,
                "dayRangePct": 2.0,
                "volatilityPct": 2.0,
                "selectedSince": "2026-08-30T15:00:00+00:00",
            },
            {
                "sym": "ZKUSDT",
                "atrPct": 3.0,
                "dayRangePct": 18.0,
                "volatilityPct": 18.0,
                "selectedSince": "2026-08-30T15:00:00+00:00",
            },
        ]

    monkeypatch.setattr(module, "_fetch_volatility_map", fake_fetch_volatility_map)
    _CACHE.clear()
    _VOLATILITY_MAP_CACHE["_at"] = 0.0
    _VOLATILITY_MAP_CACHE["items"] = None

    matches = [
        {
            "symbol": "ZKUSDT",
            "frequency": "15m",
            "gateType": "tian",
            "liveStatus": "已开",
            "crossTime": "08/30 07:00",  # 入 list（23:00北京）之前形成
        },
        {
            "symbol": "ZKUSDT",
            "frequency": "15m",
            "gateType": "tian",
            "liveStatus": "已开",
            "crossRaw": "2026-08-30 15:30:00",  # 入 list 之后形成
        },
    ]
    annotated = await annotate_matches(matches)
    assert len(annotated) == 1
    assert annotated[0]["crossRaw"] == "2026-08-30 15:30:00"
    assert annotated[0]["suggestedLeverage"] == 10



@pytest.mark.asyncio
async def test_annotate_filters_cold_universe_event_signals_before_selected_since(monkeypatch):
    """合约宇宙冷纳入币种时补发的历史 type=new，不能作为事件线信号推送。"""
    async def fake_fetch_volatility_map():
        return [
            {
                "sym": "ETHUSDT",
                "atrPct": 1.0,
                "dayRangePct": 2.0,
                "volatilityPct": 2.0,
                "selectedSince": "2026-08-30T15:00:00+00:00",
            },
            {
                "sym": "ACEUSDT",
                "atrPct": 3.0,
                "dayRangePct": 20.0,
                "volatilityPct": 20.0,
                "selectedSince": "2026-09-01T13:00:00+00:00",
            },
        ]

    monkeypatch.setattr(module, "_fetch_volatility_map", fake_fetch_volatility_map)
    _CACHE.clear()
    _VOLATILITY_MAP_CACHE["_at"] = 0.0
    _VOLATILITY_MAP_CACHE["items"] = None

    matches = [
        {
            "symbol": "ACEUSDT",
            "frequency": "1h",
            "gateType": "di",
            "status": "FORMATION_ABOVE",
            "signalKind": "event",
            "eventAt": "2026-09-01T12:00:00+08:00",  # T2 早于入选时间
        },
        {
            "symbol": "ACEUSDT",
            "frequency": "1h",
            "gateType": "di",
            "status": "FORMATION_ABOVE",
            "signalKind": "event",
            "eventAt": "2026-09-01T22:00:00+08:00",  # 入选后形成
        },
    ]
    annotated = await annotate_matches(matches)
    assert len(annotated) == 1
    assert annotated[0]["eventAt"] == "2026-09-01T22:00:00+08:00"
    assert annotated[0]["suggestedLeverage"] == 10

@pytest.mark.asyncio
async def test_annotate_matches_adds_leverage_fields(monkeypatch):
    async def fake_fetch_volatility_map():
        return [
            {"sym": "ETHUSDT", "atrPct": 1.0, "dayRangePct": 2.0, "volatilityPct": 2.0},
            {"sym": "TUTUSDT", "atrPct": 3.0, "dayRangePct": 20.0, "volatilityPct": 20.0},
        ]

    monkeypatch.setattr(module, "_fetch_volatility_map", fake_fetch_volatility_map)
    _CACHE.clear()
    _VOLATILITY_MAP_CACHE["_at"] = 0.0
    _VOLATILITY_MAP_CACHE["items"] = None

    matches = [
        {"symbol": "ETHUSDT", "frequency": "15m"},
        {"symbol": "TUTUSDT", "frequency": "15m"},
        {"symbol": "UNKNOWNUSDT", "frequency": "15m"},
    ]
    annotated = await annotate_matches(matches)
    assert annotated[0]["suggestedLeverage"] == 50
    assert annotated[0]["volatilityPct"] == 2.0
    assert annotated[1]["suggestedLeverage"] == 10  # 高波动 -> 下限 10x
    assert annotated[1]["volatilityPct"] == 20.0
    assert "suggestedLeverage" not in annotated[2]
