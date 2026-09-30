"""组合订阅条件层测试。

覆盖手头交接文档 5.5 列的六个场景：
- 单条件兼容旧订阅
- 双条件 AND：symbol + parent frequency
- below / above 方向过滤
- 条件不匹配时不输出
- /api/v1/signal-subscriptions/matches 返回组合结果
- Pushplus worker 对组合结果只推一次
- Agent 工具 sanyi_create_subscription 接受 conditions
"""
import importlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.accounts import create_user, create_user_session, issue_user_key
from app.composite_evaluator import evaluate_subscription
from app.db import init_db
from app.factor_registry import registry
from app.signal_subscriptions import (
    create_signal_subscription,
    get_signal_subscription,
    list_signal_subscriptions,
)

combo_module = importlib.import_module("app.factors.wave_jue_combo")
gate_module = importlib.import_module("app.factors.gate_condition")

LONG_COMBO_PAYLOAD = {
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
                        {
                            "freq": "15m",
                            "state": "20诀破诀",
                            "direction": "long",
                            "side": "多",
                            "thr": 20,
                            "price": 3600.0,
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
                            "price": 3700.0,
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
                },
                {
                    "sym": "AG0",
                    "name": "白银",
                    "label": "白银 AG2612",
                    "cells": [
                        {
                            "freq": "15m",
                            "state": "20诀破诀",
                            "direction": "long",
                            "side": "多",
                            "thr": 20,
                            "price": 3000.0,
                            "broken": True,
                            "gap": False,
                            "pending": False,
                            "rsi3": 28.0,
                            "walk_state": "走2",
                            "walk_code": "2",
                            "walk_mark": "20破·走2",
                            "pair_confirm_prev": False,
                            "pair_confirm_next": False,
                        },
                    ],
                },
            ],
        }
    ],
}

