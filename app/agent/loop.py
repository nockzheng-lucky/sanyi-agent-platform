"""页面 Agent 的 function-calling 循环，以 SSE 事件流输出。

事件：
- meta        会话开始
- delta       LLM 文本片段
- tool_call   模型决定调用工具
- tool_result 工具返回结果（页面可展示为“正在查询三易引擎”）
- done        完成与用量
- error       失败
"""
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from ..chat_history import (
    append_chat_messages,
    finalize_pending_assistant,
    update_pending_assistant,
)
from ..config import CHAT_MAX_MESSAGES, CHAT_MAX_TOOL_ROUNDS, LLM_MOCK, LLM_MODEL, SYSTEM_NAME
from ..db import log_usage
from ..factor_registry import registry
from .filter_store import get_filter_state
from .llm_client import chat_once
from .tools import build_tools, execute_tool

_SYSTEM_PROMPT = """你是「三易引擎」的行情研究助手，服务对象是普通交易者，不是程序员。

说话规则（非常重要）：
1. 始终用简洁的中文短句回答，像面对面聊天一样。
2. 严禁对用户输出 factorKey、API、JSON、schema、params、参数名、工具名、
   eventBased 等任何代码、英文术语或技术名词。用户听不懂这些。
3. 需要说明查询条件时，用自然语言，例如“15 分钟级别、地门、刚刚开门”，
   不要写成 factorKey=futures_door 之类的东西。
4. 所有时间都换算成北京时间，显示成“9 月 6 日 14:15”这种格式，并说明这是信号生成时间。
5. 因子计算结果的 summary 和风险提示必须转述成人话；必须提示
   “仅供研究观察，不构成投资建议”。
6. 工具返回的是三易引擎的真实计算结果，禁止凭记忆编造；查询失败就直说没查到。
7. 工具结果里如果夹带英文字段，自己翻译成人话，不要照抄。

工作规则：
1. 用户问行情、门、走势、MACD、均线、RSI、诀等信号时，必须调用工具查真实数据。
2. 当前可用的都是“基础因子”，分为九类：价格 K 线、均线 MA、MACD、RSI3 进攻、
   走势段、诀、门、门价与均线空间关系、跨级别状态。
3. 用户说“1 小时走 2”，就是“走势段”条件：1 小时、走 2；
   用户说“30 分钟走 2 / 30 分钟均线多头 / 30 分钟 RSI3 进攻”等，就是对应因子的
   30 分钟级别条件；用户说“15 分钟开地门”，就是“门”条件：15 分钟、地门、开门边沿；
   用户说“收盘价在 MA208 上方”，就是“均线”条件；
   用户说“MA169 在 MA208 上方/下方”或“169>208 / 169<208”，也是“均线”条件：
   两组均线（25/144/169 与 52/208/832）之间可以任意比较，用 relations 选
   ma169_ma208 这类键，再用 relationStates 表达上方/下方；
   用户说“RSI3 上 80 / 下 20”，就是“RSI3 进攻”条件；
   用户说“MACD 上穿零轴”，就是“MACD”条件；其他说法按九类自然匹配。
4. 用户把几个条件放在一起说时，按“同时满足”处理，所有条件之间是 AND。
5. 用户说“订阅 / 持续监控 / 有信号提醒我”时，先用自然语言复述条件并向用户确认，
   用户确认后再创建订阅。创建后告诉用户：匹配信号会出现在聊天页左侧的“订阅信号”列表。
6. 创建组合订阅时，事件条件放第一条（例如“15 分钟地门开”），状态条件放后面
   （例如“1 小时走 2”）；平台会自动按同一个品种、相邻级别去匹配。
7. 用户要求“推送到 pushplus / 推送给我”时，先查绑定状态：已绑定就告诉他新信号会
   自动推送；未绑定就引导他去「通知」页绑定，不要在聊天里索要 token。
8. 用户说“清除筛选 / 取消所有过滤”时，帮他把已保存的筛选清掉。
9. 每次回答订阅列表问题前，必须重新查询最新列表，不能引用聊天历史里的旧列表。
10. 只有“事件型条件”才能做持续订阅；纯快照条件只用于当前查询。拿不准时先查因子列表，
    再判断，不要直接告诉用户不能订阅。
"""

