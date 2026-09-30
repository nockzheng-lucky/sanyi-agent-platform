"""v4 BOLL(52,2) 因子回归测试。"""
from app.factors.v4_common import _match_boll
from app.strategy_model import strip_trigger_filters, trigger_filter_fields


def _snapshot():
    return {
        "sym": "RB0",
        "name": "螺纹钢",
        "freq": "1m",
        "last_bar_time": "2026-09-29 09:05:00",
        "computed_at": "2026-09-29T01:05:00+00:00",
        "factors": {
            "price": {"close": 4000.0},
            "boll": {
                "upper": 4010.0,
                "mid": 3990.0,
                "lower": 3970.0,
                "upper_side": "below",
                "mid_side": "above",
                "lower_side": "above",
                "upper_cross": "cross_down",
                "mid_cross": "none",
                "lower_cross": "none",
            },
        },
    }


def test_boll_upper_cross_down_matches():
    cell = _match_boll(
        _snapshot(), {"upperCrosses": ["cross_down"]}, "futures_boll", "futures"
    )
    assert cell is not None
    assert cell["upperCross"] == "cross_down"
    assert cell["upper"] == 4010.0


def test_boll_upper_cross_up_does_not_match():
    assert (
        _match_boll(
            _snapshot(), {"upperCrosses": ["cross_up"]}, "futures_boll", "futures"
        )
        is None
    )


def test_boll_lower_cross_up_does_not_match_when_none():
    assert (
        _match_boll(
            _snapshot(), {"lowerCrosses": ["cross_up"]}, "futures_boll", "futures"
        )
        is None
    )


def test_boll_position_inside():
    cell = _match_boll(
        _snapshot(), {"bollPositions": ["inside"]}, "futures_boll", "futures"
    )
    assert cell is not None
    assert cell["upperSide"] == "below"
    assert cell["lowerSide"] == "above"


def test_boll_missing_group_returns_none():
    snap = _snapshot()
    del snap["factors"]["boll"]
    assert (
        _match_boll(snap, {}, "futures_boll", "futures")
        is None
    )


def test_boll_cross_fields_are_trigger_fields():
    assert trigger_filter_fields("futures_boll") == frozenset({
        "upperCrosses", "midCrosses", "lowerCrosses"
    })


def test_boll_pool_strips_cross_filters():
    condition = {
        "factorKey": "futures_boll",
        "filters": {
            "frequencies": ["1m"],
            "upperCrosses": ["cross_down"],
        },
    }
    stripped = strip_trigger_filters(condition)
    assert stripped["filters"] == {"frequencies": ["1m"]}