SHORT_COMBO_PAYLOAD = {
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
                        {
                            "freq": "15m",
                            "state": "80诀破诀",
                            "direction": "short",
                            "side": "空",
                            "thr": 80,
                            "price": 3600.0,
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

LONG_GATE_PAYLOAD = {
    "updated": "13:15:52",
    "gates": [
        {
            "sym": "RB0",
            "name": "螺纹钢",
            "freq": "1h",
            "type": "di",
            "gate_price": 3500.0,
            "current_price": 3610.0,
            "live_status": "已开",
            "formation": "门下",
            "is_first": True,
            "key": "rb-di-1h-below",
            "t1_str": "08/30 10:00",
            "t2_str": "08/30 11:00",
            "cross_str": "08/30 12:00",
            "x_above": False,
            "x_crosses": 1,
        },
        {
            "sym": "RB0",
            "name": "螺纹钢",
            "freq": "1h",
            "type": "di",
            "gate_price": 3700.0,
            "current_price": 3610.0,
            "live_status": "已开",
            "formation": "门下",
            "is_first": False,
            "key": "rb-di-1h-above",
            "t1_str": "",
            "t2_str": "",
            "cross_str": "",
            "x_above": None,
            "x_crosses": None,
        },
        {
            "sym": "RB0",
            "name": "螺纹钢",
            "freq": "15m",
            "type": "di",
            "gate_price": 3500.0,
            "current_price": 3610.0,
            "live_status": "已开",
            "formation": "门下",
            "is_first": False,
            "key": "rb-di-15m-wrong-freq",
        },
    ],
}

SHORT_GATE_PAYLOAD = {
    "updated": "13:15:52",
    "gates": [
        {
            "sym": "RB0",
            "name": "螺纹钢",
            "freq": "1h",
            "type": "tian",
            "gate_price": 3700.0,
            "current_price": 3610.0,
            "live_status": "已关",
            "formation": "门上",
            "is_first": False,
            "key": "rb-tian-1h-above",
        },
        {
            "sym": "RB0",
            "name": "螺纹钢",
            "freq": "1h",
            "type": "tian",
            "gate_price": 3500.0,
            "current_price": 3610.0,
            "live_status": "无动作·门下",
            "formation": "门下",
            "is_first": False,
            "key": "rb-tian-1h-below",
        },
    ],
}


@pytest.fixture()
def user_record():
    init_db()
    phone = "139" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    key = issue_user_key(user["id"], "composite-test-key", rate_limit_per_min=1000)
    return {
        "id": key["id"],
        "_table": "user_keys",
        "_user_id": user["id"],
        "_key_id": key["id"],
    }


def _long_conditions():
    return [
        {
            "factorKey": "wave_jue_combo",
            "filters": {"combos": ["walk2_break20"], "frequencies": ["15m"]},
        },
        {
            "factorKey": "gate_condition",
            "filters": {"gateTypes": ["di"], "liveStatuses": ["已开", "无动作·门上"]},
            "join": {"frequencyOffset": 1, "sideRule": "below"},
        },
    ]


@pytest.mark.asyncio
async def test_single_condition_compatible_with_legacy_subscription(user_record, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return LONG_COMBO_PAYLOAD

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_fetch)

    created = create_signal_subscription(
        user_record,
        "wave_jue_combo",
        {"combos": ["walk2_break20"], "frequencies": ["15m"]},
        "走2破20诀15m",
    )
    assert created["factorKey"] == "wave_jue_combo"
    assert len(created["conditions"]) == 1
    assert created["conditions"][0]["role"] == "primary"
    assert created["conditions"][0]["filters"]["combos"] == ["walk2_break20"]

    item = await evaluate_subscription(user_record, created)
    assert item["error"] is None
    assert {m["symbol"] for m in item["matches"]} == {"RB0", "AG0"}
    assert all(m["comboKey"] == "walk2_break20" for m in item["matches"])


@pytest.mark.asyncio
async def test_dual_condition_joins_symbol_parent_frequency_and_below(user_record, monkeypatch):
    async def fake_combo(*args, **kwargs):
        return LONG_COMBO_PAYLOAD

    async def fake_gates(*args, **kwargs):
        return LONG_GATE_PAYLOAD

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_combo)
    monkeypatch.setattr(gate_module, "_fetch_gates", fake_gates)

    created = create_signal_subscription(
        user_record,
        conditions=_long_conditions(),
        name="走2破20诀 + 下方有效+1级地门",
    )
    assert created["factorKey"] == "wave_jue_combo"
    assert [c["factorKey"] for c in created["conditions"]] == ["wave_jue_combo", "gate_condition"]
    assert created["conditions"][1]["frequencyOffset"] == 1
    assert created["conditions"][1]["sideRule"] == "below"

    item = await evaluate_subscription(user_record, created)
    assert item["error"] is None
    assert item["conditionErrors"] == []
    assert len(item["matches"]) == 1
    match = item["matches"][0]
    assert match["symbol"] == "RB0"
    assert match["frequency"] == "15m"
    assert match["comboKey"] == "walk2_break20"
    assert match["composite"] is True
    assert len(match["contexts"]) == 1
    context = match["contexts"][0]
    assert context["factorKey"] == "gate_condition"
    assert [g["key"] for g in context["matches"]] == ["rb-di-1h-below"]


@pytest.mark.asyncio
async def test_side_rule_above_for_short_gate(user_record, monkeypatch):
    async def fake_combo(*args, **kwargs):
        return SHORT_COMBO_PAYLOAD

    async def fake_gates(*args, **kwargs):
        return SHORT_GATE_PAYLOAD

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_combo)
    monkeypatch.setattr(gate_module, "_fetch_gates", fake_gates)

    created = create_signal_subscription(
        user_record,
        conditions=[
            {
                "factorKey": "wave_jue_combo",
                "filters": {"combos": ["walkB_break80"], "frequencies": ["15m"]},
            },
            {
                "factorKey": "gate_condition",
                "filters": {"gateTypes": ["tian"], "liveStatuses": ["已关", "无动作·门下"]},
                "frequencyOffset": 1,
                "sideRule": "above",
            },
        ],
        name="走B破80诀 + 上方有效天门",
    )
    item = await evaluate_subscription(user_record, created)
    assert len(item["matches"]) == 1
    assert item["matches"][0]["direction"] == "short"
    assert item["matches"][0]["contexts"][0]["matches"][0]["key"] == "rb-tian-1h-above"