def _sse(event: str, data: Any) -> str:
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False, default=str))


def _delta_persister(owner: str):
    """生成一个 on_text 回调：把流式回答按 0.5s 节流增量写入 chat_history。"""
    parts: List[str] = []
    last_write = [0.0]

    async def on_text(text: str) -> None:
        parts.append(str(text or ""))
        now = time.monotonic()
        if now - last_write[0] >= 0.5:
            update_pending_assistant(owner, "".join(parts))
            last_write[0] = now

    async def flush() -> None:
        if parts:
            update_pending_assistant(owner, "".join(parts))

    return on_text, flush


def _factor_context(
    factor_keys: List[str],
    for_record: Optional[Dict[str, Any]] = None,
) -> str:
    """把用户在因子列表页加载的因子变成系统提示，只显示中文名，不显示技术 key。"""
    if not factor_keys:
        return ""
    descriptors = {d["factorKey"]: d for d in registry.descriptors(for_record=for_record)}
    loaded = []
    for key in factor_keys:
        key = str(key or "").strip()
        desc = descriptors.get(key)
        if key and desc and desc.get("status") == "active" and desc not in loaded:
            loaded.append(desc)
    if not loaded:
        return ""
    names = "、".join(str(desc.get("name") or "") for desc in loaded)
    return (
        "\n\n用户已在“因子列表”中选择了这些关注项：%s。"
        "回答相关问题时优先使用这些能力；不相关时忽略这个提示。" % names
    )


_MA_RELATION_LABELS = {
    "price_ma25": "收盘价-MA25", "price_ma144": "收盘价-MA144", "price_ma169": "收盘价-MA169",
    "ma25_ma144": "MA25-MA144", "ma25_ma169": "MA25-MA169", "ma144_ma169": "MA144-MA169",
    "ma25_ma52": "MA25-MA52", "ma25_ma208": "MA25-MA208", "ma25_ma832": "MA25-MA832",
    "ma144_ma52": "MA144-MA52", "ma144_ma208": "MA144-MA208", "ma144_ma832": "MA144-MA832",
    "ma169_ma52": "MA169-MA52", "ma169_ma208": "MA169-MA208", "ma169_ma832": "MA169-MA832",
}


def _ma_relation_filter_label(value: Any) -> str:
    labels = []
    for item in value or []:
        key = str(item)
        if key not in _MA_RELATION_LABELS:
            labels.append(key)
            continue
        left, right = _MA_RELATION_LABELS[key].split("-", 1)
        labels.append("%s相对%s" % (left, right))
    return "均线关系只看 %s" % "、".join(labels) if labels else ""


