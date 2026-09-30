"""持续信号订阅存储。

Agent 在用户确认后调用 sanyi_create_subscription 创建订阅；
聊天页左侧面板通过 /api/v1/signal-subscriptions/matches 轮询当前命中结果。

订阅从“单因子”升级为“组合条件层”：

    subscription_conditions 保存 1 个 primary + 0..N 个 context 条件；
    单条件订阅照旧使用 signal_subscriptions.factor_key/filters_json 的旧路径，
    并且创建时也会补一条 primary 子表记录，所以读取路径始终统一。
"""
import json
from typing import Any, Dict, List, Optional

from .accounts import is_shadow_mode
from .db import _now_iso, get_conn
from .factor_registry import registry
from .strategy_model import (
    LAYER_EVENT,
    LAYER_POOL,
    MODE_AUTO,
    MODE_TRIGGER,
    VALID_LAYERS,
    VALID_MODES,
    normalize_frequency_offset,
    normalize_frequency_offsets,
    normalize_target_frequencies,
    resolve_subscription_mode,
    structure_summary,
)

MAX_CONDITIONS = 8
_ROLES = ("primary", "context")
_JOIN_WITH = ("primary",)
_JOIN_SYMBOL = ("symbol",)
_SIDE_RULES = ("below", "above")


def user_id_from_record(token_record: Dict[str, Any]) -> int:
    user_id = token_record.get("_user_id")
    if not user_id:
        raise ValueError("该功能需要手机号登录账号")
    return int(user_id)


def _ensure_visible_factor(token_record: Dict[str, Any], factor_key: str) -> Any:
    spec = registry.get(factor_key)
    if spec is None or spec.status != "active":
        raise ValueError("因子不存在：%s" % factor_key)
    if spec.shadow_only and not is_shadow_mode(token_record):
        # 对普通用户隐藏影子因子的存在。
        raise ValueError("因子不存在：%s" % factor_key)
    return spec


def _parse_json(text: Any, fallback: Any) -> Any:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return fallback
    return value


def _pick(raw: Dict[str, Any], join: Dict[str, Any], *keys: str) -> Any:
    """按 camelCase / snake_case 顺序从 condition 顶层或嵌套 join 中取值。"""
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    for key in keys:
        if key in join and join[key] is not None:
            return join[key]
    return None


def _normalize_side_rule(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value or "").strip()
    if not text:
        return None
    if text not in _SIDE_RULES:
        raise ValueError("sideRule 只支持 below / above")
    return text


