"""v4 between 条件与组合订阅 gateType 对齐回归测试。"""
from app.composite_evaluator import _match_context_rows
from app.factors.v4_common import _match_ma, _match_spatial


def _snapshot():
    return {
        "sym": "AP0",
        "name": "苹果",
        "freq": "15m",
        "last_bar_time": "2026-09-28 15:00:00",
        "computed_at": "2026-09-28T07:00:00+00:00",
        "factors": {
            "price": {"close": 7000.0},
            "ma": {
                "ma52_price_side": "above",
                "ma208_price_side": "above",
                "ma832_price_side": "below",
                "ma52_price_cross": "none",
                "ma208_price_cross": "none",
                "ma832_price_cross": "none",
                "ma52_ma208_cross": "none",
                "ma52_ma832_cross": "none",
                "ma208_ma832_cross": "none",
            },
            "spatial": {
                "dimen_above_ma52": True, "dimen_below_ma52": False,
                "dimen_above_ma208": True, "dimen_below_ma208": False,
                "dimen_above_ma832": False, "dimen_below_ma832": True,
                "tianmen_above_ma52": False, "tianmen_below_ma52": True,
                "tianmen_above_ma208": False, "tianmen_below_ma208": True,
                "tianmen_above_ma832": False, "tianmen_below_ma832": True,
            },
        },
    }


def test_spatial_between_is_and_per_gate():
    snap = _snapshot()
    cells = _match_spatial(
        snap,
        {
            "gateTypes": ["di", "tian"],
            "between": [{"above": "ma208", "below": "ma832"}],
        },
        "futures_spatial",
        "futures",
    )
    # 只有地门满足 MA832 > 门价 > MA208；天门全部在两条均线下方，不应命中。
    assert len(cells) == 1
    assert cells[0]["gateType"] == "di"
    assert cells[0]["between"] == {"above": "ma208", "below": "ma832"}
    assert cells[0]["eventId"].endswith(":ma208:ma832:between")


def test_spatial_between_does_not_fallback_to_positions_or():
    snap = _snapshot()
    cells = _match_spatial(
        snap,
        {
            "gateTypes": ["di"],
            "between": [{"above": "ma832", "below": "ma208"}],  # 反向，不应命中
            "positions": ["above", "below"],
        },
        "futures_spatial",
        "futures",
    )
    assert cells == []


def test_spatial_legacy_positions_still_works():
    snap = _snapshot()
    cells = _match_spatial(
        snap,
        {"gateTypes": ["di"], "maNames": ["ma208"], "positions": ["above"]},
        "futures_spatial",
        "futures",
    )
    assert len(cells) == 1
    assert cells[0]["maName"] == "ma208"


def test_ma_between_price_side():
    snap = _snapshot()
    cell = _match_ma(
        snap,
        {"between": [{"above": "ma208", "below": "ma832"}]},
        "futures_ma",
        "futures",
    )
    assert cell is not None
    assert cell["between"] == {"above": "ma208", "below": "ma832"}
    assert "between=ma208:ma832" in cell["eventId"]


def test_ma_between_reverse_does_not_match():
    snap = _snapshot()
    cell = _match_ma(
        snap,
        {"between": [{"above": "ma832", "below": "ma208"}]},
        "futures_ma",
        "futures",
    )
    assert cell is None


def test_context_rows_align_gate_type():
    primary_row = {
        "symbol": "AP0",
        "frequency": "15m",
        "kind": "cells",
        "factorKey": "futures_door",
        "payload": {"symbol": "AP0", "frequency": "15m", "gateType": "di"},
    }
    context_eval = {
        "condition": {"frequencyOffset": 0, "frequencyOffsets": [0], "targetFrequencies": []},
        "rows": [
            {"symbol": "AP0", "frequency": "15m", "kind": "cells",
             "payload": {"symbol": "AP0", "frequency": "15m", "gateType": "tian",
                         "between": {"above": "ma208", "below": "ma832"}}},
            {"symbol": "AP0", "frequency": "15m", "kind": "cells",
             "payload": {"symbol": "AP0", "frequency": "15m", "gateType": "di",
                         "between": {"above": "ma208", "below": "ma832"}}},
        ],
    }
    matches = _match_context_rows(primary_row, context_eval)
    assert len(matches) == 1
    assert matches[0]["gateType"] == "di"
