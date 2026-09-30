"""破诀后 -2 级别首次 RSI 攻击因子回归测试。"""
from app.factors.v4_common import _match_jue_rsi_first
from app.strategy_model import trigger_filter_fields


def _snapshot():
    return {
        "sym": "C0",
        "name": "玉米",
        "freq": "15m",
        "last_bar_time": "2026-09-30 09:15:00",
        "computed_at": "2026-09-30T01:15:00+00:00",
        "factors": {
            "jue_rsi_first": [
                {
                    "pattern": "walk2_break20",
                    "patternLabel": "成走2·破20",
                    "jueThr": 20,
                    "jueFormationWalk": "走2",
                    "jueBreakTime": "2026-09-30 09:15:00",
                    "gateFreq": "15m",
                    "childFreq": "1m",
                    "side": "long",
                    "attackDirection": "short",
                    "firstAttackTime": "2026-09-30 09:15:00",
                    "firstAttackNow": True,
                    "childRsi": 18.8,
                    "childRsiLast": 18.8,
                },
                {
                    "pattern": "walkB_break80",
                    "patternLabel": "成走B·破80",
                    "jueThr": 80,
                    "jueFormationWalk": "走B",
                    "jueBreakTime": "2026-09-29 22:00:00",
                    "gateFreq": "15m",
                    "childFreq": "1m",
                    "side": "short",
                    "attackDirection": "long",
                    "firstAttackTime": "2026-09-29 22:05:00",
                    "firstAttackNow": False,
                    "childRsi": 82.0,
                    "childRsiLast": 55.0,
                },
            ]
        },
    }


def test_jue_rsi_first_long_now():
    cells = _match_jue_rsi_first(
        _snapshot(),
        {"patterns": ["walk2_break20"], "events": ["now"]},
        "futures_jue_rsi_first",
        "futures",
    )
    assert len(cells) == 1
    assert cells[0]["pattern"] == "walk2_break20"
    assert cells[0]["side"] == "long"
    assert cells[0]["firstAttackNow"] is True


def test_jue_rsi_first_short_mirror_elapsed():
    cells = _match_jue_rsi_first(
        _snapshot(),
        {"patterns": ["walkB_break80"], "events": ["elapsed"]},
        "futures_jue_rsi_first",
        "futures",
    )
    assert len(cells) == 1
    assert cells[0]["pattern"] == "walkB_break80"
    assert cells[0]["side"] == "short"
    assert cells[0]["firstAttackNow"] is False


def test_jue_rsi_first_event_id_stable_per_pattern():
    cells = _match_jue_rsi_first(
        _snapshot(),
        {"events": ["now", "elapsed"]},
        "futures_jue_rsi_first",
        "futures",
    )
    ids = [cell["eventId"] for cell in cells]
    assert len(ids) == len(set(ids)) == 2


def test_jue_rsi_first_is_trigger_factor():
    assert trigger_filter_fields("futures_jue_rsi_first") == frozenset({"events"})