def _normalize_condition(
    token_record: Dict[str, Any],
    raw: Dict[str, Any],
    index: int,
    count: int,
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("条件 %d 必须是对象" % (index + 1))

    factor_key = str(raw.get("factorKey") or raw.get("factor_key") or "").strip()
    if not factor_key:
        raise ValueError("条件 %d 缺少 factorKey" % (index + 1))
    spec = _ensure_visible_factor(token_record, factor_key)

    filters = raw.get("filters") or raw.get("filtersJson") or {}
    if not isinstance(filters, dict):
        raise ValueError("条件 %d（%s）的 filters 必须是对象" % (index + 1, factor_key))
    filters = registry.validate_params(spec, filters)
    if not filters:
        raise ValueError("条件 %d（%s）至少需要一个筛选字段" % (index + 1, factor_key))

    if count == 1:
        role = "primary"
    else:
        role = str(raw.get("role") or ("primary" if index == 0 else "context")).strip()
    if role not in _ROLES:
        raise ValueError("条件 %d 的 role 只支持 primary / context" % (index + 1))
    if index == 0 and role != "primary":
        raise ValueError("组合订阅的第一条条件必须是 primary")
    if index > 0 and role == "primary":
        raise ValueError("组合订阅只能有一条 primary 条件")

    layer = str(raw.get("layer") or raw.get("conditionLayer") or "").strip()
    if not layer:
        layer = LAYER_EVENT if role == "primary" else LAYER_POOL
    if layer not in VALID_LAYERS:
        raise ValueError("条件 %d 的 layer 只支持 event / pool" % (index + 1))
    if role == "primary" and layer != LAYER_EVENT:
        raise ValueError("事件条件必须是 primary")
    if role == "context" and layer != LAYER_POOL:
        raise ValueError("候选池条件必须是 context")

    join = raw.get("join") if isinstance(raw.get("join"), dict) else raw.get("joinOn")
    if not isinstance(join, dict):
        join = {}
    if role == "primary":
        join_with = None
        join_symbol = "symbol"
        frequency_offset = 0
        frequency_offsets = [0]
        target_frequencies: List[str] = []
        side_rule = None
    else:
        join_with = str(_pick(raw, join, "joinWith", "join_with") or "primary")
        join_symbol = str(_pick(raw, join, "joinSymbol", "join_symbol", "symbol") or "symbol")
        if join_with not in _JOIN_WITH:
            raise ValueError("条件 %d 的 joinWith 只支持 primary" % (index + 1))
        if join_symbol not in _JOIN_SYMBOL:
            raise ValueError("条件 %d 的 joinSymbol 只支持 symbol" % (index + 1))
        target_frequencies = normalize_target_frequencies(
            _pick(raw, join, "targetFrequencies", "target_frequencies"),
            field="条件 %d 的 targetFrequencies" % (index + 1),
        )
        legacy_raw = _pick(raw, join, "frequencyOffset", "frequency_offset")
        explicit_offsets = _pick(raw, join, "frequencyOffsets", "frequency_offsets")
        if explicit_offsets is None:
            if legacy_raw is not None:
                frequency_offsets = [
                    normalize_frequency_offset(
                        legacy_raw,
                        field="条件 %d 的 frequencyOffset" % (index + 1),
                    )
                ]
            elif target_frequencies:
                # 绝对锚定模式：没有显式 offset 时只锚定目标级别，不同时命中 primary 自身。
                frequency_offsets = []
            else:
                frequency_offsets = [0]
        else:
            frequency_offsets = normalize_frequency_offsets(
                explicit_offsets,
                field="条件 %d 的 frequencyOffsets" % (index + 1),
            )
            if legacy_raw is not None:
                legacy_offset = normalize_frequency_offset(
                    legacy_raw,
                    field="条件 %d 的 frequencyOffset" % (index + 1),
                )
                if legacy_offset not in frequency_offsets:
                    frequency_offsets.append(legacy_offset)
        side_rule = _normalize_side_rule(
            _pick(raw, join, "sideRule", "side_rule")
        )
        frequency_offset = frequency_offsets[0] if frequency_offsets else 0

    return {
        "factorKey": factor_key,
        "filters": filters,
        "role": role,
        "layer": layer,
        "joinWith": join_with,
        "joinSymbol": join_symbol,
        "frequencyOffset": frequency_offset,
        "frequencyOffsets": frequency_offsets,
        "targetFrequencies": target_frequencies,
        "sideRule": side_rule,
        "sortOrder": index,
    }


def _normalize_conditions(
    token_record: Dict[str, Any],
    factor_key: Optional[str],
    filters: Optional[Dict[str, Any]],
    conditions: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if conditions is not None:
        if factor_key or filters:
            raise ValueError("conditions 与 factorKey/filters 不能同时使用")
        if not isinstance(conditions, list) or not conditions:
            raise ValueError("conditions 至少需要一个条件")
        if len(conditions) > MAX_CONDITIONS:
            raise ValueError("一个订阅最多 %d 个条件" % MAX_CONDITIONS)
        normalized = [
            _normalize_condition(token_record, raw, index, len(conditions))
            for index, raw in enumerate(conditions)
        ]
        return normalized

    factor_key = str(factor_key or "").strip()
    if not factor_key:
        raise ValueError("缺少 factorKey 或 conditions")
    spec = _ensure_visible_factor(token_record, factor_key)
    filters = registry.validate_params(spec, filters or {})
    if not filters:
        raise ValueError("订阅至少需要一个筛选条件")
    return [
        {
            "factorKey": factor_key,
            "filters": filters,
            "role": "primary",
            "layer": LAYER_EVENT,
            "joinWith": None,
            "joinSymbol": "symbol",
            "frequencyOffset": 0,
            "frequencyOffsets": [0],
            "targetFrequencies": [],
            "sideRule": None,
            "sortOrder": 0,
        }
    ]


def _ensure_trigger_pool(conditions: List[Dict[str, Any]]) -> tuple[bool, List[Dict[str, Any]]]:
    """触发型单条件订阅自动补一个全市场候选池。

    用户只说“15 分钟地门开门”这类动作条件时，平台不再要求用户补齐背景池；
    默认池使用同域的 price 因子（只带 primary 的 frequencies），
    语义 = 与动作同一品种、同一周期，因此不会改变触发命中集合。
    """
    if len(conditions) != 1:
        return False, conditions
    resolved = resolve_subscription_mode(MODE_AUTO, conditions)
    if resolved != MODE_TRIGGER:
        return False, conditions
    primary = conditions[0]
    factor_key = str(primary.get("factorKey") or "")
    domain = factor_key.split("_", 1)[0] if "_" in factor_key else ""
    price_key = "%s_price" % domain if domain else ""
    price_spec = registry.get(price_key)
    if price_spec is None or price_spec.status != "active":
        return False, conditions
    pool_filters: Dict[str, Any] = {}
    frequencies = (primary.get("filters") or {}).get("frequencies")
    if isinstance(frequencies, list) and frequencies:
        pool_filters["frequencies"] = list(frequencies)
    pool_condition = {
        "factorKey": price_key,
        "filters": pool_filters,
        "role": "context",
        "layer": LAYER_POOL,
        "joinWith": "primary",
        "joinSymbol": "symbol",
        "frequencyOffset": 0,
        "frequencyOffsets": [0],
        "targetFrequencies": [],
        "sideRule": None,
        "sortOrder": len(conditions),
        "autoPool": True,
    }
    return True, list(conditions) + [pool_condition]


def create_signal_subscription(
    token_record: Dict[str, Any],
    factor_key: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    name: str = "",
    conditions: Optional[List[Dict[str, Any]]] = None,
    mode: str = "auto",
) -> Dict[str, Any]:
    user_id = user_id_from_record(token_record)
    normalized = _normalize_conditions(token_record, factor_key, filters, conditions)
    primary = normalized[0]
    primary_spec = registry.get(primary["factorKey"])
    if mode not in VALID_MODES:
        raise ValueError("mode 只支持 auto / trigger / pool")
    auto_pool_added, normalized = _ensure_trigger_pool(normalized)
    resolved_mode = resolve_subscription_mode(mode, normalized)
    structure = structure_summary(resolved_mode, normalized)
    if resolved_mode == MODE_TRIGGER and not structure["structureComplete"]:
        raise ValueError(
            "订阅结构不完整，缺少：%s。触发型订阅必须包含 1 个事件条件 + 至少 1 个候选池条件。"
            % "；".join(structure["missingParts"])
        )

    if not name:
        if len(normalized) == 1 or auto_pool_added:
            name = primary_spec.name if primary_spec else primary["factorKey"]
        else:
            name = "%s等%d项组合订阅" % (primary_spec.name if primary_spec else primary["factorKey"], len(normalized))

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO signal_subscriptions(user_id, factor_key, filters_json, name,"
        " status, created_at, updated_at, mode) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
        (
            user_id,
            primary["factorKey"],
            json.dumps(primary["filters"], ensure_ascii=False),
            str(name)[:80],
            _now_iso(),
            _now_iso(),
            mode,
        ),
    )
    subscription_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO subscription_conditions("
        "subscription_id, factor_key, filters_json, role, condition_layer,"
        "join_with, join_symbol, frequency_offset, side_rule, sort_order,"
        "frequency_offsets_json, target_frequencies_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                subscription_id,
                condition["factorKey"],
                json.dumps(condition["filters"], ensure_ascii=False),
                condition["role"],
                condition["layer"],
                condition["joinWith"],
                condition["joinSymbol"],
                condition["frequencyOffset"],
                condition["sideRule"],
                condition["sortOrder"],
                json.dumps(condition["frequencyOffsets"], ensure_ascii=False),
                json.dumps(condition["targetFrequencies"], ensure_ascii=False),
            )
            for condition in normalized
        ],
    )
    conn.commit()
    return get_signal_subscription(user_id, subscription_id)


