"""v4 开门后子级首次 RSI 攻击因子回归测试。"""
from app.factors.v4_common import _match_gate_rsi_first
from app.strategy_model import trigger_filter_fields


def _snapshot():
    return {
        "sym": "RB0",
        "name": "螺纹钢",
        "freq": "15m",
        "last_bar_time": "2026-09-29 11:30:00",
        "computed_at": "2026-09-29T03:30:00+00:00",
        "factors": {
            "gate_rsi_first": [
                {
                    "gateKey": "jinmen2323_RB0_15m_di_0929_1030",
                    "gateType": "di",
                    "gateFreq": "15m",
                    "gateOpenAt": "2026-09-29 10:30:00",
                    "childFreq": "5m",
                    "direction": "short",
                    "threshold": 20.0,
                    "firstAttackTime": "2026-09-29 11:30:00",
                    "firstAttackIdx": 12,
                    "firstAttackNow": True,
                    "childRsi": 18.4,
                    "childRsiLast": 18.4,
                },
                {
                    "gateKey": "jinmen2323_RB0_15m_di_0928_0900",
                    "gateType": "di",
                    "gateFreq": "15m",
                    "gateOpenAt": "2026-09-28 09:00:00",
                    "childFreq": "5m",
                    "direction": "short",
                    "threshold": 20.0,
                    "firstAttackTime": "2026-09-28 09:25:00",
                    "firstAttackIdx": 5,
                    "firstAttackNow": False,
                    "childRsi": 18.0,
                    "childRsiLast": 44.0,
                },
            ],
        },
    }


def test_first_attack_now_only():
    cells = _match_gate_rsi_first(
        _snapshot(),
        {"directions": ["short"], "events": ["now"]},
        "futures_gate_rsi_first",
        "futures",
    )
    assert len(cells) == 1
    assert cells[0]["gateKey"].startswith("jinmen2323_RB0_15m_di_0929")
    assert cells[0]["mode"] == "now"
    assert cells[0]["firstAttackNow"] is True


def test_first_attack_elapsed_only():
    cells = _match_gate_rsi_first(
        _snapshot(),
        {"directions": ["short"], "events": ["elapsed"]},
        "futures_gate_rsi_first",
        "futures",
    )
    assert len(cells) == 1
    assert cells[0]["mode"] == "elapsed"


def test_first_attack_gate_type_filter():
    cells = _match_gate_rsi_first(
        _snapshot(),
        {"gateTypes": ["tian"], "events": ["now", "elapsed"]},
        "futures_gate_rsi_first",
        "futures",
    )
    assert cells == []


def test_first_attack_event_id_stable_per_gate():
    cells = _match_gate_rsi_first(
        _snapshot(),
        {"directions": ["short"], "events": ["now", "elapsed"]},
        "futures_gate_rsi_first",
        "futures",
    )
    ids = [cell["eventId"] for cell in cells]
    assert len(ids) == len(set(ids)) == 2
    assert all("gate_rsi_first" in value for value in ids)


def test_gate_rsi_first_is_trigger_factor():
    assert trigger_filter_fields("futures_gate_rsi_first") == frozenset({"events"})
