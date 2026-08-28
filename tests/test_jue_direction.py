import importlib

import pytest

from app.factors.base import FactorContext

module = importlib.import_module("app.factors.jue_direction")

SAMPLE = {
    "updated": "11:33:25",
    "updated_at": "2026-08-28T03:34:07.006938+00:00",
    "contracts": 2,
    "matched_cells": 7,
    "state_rules": [
        {"state": "80诀", "thr": 80, "broken": False, "direction": "long", "side": "多"},
        {"state": "80诀破诀", "thr": 80, "broken": True, "direction": "short", "side": "空"},
        {"state": "20诀", "thr": 20, "broken": False, "direction": "short", "side": "空"},
        {"state": "20诀破诀", "thr": 20, "broken": True, "direction": "long", "side": "多"},
        {"state": "无诀", "thr": None, "broken": False, "direction": None, "side": None},
    ],
    "sectors": [
        {
            "name": "贵金属",
            "items": [
                {
                    "sym": "AU0",
                    "name": "黄金",
                    "label": "黄金 AU2610",
                    "cells": [
                        {"freq": "5m", "state": "80诀", "direction": "long", "thr": 80, "price": 992.0, "broken": False, "gap": False, "pending": False, "rsi3": 63.0, "walk_state": "走B", "walk_code": "B", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "15m", "state": "80诀破诀", "direction": "short", "thr": 80, "price": 996.0, "broken": True, "gap": False, "pending": False, "rsi3": 66.0, "walk_state": "走B", "walk_code": "B", "walk_mark": "80破·走B", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "1h", "state": "20诀破诀", "direction": "long", "thr": 20, "price": 999.0, "broken": True, "gap": False, "pending": False, "rsi3": 29.0, "walk_state": "走2", "walk_code": "2", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": True},
                        {"freq": "1d", "state": "无诀", "direction": None, "thr": None, "price": None, "broken": False, "gap": False, "pending": True, "rsi3": 54.0, "walk_state": "走1", "walk_code": "1", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": False},
                    ],
                }
            ],
        },
        {
            "name": "有色金属",
            "items": [
                {
                    "sym": "CU0",
                    "name": "沪铜",
                    "label": "沪铜 CU2610",
                    "cells": [
                        {"freq": "5m", "state": "20诀", "direction": "short", "thr": 20, "price": 108780.0, "broken": False, "gap": False, "pending": False, "rsi3": 44.0, "walk_state": "走2", "walk_code": "2", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "15m", "state": "80诀", "direction": "long", "thr": 80, "price": None, "broken": False, "gap": False, "pending": True, "rsi3": 83.0, "walk_state": "走1", "walk_code": "1", "walk_mark": "", "pair_confirm_prev": False, "pair_confirm_next": False},
                        {"freq": "1M", "state": "80诀破诀", "direction": "short", "thr": 80, "price": 128800.0, "broken": True, "gap": False, "pending": False, "rsi3": 41.0, "walk_state": "走B", "walk_code": "B", "walk_mark": "80破·走B", "pair_confirm_prev": False, "pair_confirm_next": False},
                    ],
                }
            ],
        },
    ],
}


@pytest.fixture()
def patch_source(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return SAMPLE

    monkeypatch.setattr(module, "_fetch_raw", fake_fetch)
    return SAMPLE


@pytest.mark.asyncio
async def test_jue_direction_default_overview(patch_source):
    result = await module.jue_direction.handler({}, FactorContext(token_id=1))
    assert result["factorKey"] == "jue_direction"
    assert result["details"]["matchedCells"] == 6  # 默认排除 1 个“无诀”
    assert all(c["updatedAt"] == SAMPLE["updated_at"] for c in result["details"]["cells"])
    assert result["details"]["counts"]["long"] == 3
    assert result["details"]["counts"]["short"] == 3
    assert result["details"]["counts"]["broken"] == 3
    assert result["signal"] == "MIXED"
    assert result["score"] == 50.0
    assert result["summary"].startswith("筛选范围内共 6 个诀单元")


@pytest.mark.asyncio
async def test_jue_direction_filter_by_state(patch_source):
    result = await module.jue_direction.handler(
        {"states": ["80诀破诀"], "limit": 10},
        FactorContext(token_id=1),
    )
    cells = result["details"]["cells"]
    assert [c["symbol"] for c in cells] == ["AU0", "CU0"]
    assert all(c["state"] == "80诀破诀" for c in cells)
    assert result["signal"] == "SHORT"
    assert result["score"] == 0.0


@pytest.mark.asyncio
async def test_jue_direction_filter_by_symbol_and_broken(patch_source):
    result = await module.jue_direction.handler(
        {"symbols": ["AU0"]},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 3
    assert result["signal"] == "LONG"

    result = await module.jue_direction.handler(
        {"broken": True, "limit": 2},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 3
    assert result["details"]["returnedCells"] == 2
    assert all(c["broken"] for c in result["details"]["cells"])
    assert result["signal"] == "SHORT"


@pytest.mark.asyncio
async def test_jue_direction_filter_by_walk_code_and_mark(patch_source):
    result = await module.jue_direction.handler(
        {"walkCodes": ["2"], "limit": 10},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 2
    assert all(c["walkCode"] == "2" for c in result["details"]["cells"])

    result = await module.jue_direction.handler(
        {"walkMarks": ["80破·走B"], "limit": 10},
        FactorContext(token_id=1),
    )
    assert result["details"]["matchedCells"] == 2
    assert all(c["walkMark"] == "80破·走B" for c in result["details"]["cells"])


@pytest.mark.asyncio
async def test_jue_direction_empty_result(patch_source):
    result = await module.jue_direction.handler(
        {"symbols": ["ZZ0"]},
        FactorContext(token_id=1),
    )
    assert result["signal"] == "NONE"
    assert result["details"]["matchedCells"] == 0
    assert result["summary"].startswith("当前筛选范围内没有")


def test_jue_direction_factor_api_flow(client, token_headers, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return SAMPLE

    monkeypatch.setattr(module, "_fetch_raw", fake_fetch)

    listed = client.get("/api/v1/factors", headers=token_headers).json()["data"]["factors"]
    keys = [f["factorKey"] for f in listed]
    assert "dimen_gate_signal" in keys
    assert "jue_direction" in keys

    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={
            "factorKey": "jue_direction",
            "params": {"frequencies": ["5m", "15m"], "symbols": ["AU0"]},
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["factorKey"] == "jue_direction"
    assert data["details"]["returnedCells"] == 2
    assert [c["symbol"] for c in data["details"]["cells"]] == ["AU0", "AU0"]
