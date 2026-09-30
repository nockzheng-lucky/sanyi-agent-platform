"""Agent 工具白名单。

页面 Agent 只能调用这里列出的工具；未来开放给用户自己的 Agent 时，
MCP/REST 复用同一套 factor schema，保证行为一致。

除因子查询外，Agent 还负责维护“当前筛选条件”：
用户说“只看 15 分钟”“只保留破诀”时，模型调用 sanyi_update_filters
持久化条件；后续 evaluate 会自动合并这些条件，当次显式参数优先。
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from ..factor_registry import registry
from ..push_channels import get_pushplus_channel
from ..signal_subscriptions import (
    create_signal_subscription,
    delete_signal_subscription,
    list_signal_subscriptions,
    user_id_from_record,
)
from .filter_store import (
    clear_filters,
    get_filter_state,
    merge_params,
    patch_filters,
)


def build_tools(for_record: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    descriptors = registry.descriptors(for_record=for_record)
    factor_keys = [d["factorKey"] for d in descriptors]
    subscribable_factor_keys = [d["factorKey"] for d in descriptors if d.get("eventBased")]
    filter_fields: Dict[str, Any] = {
        "frequencies": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "周期过滤，例如 [\"15m\"]；null 表示清除该限制。",
        },
        "symbols": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "品种或交易对过滤，例如 [\"AU0\"] / [\"BTCUSDT\"]；null 表示清除该限制。",
        },
        "combos": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": ["walk2_break20", "walkB_break80", "walkC_break20"]},
            "description": "走法×破诀组合过滤；walk2_break20=走2破20诀 / walkB_break80=走B破80诀 / walkC_break20=走C破20诀。",
        },
        "gateTypes": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": ["tian", "di"]},
            "description": "门类型过滤；tian=天门 / di=地门。",
        },
        "eventTypes": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
                "enum": ["open", "close", "formation", "formationAbove", "formationBelow"],
            },
            "description": (
                "币圈今日门信号事件类型。open=开门；close=关门；"
                "formation=形成门（门上+门下）；formationAbove=形成门·无动作门上；"
                "formationBelow=形成门·无动作门下。新订阅要精确区分四态时，"
                "请使用 open / close / formationAbove / formationBelow。"
            ),
        },
        "actions": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": ["open", "close"]},
            "description": "期货今日门信号动作类型；open=开门，close=关门。",
        },
        "liveStatuses": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": ["已开", "开+关", "无动作·门上", "无动作·门下", "已关", "删除"]},
            "description": "门生命周期过滤；多头地门常用 [\"已开\", \"无动作·门上\"]，空头天门常用 [\"已关\", \"无动作·门下\"]。",
        },
        "ma208Anchor": {
            "type": ["string", "null"],
            "enum": ["gatePrice", "currentPrice"],
            "description": "MA208 比较锚点。gatePrice=门价；currentPrice=现价。出条件默认用 gatePrice（门价）。",
        },
        "ma208Mode": {
            "type": ["string", "null"],
            "enum": ["near", "above", "nearOrAbove"],
            "description": (
                "MA208 位置过滤；null 表示不启用。near=在 MA208 附近（±ma208TolerancePct）；"
                "above=在 MA208 以上；nearOrAbove=附近或以上。"
            ),
        },
        "ma208TolerancePct": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 50,
            "description": "“附近”的偏离百分比，默认 1（±1%）。仅 near / nearOrAbove 使用。",
        },
        "includeDeleted": {
            "type": ["boolean", "null"],
            "description": "门条件因子专用；true 时包含已删除门。",
        },
        "states": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "状态过滤，例如 [\"80诀破诀\"]；null 表示清除该限制。",
        },
        "directions": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": ["long", "short", "none"]},
            "description": "方向过滤；long=多 / short=空 / none=无方向。",
        },
        "broken": {
            "type": ["boolean", "null"],
            "description": "是否只看破诀；null 表示不限。",
        },
        "walkCodes": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "走法代码过滤，例如 [\"2\"] 表示“走2”；null 表示清除。",
        },
        "walkMarks": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "特殊标记过滤，例如 [\"20破·走2\"]；null 表示清除。",
        },
        "maxAgeMinutes": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": "只看最近 N 分钟；null 表示清除该限制。",
        },
        "limit": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 100,
            "description": "最多返回条数；null 表示清除该限制。",
        },
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "sanyi_list_factors",
                "description": "列出三易引擎当前可用的因子及其参数、价格、风险提示。用户问“有什么因子/能查什么”时先调用。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_evaluate_factor",
                "description": (
                    "执行三易引擎因子计算。必须使用列表接口返回的 factorKey；"
                    "params 严格按因子 paramsSchema 填写。若用户已设置当前筛选条件，"
                    "这里未显式传的参数会自动套用存量筛选条件；显式传参优先。"
                    "结果里的 generatedAt 必须转述给用户。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "factorKey": {"type": "string", "enum": factor_keys},
                        "params": {"type": "object"},
                    },
                    "required": ["factorKey"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_update_filters",
                "description": (
                    "保存/调整某个因子的持久筛选条件。用户用自然语言提出筛选偏好时调用："
                    "例如“只看 15 分钟”就把对应 factorKey 的 frequencies 改成 [\"15m\"]；"
                    "“只保留破诀”就改 states 或 broken。只传需要变化的字段，未传字段保持不变；"
                    "replace=true 表示整体替换该因子的条件。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "factorKey": {"type": "string", "enum": factor_keys},
                        "filters": {
                            "type": "object",
                            "properties": filter_fields,
                            "additionalProperties": False,
                        },
                        "replace": {
                            "type": "boolean",
                            "description": "默认 false=只更新传入字段；true=先清空该因子旧条件再写入。",
                        },
                    },
                    "required": ["factorKey", "filters"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_get_filters",
                "description": "查看当前所有因子的持久筛选条件。用户问“现在有什么筛选条件”时调用。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_clear_filters",
                "description": "清除筛选条件。factorKey 省略时清除全部；指定时只清除该因子。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "factorKey": {"type": ["string", "null"], "enum": factor_keys + [None]},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_create_subscription",
                "description": (
                    "创建持续信号订阅。只能订阅事件线因子（eventBased=true），"
                    "快照因子只用于查询，不能订阅。用户要求“订阅/持续监控/有信号提醒我”时，"
                    "先复述筛选条件并向用户确认；用户明确确认后再调用本工具。"
                    "单因子订阅用 factorKey+filters；用户要求“叠加/同时满足/交叉匹配/结合门条件”时，"
                    "必须用 conditions 创建一个订阅（第一条是 primary，其余是 context，全部 AND），"
                    "不要创建多个订阅让用户自行交叉。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "factorKey": {
                            "type": ["string", "null"],
                            "enum": subscribable_factor_keys,
                            "description": "单因子订阅的事件线 factorKey；使用 conditions 时省略。",
                        },
                        "filters": {
                            "type": "object",
                            "properties": filter_fields,
                            "additionalProperties": False,
                        },
                        "name": {"type": "string", "description": "订阅名称，例如“走2破20诀 + 下方有效地门”。"},
                        "conditions": {
                            "type": "array",
                            "description": "组合订阅条件（AND）。单因子订阅不要传。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "factorKey": {"type": "string", "enum": subscribable_factor_keys},
                                    "filters": {
                                        "type": "object",
                                        "properties": filter_fields,
                                        "additionalProperties": False,
                                    },
                                    "role": {
                                        "type": "string",
                                        "enum": ["primary", "context"],
                                        "description": "第一条默认 primary，后续默认 context。",
                                    },
                                    "joinWith": {"type": "string", "enum": ["primary"], "description": "默认 primary。"},
                                    "joinSymbol": {"type": "string", "enum": ["symbol"], "description": "默认 symbol==symbol。"},
                                    "frequencyOffset": {
                                        "type": "integer",
                                        "enum": [0, 1],
                                        "description": "0=同周期；1=父级周期（15m→1h、1h→1d）。",
                                    },
                                    "sideRule": {
                                        "type": "string",
                                        "enum": ["below", "above"],
                                        "description": "below=门价在 primary 当前价下方；above=门价在上方。",
                                    },
                                    "join": {
                                        "type": "object",
                                        "properties": {
                                            "joinWith": {"type": "string", "enum": ["primary"]},
                                            "joinSymbol": {"type": "string", "enum": ["symbol"]},
                                            "frequencyOffset": {"type": "integer", "enum": [0, 1]},
                                            "sideRule": {"type": "string", "enum": ["below", "above"]},
                                        },
                                        "description": "嵌套写法：join.frequencyOffset / join.sideRule 与顶层字段等价。",
                                    },
                                },
                                "required": ["factorKey"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_get_pushplus",
                "description": (
                    "查询当前账号的 Pushplus 绑定状态。用户说“推送到 pushplus / 推送给我”时先调用本工具；"
                    "已绑定时订阅新匹配会自动推送；未绑定时请用户到「通知」页绑定，不要在聊天里索要 token。"
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_list_subscriptions",
                "description": "查看用户当前所有持续信号订阅。每次回答订阅列表问题都必须重新调用，以最新结果为准，历史消息里的列表可能已过期。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sanyi_delete_subscription",
                "description": "删除一个持续信号订阅。subscriptionId 来自 sanyi_list_subscriptions。",
                "parameters": {
                    "type": "object",
                    "properties": {"subscriptionId": {"type": "integer"}},
                    "required": ["subscriptionId"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _ensure_subscribable_factor(factor_key: str) -> Optional[str]:
    spec = registry.get(factor_key)
    if spec is None or not spec.event_based:
        return "因子 %s 不是事件线因子，不能用于持续订阅" % factor_key
    return None


async def execute_tool(name: str, arguments: Dict[str, Any], token_record: dict) -> Dict[str, Any]:
    """执行工具并返回给模型的结果。异常也要作为工具结果返回，而不是中断会话。"""
    try:
        if name == "sanyi_list_factors":
            return {
                "factors": registry.descriptors(for_record=token_record),
                "currentFilters": get_filter_state(token_record),
            }

        if name == "sanyi_get_filters":
            return {"filters": get_filter_state(token_record)}

        if name == "sanyi_get_pushplus":
            try:
                user_id = user_id_from_record(token_record)
                channel = get_pushplus_channel(user_id)
            except ValueError as exc:
                return {"error": str(exc)}
            if channel:
                return {
                    "pushplus": channel,
                    "bound": True,
                    "message": "Pushplus 已绑定（%s）。订阅创建后，新匹配信号会自动推送到该通道。" % channel["tokenMasked"],
                }
            return {
                "pushplus": None,
                "bound": False,
                "message": "Pushplus 尚未绑定。请用户到「通知」页绑定自己的 Pushplus token；绑定后订阅会自动推送。",
            }

        if name == "sanyi_clear_filters":
            factor_key = arguments.get("factorKey")
            state = clear_filters(token_record, str(factor_key) if factor_key else None)
            return {"filters": state, "message": "筛选条件已清除"}

        if name == "sanyi_create_subscription":
            conditions = arguments.get("conditions")
            name = str(arguments.get("name") or "")
            if isinstance(conditions, list) and conditions:
                for condition in conditions:
                    if isinstance(condition, dict) and condition.get("factorKey"):
                        error = _ensure_subscribable_factor(str(condition["factorKey"]))
                        if error:
                            return {"error": error}
                created = create_signal_subscription(
                    token_record,
                    conditions=conditions,
                    name=name,
                )
            else:
                factor_key = str(arguments.get("factorKey") or "").strip()
                if not factor_key:
                    return {"error": "缺少 factorKey 或 conditions"}
                error = _ensure_subscribable_factor(factor_key)
                if error:
                    return {"error": error}
                filters = arguments.get("filters")
                if not isinstance(filters, dict) or not filters:
                    filters = get_filter_state(token_record).get(factor_key) or {}
                created = create_signal_subscription(
                    token_record,
                    factor_key=factor_key,
                    filters=filters,
                    name=name,
                )
            return {
                "subscription": created,
                "message": "订阅已创建，匹配信号会出现在聊天页左侧的订阅信号列表",
            }

        if name == "sanyi_list_subscriptions":
            try:
                rows = list_signal_subscriptions(user_id_from_record(token_record))
            except ValueError as exc:
                return {"error": str(exc)}
            return {"subscriptions": rows}

        if name == "sanyi_delete_subscription":
            try:
                subscription_id = int(arguments.get("subscriptionId") or 0)
                deleted = delete_signal_subscription(
                    user_id_from_record(token_record),
                    subscription_id,
                )
            except (TypeError, ValueError) as exc:
                return {"error": "subscriptionId 不正确：%s" % exc}
            if not deleted:
                return {"error": "订阅不存在"}
            return {"message": "订阅已删除", "subscriptionId": subscription_id}

        if name == "sanyi_update_filters":
            factor_key = str(arguments.get("factorKey") or "").strip()
            if not factor_key:
                return {"error": "缺少 factorKey"}
            patch = arguments.get("filters") or {}
            if not isinstance(patch, dict):
                return {"error": "filters 必须是对象"}
            state = patch_filters(
                token_record,
                factor_key=factor_key,
                patch=patch,
                replace=bool(arguments.get("replace")),
            )
            return {
                "filters": state,
                "message": "筛选条件已更新：%s" % factor_key,
            }

        if name == "sanyi_evaluate_factor":
            factor_key = str(arguments.get("factorKey") or "").strip()
            if not factor_key:
                return {"error": "缺少 factorKey"}
            explicit = arguments.get("params") or {}
            if not isinstance(explicit, dict):
                return {"error": "params 必须是对象"}
            stored = get_filter_state(token_record).get(factor_key) or {}
            params = merge_params(stored, explicit)
            result = await registry.evaluate(
                token_record=token_record,
                factor_key=factor_key,
                params=params,
            )
            result["appliedFilters"] = params
            return result

        return {"error": "未知工具：%s" % name}
    except HTTPException as exc:
        return {"error": json.dumps(exc.detail, ensure_ascii=False)}
    except Exception as exc:  # noqa: BLE001 - Agent 上下文需要结构化错误
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
