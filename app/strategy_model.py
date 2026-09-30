"""策略订阅模式：区分“触发型”与“池型”，并支持从 trigger 条件推导基础池。

- trigger：primary 里带动作字段（上穿/下穿/开关门），只有动作发生那一刻发信号；
          背景条件（context）持续评估为基础池。
- pool：没有动作字段，条件本身就是一个持续存在的池子；入池即信号，出池自动移除。
- auto：按条件里的动作字段自动判定，新老订阅都无需迁移数据。
"""
from __future__ import annotations

from typing import Any, Dict, List

MODE_AUTO = "auto"
MODE_TRIGGER = "trigger"
MODE_POOL = "pool"
VALID_MODES = (MODE_AUTO, MODE_TRIGGER, MODE_POOL)

LAYER_EVENT = "event"
LAYER_POOL = "pool"
VALID_LAYERS = (LAYER_EVENT, LAYER_POOL)

LAYER_LABELS = {
    LAYER_EVENT: "事件条件",
    LAYER_POOL: "候选池",
}

MODE_LABELS = {
    MODE_TRIGGER: "触发型",
    MODE_POOL: "池型",
    MODE_AUTO: "自动",
}

# 绝对频率共 8 级：1m/5m/15m/30m/1h/1d/1w/1M。
# frequencyOffset 支持 -6..+6；相对偏移沿用旧 7 级阶梯（1m/5m/15m/1h/1d/1w/1M），
# 保证存量订阅语义不变：15m+1 仍为 1h，而不是 30m。
# 30m 作为绝对级别可显式使用；30m 的相对偏移特例：+1=1h、-1=15m。
FREQUENCY_OFFSET_MIN = -6
FREQUENCY_OFFSET_MAX = 6
FREQUENCY_LEVELS = ("1m", "5m", "15m", "30m", "1h", "1d", "1w", "1M")
LEGACY_RELATIVE_FREQUENCY_LEVELS = ("1m", "5m", "15m", "1h", "1d", "1w", "1M")


def normalize_frequency_offset(value: Any, field: str = "frequencyOffset") -> int:
    try:
        offset = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError("%s 必须是整数" % field)
    if offset < FREQUENCY_OFFSET_MIN or offset > FREQUENCY_OFFSET_MAX:
        raise ValueError(
            "%s 只支持 %d..%d（相对频率阶梯共 7 级：1m/5m/15m/1h/1d/1w/1M；"
            "30m 为绝对级别，+1=1h、-1=15m）"
            % (field, FREQUENCY_OFFSET_MIN, FREQUENCY_OFFSET_MAX)
        )
    return offset


def normalize_frequency_offsets(value: Any, field: str = "frequencyOffsets") -> List[int]:
    """一个 context 条件可以配置多个 offset；同一条件内任一 offset 命中即可。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("%s 必须是非空数组" % field)
    offsets: List[int] = []
    for item in value:
        offset = normalize_frequency_offset(item, field=field)
        if offset not in offsets:
            offsets.append(offset)
    return offsets


def normalize_target_frequencies(value: Any, field: str = "targetFrequencies") -> List[str]:
    """绝对目标级别，例如 ["1h"]：不随 primary 频率做偏移，直接锚定这些级别。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("%s 必须是非空数组" % field)
    targets: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text not in FREQUENCY_LEVELS:
            raise ValueError(
                "%s 只支持 %s，收到：%s" % (field, "/".join(FREQUENCY_LEVELS), text or item)
            )
        if text not in targets:
            targets.append(text)
    return targets


def frequency_offset_label(offset: Any) -> str:
    value = int(offset or 0)
    if value == 0:
        return "同周期"
    if value > 0:
        return "父级+%d" % value
    return "子级%d" % value


def trigger_filter_fields(factor_key: str) -> frozenset:
    """每个基础因子里表示“动作/边沿”的筛选字段。"""
    key = str(factor_key or "")
    if key.endswith("_ma_triple"):
        return frozenset({"relationCrosses"})
    if key.endswith("_macd"):
        return frozenset({"difCrosses", "histCrosses"})
    if key.endswith("_door"):
        return frozenset({"edges"})
    if key.endswith("_ma"):
        return frozenset({"priceCrosses", "pairCrosses", "relationCrosses"})
    if key.endswith("_boll"):
        return frozenset({"upperCrosses", "midCrosses", "lowerCrosses"})
    if key.endswith("_gate_rsi_first"):
        return frozenset({"events"})
    if key.endswith("_jue_rsi_first"):
        return frozenset({"events"})
    # 旧版事件线因子兼容；v4 基础因子没有这些字段。
    return frozenset({"eventTypes", "actions"})


def condition_has_trigger(condition: Dict[str, Any]) -> bool:
    if not isinstance(condition, dict):
        return False
    fields = trigger_filter_fields(str(condition.get("factorKey") or ""))
    filters = condition.get("filters")
    if not isinstance(filters, dict):
        return False
    return any(field in filters for field in fields)


def resolve_subscription_mode(explicit: Any, conditions: List[Dict[str, Any]]) -> str:
    """订阅展示/推送模式。explicit 优先，auto 按条件自动判定。"""
    if explicit in (MODE_TRIGGER, MODE_POOL):
        return str(explicit)
    conditions = [c for c in (conditions or []) if isinstance(c, dict)]
    if any(condition_has_trigger(c) for c in conditions):
        return MODE_TRIGGER
    return MODE_POOL


def strip_trigger_filters(condition: Dict[str, Any]) -> Dict[str, Any]:
    """去掉动作字段，只保留状态/范围字段，用于评估“基础池”。"""
    cloned = dict(condition or {})
    filters = cloned.get("filters")
    if not isinstance(filters, dict):
        return cloned
    fields = trigger_filter_fields(str(condition.get("factorKey") or ""))
    cloned["filters"] = {k: v for k, v in filters.items() if k not in fields}
    return cloned


def has_pool_layer(mode: str, conditions: List[Dict[str, Any]]) -> bool:
    """触发型单条件订阅没有基础池；其余模式都展示基础池。"""
    if mode == MODE_POOL:
        return True
    return len([c for c in (conditions or []) if isinstance(c, dict)]) > 1


def structure_summary(mode: str, conditions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按“事件条件 + 候选池”两段式结构检查订阅。

    - trigger 模式：必须有且仅有一个 event 条件，并且至少一个 pool 条件；
    - pool 模式：event 条件本身定义候选池，进入候选池即为事件，因此不强制额外 pool。
    """
    items = [c for c in (conditions or []) if isinstance(c, dict)]
    event_conditions = [
        c for c in items if c.get("layer") == LAYER_EVENT or c.get("role") == "primary"
    ]
    pool_conditions = [
        c for c in items if c.get("layer") == LAYER_POOL or c.get("role") == "context"
    ]
    missing: List[str] = []
    if len(event_conditions) != 1:
        missing.append("事件条件（必须且只能有一个）")
    if mode == MODE_TRIGGER and not pool_conditions:
        missing.append("候选池条件（至少一个背景条件）")
    return {
        "eventConditions": event_conditions,
        "poolConditions": pool_conditions,
        "structureComplete": not missing,
        "missingParts": missing,
    }