@pytest.mark.asyncio
async def test_condition_not_matching_outputs_no_signal(user_record, monkeypatch):
    async def fake_combo(*args, **kwargs):
        return LONG_COMBO_PAYLOAD

    async def fake_gates(*args, **kwargs):
        return {
            "updated": "13:15:52",
            "gates": [
                {
                    "sym": "RB0",
                    "name": "螺纹钢",
                    "freq": "1h",
                    "type": "di",
                    "gate_price": 3800.0,
                    "current_price": 3610.0,
                    "live_status": "已开",
                    "formation": "门下",
                    "is_first": True,
                    "key": "rb-di-above-price",
                }
            ],
        }

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_combo)
    monkeypatch.setattr(gate_module, "_fetch_gates", fake_gates)

    created = create_signal_subscription(user_record, conditions=_long_conditions(), name="不匹配组合")
    item = await evaluate_subscription(user_record, created)
    assert item["matches"] == []
    assert item["signal"] == "NONE"
    assert "0 条" in item["summary"]


def test_composite_subscription_matches_api(client, user_record, monkeypatch):
    async def fake_combo(*args, **kwargs):
        return LONG_COMBO_PAYLOAD

    async def fake_gates(*args, **kwargs):
        return LONG_GATE_PAYLOAD

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_combo)
    monkeypatch.setattr(gate_module, "_fetch_gates", fake_gates)

    raw_session = create_user_session(user_record["_user_id"])
    client.cookies.set("sanyi_user", raw_session)
    created = create_signal_subscription(
        user_record,
        conditions=_long_conditions(),
        name="走2破20诀 + 下方有效+1级地门",
    )

    listed = client.get("/api/v1/signal-subscriptions").json()["data"]["subscriptions"]
    assert listed[0]["conditions"][1]["sideRule"] == "below"

    resp = client.get("/api/v1/signal-subscriptions/matches")
    assert resp.status_code == 200
    subs = resp.json()["data"]["subscriptions"]
    assert len(subs) == 1
    sub = subs[0]
    assert sub["id"] == created["id"]
    assert len(sub["matches"]) == 1
    assert sub["matches"][0]["contexts"][0]["matches"][0]["key"] == "rb-di-1h-below"