_FILTER_LABELS = {
    "frequencies": lambda v: "只看 %s 级别" % "、".join(str(x) for x in v),
    "symbols": lambda v: "只看 %s" % "、".join(str(x) for x in v),
    "walkCodes": lambda v: "走势只保留 %s" % "、".join("走" + str(x) for x in v),
    "gateTypes": lambda v: "只保留 %s" % "、".join({"di": "地门", "tian": "天门"}.get(str(x), str(x)) for x in v),
    "edges": lambda v: "只看 %s" % "、".join({"open": "开门", "close": "关门"}.get(str(x), str(x)) for x in v),
    "liveStatuses": lambda v: "门状态只保留 %s" % "、".join(str(x) for x in v),
    "attacks": lambda v: "RSI3 只看 %s" % "、".join({"long": "超过 80 的进攻", "short": "跌破 20 的进攻"}.get(str(x), str(x)) for x in v),
    "directions": lambda v: "方向只看 %s" % "、".join({"long": "多", "short": "空", "none": "无"}.get(str(x), str(x)) for x in v),
    "broken": lambda v: "只看已经破诀的" if v else "只看还没破诀的",
    "maNames": lambda v: "均线只看 %s" % "、".join(str(x).upper() for x in v),
    "priceSides": lambda v: "价格与均线关系只看 %s" % "、".join({"above": "均线上方", "below": "均线下方", "equal": "正好相交"}.get(str(x), str(x)) for x in v),
    "relations": _ma_relation_filter_label,
    "relationStates": lambda v: "关系状态只看 %s" % "、".join({"above": "左侧在上方", "below": "左侧在下方", "near": "贴线"}.get(str(x), str(x)) for x in v),
    "relationCrosses": lambda v: "关系穿越只看 %s" % "、".join({"cross_up": "上穿/金叉", "cross_down": "下穿/死叉", "none": "无穿越"}.get(str(x), str(x)) for x in v),
    "requireAllRelations": lambda v: "所有选中关系都要同时满足" if v else "",
    "tolerancePct": lambda v: "关系贴线容差 ±%s%%" % v,
    "ma52AboveMa208": lambda v: "只看 MA52>MA208" if v else "只看 MA52<MA208",
    "ma52AboveMa832": lambda v: "只看 MA52>MA832" if v else "只看 MA52<MA832",
    "ma208AboveMa832": lambda v: "只看 MA208>MA832" if v else "只看 MA208<MA832",
    "allowMissingMaRelation": lambda v: "关系数据不足时也放行并标未确认" if v else "",
    "roles": lambda v: "跨级别只看 %s" % "、".join({"parent": "父级", "child": "子级"}.get(str(x), str(x)) for x in v),
    "positions": lambda v: "门价位置只看 %s" % "、".join({"above": "均线上方", "below": "均线下方"}.get(str(x), str(x)) for x in v),
    "formations": lambda v: "门形态只看 %s" % "、".join(str(x) for x in v),
    "limit": lambda v: "最多看 %s 条" % str(v),
}


def _friendly_filters(filters: Dict[str, Any]) -> str:
    if not isinstance(filters, dict) or not filters:
        return ""
    parts = []
    for key, value in filters.items():
        if value is None or value == [] or value == "":
            continue
        formatter = _FILTER_LABELS.get(key)
        if formatter:
            parts.append(formatter(value))
    return "；".join(parts)


def _filters_context(filter_state: Optional[Dict[str, Any]]) -> str:
    if not filter_state:
        return ""
    lines = []
    for factor_key, filters in sorted(filter_state.items()):
        if not isinstance(filters, dict) or not filters:
            continue
        desc = registry.get(factor_key)
        name = desc.name if desc is not None else factor_key
        friendly = _friendly_filters(filters)
        if friendly:
            lines.append("%s：%s" % (name, friendly))
    if not lines:
        return ""
    return (
        "\n\n当前用户已保存的筛选习惯：\n- " + "\n- ".join(lines) +
        "\n执行查询时默认套用这些习惯；用户当次明确说不同的条件时，以当次说的为准。"
    )