def _load_conditions(subscription_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM subscription_conditions WHERE subscription_id = ?"
        " ORDER BY sort_order ASC, id ASC",
        (subscription_id,),
    ).fetchall()
    conditions = []
    for row in rows:
        rec = dict(row)
        frequency_offsets = _parse_json(rec.get("frequency_offsets_json"), None)
        if not isinstance(frequency_offsets, list):
            # 旧库没有该列数据：从单值 frequency_offset 兼容。
            frequency_offsets = [int(rec["frequency_offset"] or 0)]
        conditions.append(
            {
                "id": int(rec["id"]),
                "subscriptionId": int(rec["subscription_id"]),
                "factorKey": rec["factor_key"],
                "filters": _parse_json(rec.get("filters_json"), {}),
                "role": rec["role"],
                "layer": rec.get("condition_layer") or (
                    LAYER_EVENT if rec["role"] == "primary" else LAYER_POOL
                ),
                "joinWith": rec.get("join_with"),
                "joinSymbol": rec.get("join_symbol"),
                "frequencyOffset": int(rec["frequency_offset"] or 0),
                "frequencyOffsets": frequency_offsets,
                "targetFrequencies": _parse_json(rec.get("target_frequencies_json"), []),
                "sideRule": rec.get("side_rule"),
                "sortOrder": int(rec["sort_order"] or 0),
            }
        )
    return conditions


