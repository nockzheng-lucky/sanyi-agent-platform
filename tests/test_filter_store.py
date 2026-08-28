import pytest

from app.agent.filter_store import (
    clear_filters,
    get_filter_state,
    merge_params,
    owner_key,
    patch_filters,
)
from app.agent.loop import _history_from
from app.agent.tools import execute_tool
from app.db import issue_token


def _record():
    record = issue_token(name="filter-test", quota_total=100000, rate_limit_per_min=1000)
    record["_table"] = "tokens"
    return record


def test_filter_patch_merge_and_clear():
    record = _record()
    assert owner_key(record) == "tokens:%s" % record["id"]

    state = patch_filters(record, "jue_direction", {"frequencies": ["15m"], "limit": 20})
    assert state["jue_direction"]["frequencies"] == ["15m"]

    state = patch_filters(record, "jue_direction", {"broken": True})
    assert state["jue_direction"]["frequencies"] == ["15m"]
    assert state["jue_direction"]["broken"] is True

    state = patch_filters(record, "jue_direction", {"limit": 5}, replace=True)
    assert state["jue_direction"] == {"limit": 5}

    merged = merge_params({"frequencies": ["15m"], "limit": 20}, {"limit": 5, "broken": None})
    assert merged == {"frequencies": ["15m"], "limit": 5}

    clear_filters(record, "jue_direction")
    assert get_filter_state(record) == {}


@pytest.mark.asyncio
async def test_execute_tool_applies_persisted_filters():
    record = _record()

    updated = await execute_tool(
        "sanyi_update_filters",
        {"factorKey": "dimen_gate_signal", "filters": {"frequencies": ["15m"], "limit": 3}},
        record,
    )
    assert updated["filters"]["dimen_gate_signal"]["frequencies"] == ["15m"]

    evaluated = await execute_tool(
        "sanyi_evaluate_factor",
        {"factorKey": "dimen_gate_signal", "params": {}},
        record,
    )
    assert evaluated["appliedFilters"]["frequencies"] == ["15m"]
    assert evaluated["appliedFilters"]["limit"] == 3

    # 当次显式参数优先，且不污染存量筛选条件。
    evaluated = await execute_tool(
        "sanyi_evaluate_factor",
        {"factorKey": "dimen_gate_signal", "params": {"limit": 1}},
        record,
    )
    assert evaluated["appliedFilters"]["frequencies"] == ["15m"]
    assert evaluated["appliedFilters"]["limit"] == 1
    assert get_filter_state(record)["dimen_gate_signal"]["limit"] == 3

    cleared = await execute_tool("sanyi_clear_filters", {"factorKey": "dimen_gate_signal"}, record)
    assert cleared["filters"] == {}


def test_history_includes_current_filters():
    history = _history_from(
        [{"role": "user", "content": "看看现在的情况"}],
        filter_state={"jue_direction": {"frequencies": ["15m"], "broken": True}},
    )
    prompt = history[0]["content"]
    assert "当前持久筛选条件" in prompt
    assert "jue_direction" in prompt
    assert "15m" in prompt
