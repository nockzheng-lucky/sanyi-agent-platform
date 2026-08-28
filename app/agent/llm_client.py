"""OpenAI 兼容 LLM 客户端。

平台承担模型成本。这里只做流式调用与用量统计；
后续要控制成本时，在 config 层加：模型分级、月预算、每日上限、缓存。
"""
import json
from typing import Any, Dict, List, Optional

import httpx

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MOCK, LLM_MODEL


class LLMError(RuntimeError):
    pass


async def chat_once(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """调用一次 chat.completions，返回 content / tool_calls / usage。"""
    if LLM_MOCK:
        return _mock_once(messages, tools)

    if not LLM_API_KEY:
        raise LLMError("未配置 LLM_API_KEY；本地联调可先设 LLM_MOCK=1")

    payload: Dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Authorization": "Bearer %s" % LLM_API_KEY, "Content-Type": "application/json"}

    content_parts: List[str] = []
    tool_parts: Dict[int, Dict[str, Any]] = {}
    usage: Dict[str, int] = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        async with client.stream(
            "POST", "%s/chat/completions" % LLM_BASE_URL, headers=headers, json=payload
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:500]
                raise LLMError("LLM 请求失败 %s: %s" % (resp.status_code, body))
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = {
                        "input": int(chunk["usage"].get("prompt_tokens") or 0),
                        "output": int(chunk["usage"].get("completion_tokens") or 0),
                    }
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_parts.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if tc.get("function", {}).get("name"):
                        slot["name"] = tc["function"]["name"]
                    if tc.get("function", {}).get("arguments"):
                        slot["arguments"] += tc["function"]["arguments"]

    tool_calls = []
    for slot in tool_parts.values():
        if slot["name"]:
            raw_arguments = slot["arguments"] or "{}"
            try:
                args = json.loads(raw_arguments)
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": slot["id"] or "call_%d" % len(tool_calls),
                    "name": slot["name"],
                    "arguments": args,
                    "raw_arguments": raw_arguments,
                }
            )
    return {
        "content": "".join(content_parts),
        "chunks": list(content_parts),
        "tool_calls": tool_calls,
        "usage": usage,
    }


def _mock_once(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """本地假模型：不产生成本，用于跑通链路。

    触发规则：如果本轮提供工具，且最近的用户消息看起来在问因子/信号，就调用第一个因子。
    """
    if any(msg.get("role") == "tool" for msg in messages):
        final = "（本地假模型）我已经拿到三易引擎的因子结果。"
        "请把结果里的 generatedAt、summary 和 riskNote 展示给用户；"
        "当前为 MOCK 结果，真实规则接入后这里会由 LLM 生成最终解读。"
        return {
            "content": final,
            "chunks": [final],
            "tool_calls": [],
            "usage": {"input": 100, "output": 40},
        }

    last_user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user = str(msg.get("content") or "")
            break

    need_tool = bool(tools) and any(
        k in last_user for k in ("因子", "信号", "地门", "诀", "破诀", "列表", "factor")
    )
    if need_tool:
        if any(k in last_user for k in ("诀", "破诀", "jue")):
            factor_key = "jue_direction"
            params = {"limit": 20}
            raw_arguments = "{\"factorKey\": \"jue_direction\", \"params\": {\"limit\": 20}}"
        else:
            factor_key = "dimen_gate_signal"
            params = {"maxAgeMinutes": 120}
            raw_arguments = "{\"factorKey\": \"dimen_gate_signal\", \"params\": {\"maxAgeMinutes\": 120}}"

        tool_calls = []
        # 本地 MOCK 也演示“自然语言调整筛选条件”：先存 filters，再执行查询。
        if any(k in last_user for k in ("只看", "只要", "筛选", "保留", "改成", "调整为")):
            freq = "15m" if ("15分钟" in last_user or "15 分钟" in last_user) else ("5m" if ("5分钟" in last_user or "5 分钟" in last_user) else "1h")
            filters = {"frequencies": [freq]}
            filter_raw = "{\"factorKey\": \"%s\", \"filters\": {\"frequencies\": [\"%s\"]}}" % (factor_key, freq)
            tool_calls.append(
                {
                    "id": "call_mock_filter",
                    "name": "sanyi_update_filters",
                    "arguments": {"factorKey": factor_key, "filters": filters},
                    "raw_arguments": filter_raw,
                }
            )

        tool_calls.append(
            {
                "id": "call_mock_1",
                "name": "sanyi_evaluate_factor",
                "arguments": {"factorKey": factor_key, "params": params},
                "raw_arguments": raw_arguments,
            }
        )
        return {
            "content": "",
            "chunks": [],
            "tool_calls": tool_calls,
            "usage": {"input": 120, "output": 20},
        }
    final = "（本地假模型）我是三易引擎 Agent。当前没有配置真实 LLM，"
    "链路处于 MOCK 模式。你可以问我“现在有什么因子”或“看看 15 分钟地门开信号”。"
    return {
        "content": final,
        "chunks": [final],
        "tool_calls": [],
        "usage": {"input": 60, "output": 30},
    }