def _to_dict(row: Any) -> Dict[str, Any]:
    rec = dict(row)
    conditions = _load_conditions(int(rec["id"]))
    if not conditions:
        # 极端情况（如旧库未执行迁移）：用父表字段合成一条 primary 条件。
        conditions = [
            {
                "id": None,
                "subscriptionId": int(rec["id"]),
                "factorKey": rec["factor_key"],
                "filters": _parse_json(rec.get("filters_json"), {}),
                "role": "primary",
                "layer": LAYER_EVENT,
                "joinWith": None,
                "joinSymbol": "symbol",
                "frequencyOffset": 0,
                "frequencyOffsets": [0],
                "targetFrequencies": [],
                "sideRule": None,
                "sortOrder": 0,
            }
        ]
    primary = conditions[0]
    mode = resolve_subscription_mode(rec.get("mode") or "auto", conditions)
    structure = structure_summary(mode, conditions)
    return {
        "id": rec["id"],
        "userId": rec["user_id"],
        "factorKey": rec["factor_key"],
        "filters": primary["filters"],
        "conditions": conditions,
        "name": rec.get("name"),
        "status": rec.get("status"),
        "createdAt": rec.get("created_at"),
        "updatedAt": rec.get("updated_at"),
        "baselineAt": rec.get("baseline_at"),
        "mode": rec.get("mode") or "auto",
        "resolvedMode": mode,
        "eventConditions": structure["eventConditions"],
        "poolConditions": structure["poolConditions"],
        "structureComplete": structure["structureComplete"],
        "missingParts": structure["missingParts"],
    }


def get_signal_subscription(user_id: int, subscription_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM signal_subscriptions WHERE id = ? AND user_id = ?",
        (subscription_id, user_id),
    ).fetchone()
    return _to_dict(row) if row else None


def list_signal_subscriptions(user_id: int, include_inactive: bool = False) -> List[Dict[str, Any]]:
    conn = get_conn()
    sql = "SELECT * FROM signal_subscriptions WHERE user_id = ?"
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, (user_id,)).fetchall()
    return [_to_dict(r) for r in rows]


def list_all_active_signal_subscriptions() -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM signal_subscriptions WHERE status = 'active' ORDER BY id ASC"
    ).fetchall()
    return [_to_dict(r) for r in rows]


def is_signal_subscription_active(subscription_id: int, user_id: int) -> bool:
    """推送前实时复核订阅状态，防止评估期间用户刚取消的订阅仍然推送。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT status FROM signal_subscriptions WHERE id = ? AND user_id = ?",
        (subscription_id, user_id),
    ).fetchone()
    return row is not None and row["status"] == "active"


def set_subscription_baseline(subscription_id: int, now: str) -> bool:
    """标记订阅已完成首轮 baseline，之后只推新出现的信号。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE signal_subscriptions SET baseline_at = ? WHERE id = ?",
        (now, subscription_id),
    )
    conn.commit()
    return cur.rowcount == 1


def delete_signal_subscription(user_id: int, subscription_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE signal_subscriptions SET status = 'deleted', updated_at = ?"
        " WHERE id = ? AND user_id = ? AND status = 'active'",
        (_now_iso(), subscription_id, user_id),
    )
    conn.commit()
    return cur.rowcount == 1