@pytest.mark.asyncio
async def test_pusher_pushes_composite_match_once(user_record, monkeypatch):
    from app.engine import subscription_pusher as pusher_module
    from app.push_channels import set_pushplus_token

    state = {"combo": LONG_COMBO_PAYLOAD, "gates": LONG_GATE_PAYLOAD}

    async def fake_combo(*args, **kwargs):
        return state["combo"]

    async def fake_gates(*args, **kwargs):
        return state["gates"]

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_combo)
    monkeypatch.setattr(gate_module, "_fetch_gates", fake_gates)

    created = create_signal_subscription(
        user_record,
        conditions=_long_conditions(),
        name="组合订阅推送测试",
    )
    set_pushplus_token(user_record["_user_id"], "composite-pushplus-token")
    sent = []

    async def fake_send(title, content, token=""):
        assert token == "composite-pushplus-token"
        sent.append({"title": title, "content": content})
        return True, 200

    monkeypatch.setattr(pusher_module, "send_pushplus_checked", fake_send)

    pusher = pusher_module.SubscriptionPusher()
    # 首轮 baseline：订阅时已有的组合结果不推。
    assert await pusher.run_once() == 0
    assert sent == []

    # 同一组合结果（eventId 稳定）不重复推送。
    registry._cache.clear()
    assert await pusher.run_once() == 0
    assert sent == []

    # 新出现的品种 + 门组合，只推新增一条。
    state["gates"] = {
        "updated": "13:20:00",
        "gates": LONG_GATE_PAYLOAD["gates"]
        + [
            {
                "sym": "AG0",
                "name": "白银",
                "freq": "1h",
                "type": "di",
                "gate_price": 2900.0,
                "current_price": 3010.0,
                "live_status": "已开",
                "formation": "门下",
                "is_first": False,
                "key": "ag-di-1h-below",
                "open_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    state["combo"] = {
        **LONG_COMBO_PAYLOAD,
        "sectors": [
            {
                "name": "黑色",
                "items": [
                    {
                        "sym": "AG0",
                        "name": "白银",
                        "label": "白银 AG2612",
                        "cells": [
                            {
                                "freq": "15m",
                                "state": "20诀破诀",
                                "direction": "long",
                                "side": "多",
                                "thr": 20,
                                "price": 3000.0,
                                "broken": True,
                                "gap": False,
                                "pending": False,
                                "rsi3": 28.0,
                                "walk_state": "走2",
                                "walk_code": "2",
                                "walk_mark": "20破·走2",
                                "pair_confirm_prev": False,
                                "pair_confirm_next": False,
                            },
                        ],
                    }
                ],
            }
        ],
    }
    registry._cache.clear()
    assert await pusher.run_once() == 1
    assert len(sent) == 1
    assert "白银" in sent[0]["content"]


@pytest.mark.asyncio
async def test_agent_tool_accepts_conditions(user_record):
    from app.agent.tools import build_tools, execute_tool

    tools = build_tools(for_record=user_record)
    create_tool = next(t for t in tools if t["function"]["name"] == "sanyi_create_subscription")
    assert "conditions" in create_tool["function"]["parameters"]["properties"]
    assert "factorKey" in create_tool["function"]["parameters"]["properties"]
    assert "required" not in create_tool["function"]["parameters"]

    result = await execute_tool(
        "sanyi_create_subscription",
        {
            "name": "期货今日门 + 地门事件",
            "conditions": [
                {
                    "factorKey": "futures_gate_signal",
                    "filters": {"frequencies": ["15m"], "gateTypes": ["tian"], "actions": ["open"]},
                },
                {
                    "factorKey": "dimen_gate_signal",
                    "filters": {"frequencies": ["15m"]},
                    "join": {"frequencyOffset": 1, "sideRule": "below"},
                },
            ],
        },
        user_record,
    )
    assert "error" not in result
    sub = result["subscription"]
    assert len(sub["conditions"]) == 2
    assert sub["conditions"][0]["role"] == "primary"
    assert sub["conditions"][1]["role"] == "context"
    assert sub["conditions"][1]["joinWith"] == "primary"
    assert sub["conditions"][1]["frequencyOffset"] == 1
    assert sub["conditions"][1]["sideRule"] == "below"

    listed = await execute_tool("sanyi_list_subscriptions", {}, user_record)
    assert listed["subscriptions"][0]["conditions"][1]["factorKey"] == "dimen_gate_signal"


def test_conditions_accept_join_on_form(user_record):
    conditions = _long_conditions()
    conditions[1] = {
        "factorKey": "gate_condition",
        "filters": {"gateTypes": ["di"], "liveStatuses": ["已开", "无动作·门上"]},
        "joinOn": {"symbol": "symbol", "frequencyOffset": 1},
        "sideRule": "below",
    }
    created = create_signal_subscription(user_record, conditions=conditions, name="joinOn 写法")
    assert created["conditions"][1]["joinSymbol"] == "symbol"
    assert created["conditions"][1]["frequencyOffset"] == 1
    assert created["conditions"][1]["sideRule"] == "below"


def test_composite_condition_rejects_invisible_factor_for_normal_user(user_record):
    with pytest.raises(ValueError):
        create_signal_subscription(
            user_record,
            conditions=[
                {
                    "factorKey": "wave_jue_combo",
                    "filters": {"combos": ["walk2_break20"]},
                },
                {
                    "factorKey": "crypto_market",
                    "filters": {"frequencies": ["15m"]},
                },
            ],
        )


def test_composite_subscription_requires_one_primary(user_record):
    with pytest.raises(ValueError):
        create_signal_subscription(
            user_record,
            conditions=[
                {
                    "factorKey": "wave_jue_combo",
                    "filters": {"combos": ["walk2_break20"]},
                    "role": "context",
                },
                {
                    "factorKey": "gate_condition",
                    "filters": {"gateTypes": ["di"]},
                },
            ],
        )


def test_list_all_active_keeps_composite_conditions(user_record):
    created = create_signal_subscription(
        user_record,
        conditions=_long_conditions(),
        name="组合订阅列表",
    )
    rows = list_signal_subscriptions(user_record["_user_id"])
    assert rows[0]["id"] == created["id"]
    assert len(rows[0]["conditions"]) == 2


def test_legacy_subscription_without_child_rows_is_migrated_to_one_condition(user_record):
    from app.db import _now_iso, get_conn

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO signal_subscriptions(user_id, factor_key, filters_json, name,"
        " status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (
            user_record["_user_id"],
            "jue_direction",
            '{"frequencies": ["15m"]}',
            "旧版订阅",
            _now_iso(),
            _now_iso(),
        ),
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM subscription_conditions WHERE subscription_id = ?",
        (cur.lastrowid,),
    ).fetchone()["n"] == 0

    init_db()
    migrated = get_signal_subscription(user_record["_user_id"], int(cur.lastrowid))
    assert len(migrated["conditions"]) == 1
    assert migrated["conditions"][0]["role"] == "primary"
    assert migrated["conditions"][0]["filters"]["frequencies"] == ["15m"]


@pytest.mark.asyncio
async def test_context_condition_error_is_reported_without_breaking_subscription(user_record, monkeypatch):
    from fastapi import HTTPException

    async def fake_combo(*args, **kwargs):
        return LONG_COMBO_PAYLOAD

    async def fake_gates(*args, **kwargs):
        raise HTTPException(
            status_code=502,
            detail={"code": 502, "message": "门数据源不可用：测试", "data": None},
        )

    monkeypatch.setattr(combo_module, "_fetch_raw", fake_combo)
    monkeypatch.setattr(gate_module, "_fetch_gates", fake_gates)

    created = create_signal_subscription(user_record, conditions=_long_conditions(), name="容错组合")
    item = await evaluate_subscription(user_record, created)
    assert item["error"] is None
    assert item["matches"] == []
    assert item["signal"] == "NONE"
    assert len(item["conditionErrors"]) == 1
    assert item["conditionErrors"][0]["factorKey"] == "gate_condition"
    assert "门数据源不可用" in item["conditionErrors"][0]["error"]


def test_futures_gate_subscription_keeps_only_current_15_cycle():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.composite_evaluator import _beijing_trading_cycle_start, _filter_futures_gate_subscription

    cycle_start = _beijing_trading_cycle_start(datetime.now(ZoneInfo("Asia/Shanghai")))
    new_time = (cycle_start.replace(tzinfo=None) + timedelta(hours=2)).isoformat()
    old_time = (cycle_start.replace(tzinfo=None) - timedelta(hours=2)).isoformat()
    matches = [
        {"symbol": "NEW", "openAt": new_time + "+08:00"},
        {"symbol": "OLD", "openAt": old_time + "+08:00"},
        {"symbol": "NO_TIME", "openAt": None},
    ]
    kept = _filter_futures_gate_subscription({"factorKey": "gate_condition"}, matches)
    assert [m["symbol"] for m in kept] == ["NEW"]