def _history_from(
    messages: List[Dict[str, Any]],
    factor_keys: Optional[List[str]] = None,
    filter_state: Optional[Dict[str, Any]] = None,
    token_record: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    system_prompt = (
        _SYSTEM_PROMPT
        + _factor_context(list(factor_keys or []), for_record=token_record)
        + _filters_context(filter_state)
    )
    history: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in messages[-CHAT_MAX_MESSAGES:]:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            history.append({"role": role, "content": content})
    if not any(m.get("role") == "user" for m in history):
        raise ValueError("没有可处理的用户消息")
    return history


async def run_agent_stream(
    messages: List[Dict[str, Any]],
    token_record: dict,
    request_id: str = "",
    factor_keys: Optional[List[str]] = None,
    persist_owner_key: Optional[str] = None,
    persist_messages: Optional[List[Dict[str, Any]]] = None,
) -> AsyncIterator[str]:
    yield _sse("meta", {"system": SYSTEM_NAME, "mode": "mock" if LLM_MOCK else "llm"})

    if persist_owner_key:
        # 上一轮被页面切走打断的回答，先固化为普通历史；再追加本轮用户消息。
        finalize_pending_assistant(persist_owner_key)
        if persist_messages:
            append_chat_messages(persist_owner_key, persist_messages)

    try:
        filter_state = get_filter_state(token_record)
        history = _history_from(
            messages,
            factor_keys=factor_keys,
            filter_state=filter_state,
            token_record=token_record,
        )
    except ValueError as exc:
        yield _sse("error", {"message": str(exc)})
        return

    total_usage = {"input": 0, "output": 0}
    last_assistant_text = ""
    try:
        for round_no in range(CHAT_MAX_TOOL_ROUNDS):
            on_text = None
            flush_pending = None
            if persist_owner_key:
                on_text, flush_pending = _delta_persister(persist_owner_key)
            agg = await chat_once(
                history,
                tools=build_tools(for_record=token_record),
                on_text=on_text,
            )
            if flush_pending:
                await flush_pending()
            total_usage["input"] += int(agg.get("usage", {}).get("input") or 0)
            total_usage["output"] += int(agg.get("usage", {}).get("output") or 0)

            content = str(agg.get("content") or "")
            for chunk in agg.get("chunks") or []:
                yield _sse("delta", {"text": chunk})
            if content:
                last_assistant_text = content
                if persist_owner_key:
                    update_pending_assistant(persist_owner_key, content)

            tool_calls = agg.get("tool_calls") or []
            assistant_msg = {
                "role": "assistant",
                "content": content if content else None,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": call.get("id", "call_%d" % idx),
                        "type": "function",
                        "function": {
                            "name": str(call.get("name") or ""),
                            "arguments": call.get("raw_arguments")
                            or json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for idx, call in enumerate(tool_calls)
                ]
            history.append(assistant_msg)
            if not tool_calls:
                break

            for call in tool_calls:
                name = str(call.get("name") or "")
                args = call.get("arguments") or {}
                yield _sse("tool_call", {"id": call.get("id", ""), "name": name, "arguments": args})
                result = await execute_tool(name, args, token_record)
                yield _sse("tool_result", {"name": name, "result": result})
                if name in ("sanyi_update_filters", "sanyi_clear_filters") and "error" not in result:
                    yield _sse("filter_update", {"name": name, "result": result})
                if name in ("sanyi_create_subscription", "sanyi_delete_subscription") and "error" not in result:
                    yield _sse("subscription_update", {"name": name, "result": result})
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", "call_%s" % name),
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
        else:
            history.append(
                {
                    "role": "user",
                    "content": "请根据上面的工具结果，直接给出最终回答，不要再调用工具。",
                }
            )
            on_text = None
            flush_pending = None
            if persist_owner_key:
                on_text, flush_pending = _delta_persister(persist_owner_key)
            agg = await chat_once(history, tools=None, on_text=on_text)
            if flush_pending:
                await flush_pending()
            total_usage["input"] += int(agg.get("usage", {}).get("input") or 0)
            total_usage["output"] += int(agg.get("usage", {}).get("output") or 0)
            last_assistant_text = str(agg.get("content") or "")
            if last_assistant_text and persist_owner_key:
                update_pending_assistant(persist_owner_key, last_assistant_text)
            for chunk in agg.get("chunks") or []:
                yield _sse("delta", {"text": chunk})

        log_usage(
            token_id=token_record["id"],
            service="chat",
            action="message",
            model=LLM_MODEL,
            input_tokens=total_usage["input"],
            output_tokens=total_usage["output"],
            cost=0,  # LLM 成本暂由平台承担；后续按模型单价换算额度
            status="ok",
            request_id=request_id,
            detail=json.dumps({"rounds": min(round_no + 1, CHAT_MAX_TOOL_ROUNDS)}, ensure_ascii=False),
        )
        if persist_owner_key and last_assistant_text:
            update_pending_assistant(persist_owner_key, last_assistant_text)
        if persist_owner_key:
            finalize_pending_assistant(persist_owner_key)
        yield _sse("done", {"usage": total_usage})
    except Exception as exc:  # noqa: BLE001 - 必须把错误结构化返回给前端
        if persist_owner_key:
            finalize_pending_assistant(persist_owner_key)
        log_usage(
            token_id=token_record["id"],
            service="chat",
            action="message",
            model=LLM_MODEL,
            status="error",
            request_id=request_id,
            detail="%s: %s" % (type(exc).__name__, exc),
        )
        yield _sse("error", {"message": "%s: %s" % (type(exc).__name__, exc)})
    finally:
        if persist_owner_key:
            finalize_pending_assistant(persist_owner_key)
