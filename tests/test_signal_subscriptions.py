import importlib
import uuid

import pytest

from app.accounts import create_user, create_user_session, issue_user_key
from app.agent.filter_store import patch_filters
from app.signal_subscriptions import (
    create_signal_subscription,
    delete_signal_subscription,
    list_signal_subscriptions,
)

jue_module = importlib.import_module("app.factors.jue_direction")


@pytest.fixture()
def user_record():
    phone = "137" + uuid.uuid4().hex[:8]
    user = create_user(phone, "password123")
    key = issue_user_key(user["id"], "subscription-test-key", rate_limit_per_min=1000)
    return {
        "id": key["id"],
        "_table": "user_keys",
        "_user_id": user["id"],
        "_key_id": key["id"],
    }


def test_signal_subscription_crud(user_record):
    created = create_signal_subscription(
        user_record,
        "jue_direction",
        {"frequencies": ["15m", "1h"], "states": ["20诀破诀"], "walkCodes": ["2"]},
        "20诀破诀·走2",
    )
    assert created["factorKey"] == "jue_direction"
    assert created["filters"]["walkCodes"] == ["2"]

    rows = list_signal_subscriptions(user_record["_user_id"])
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]

    assert delete_signal_subscription(user_record["_user_id"], created["id"]) is True
    assert list_signal_subscriptions(user_record["_user_id"]) == []


def test_signal_subscriptions_api_matches_and_delete(client, user_record, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return {
            "updated_at": "2026-08-28T03:34:07.006938+00:00",
            "state_rules": [],
            "sectors": [
                {
                    "name": "贵金属",
                    "items": [
                        {
                            "sym": "AU0",
                            "name": "黄金",
                            "label": "黄金 AU2610",
                            "cells": [
                                {
                                    "freq": "15m",
                                    "state": "80诀破诀",
                                    "direction": "short",
                                    "thr": 80,
                                    "price": 996.0,
                                    "broken": True,
                                    "gap": False,
                                    "pending": False,
                                    "rsi3": 66.0,
                                    "walk_state": "走B",
                                    "walk_code": "B",
                                    "walk_mark": "80破·走B",
                                    "pair_confirm_prev": False,
                                    "pair_confirm_next": False,
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(jue_module, "_fetch_raw", fake_fetch)

    # 页面登录会话 + 默认 Key。
    raw_session = create_user_session(user_record["_user_id"])
    client.cookies.set("sanyi_user", raw_session)

    created = create_signal_subscription(
        user_record,
        "jue_direction",
        {"frequencies": ["15m"], "states": ["80诀破诀"]},
        "80诀破诀15m",
    )

    resp = client.get("/api/v1/signal-subscriptions")
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()["data"]["subscriptions"]] == [created["id"]]

    resp = client.get("/api/v1/signal-subscriptions/matches")
    assert resp.status_code == 200
    subs = resp.json()["data"]["subscriptions"]
    assert len(subs) == 1
    assert subs[0]["factorKey"] == "jue_direction"
    assert [c["symbol"] for c in subs[0]["matches"]] == ["AU0"]

    resp = client.delete(f"/api/v1/signal-subscriptions/{created['id']}")
    assert resp.status_code == 200
    assert client.get("/api/v1/signal-subscriptions").json()["data"]["subscriptions"] == []


@pytest.mark.asyncio
async def test_execute_tool_creates_subscription_from_current_filters(user_record):
    from app.agent.tools import execute_tool

    patch_filters(
        user_record,
        "jue_direction",
        {"frequencies": ["15m", "1h"], "states": ["20诀破诀"], "walkCodes": ["2"]},
    )
    result = await execute_tool(
        "sanyi_create_subscription",
        {"factorKey": "jue_direction"},
        user_record,
    )
    assert "error" not in result
    assert result["subscription"]["filters"]["walkCodes"] == ["2"]

    listed = await execute_tool("sanyi_list_subscriptions", {}, user_record)
    assert len(listed["subscriptions"]) == 1
    sub_id = listed["subscriptions"][0]["id"]

    deleted = await execute_tool("sanyi_delete_subscription", {"subscriptionId": sub_id}, user_record)
    assert deleted["subscriptionId"] == sub_id
