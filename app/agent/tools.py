"""Agent 工具白名单。

页面 Agent 只能调用这里列出的工具；未来开放给用户自己的 Agent 时，
MCP/REST 复用同一套 schema，保证行为一致。
"""
import json
from typing import Any, Dict, List

from fastapi import HTTPException

from ..factor_registry import registry


def build_tools() -> List[Dict[str, Any]]:
    factor_keys = [d["factorKey"] for d in registry.descriptors()]
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
                    "params 严格按因子 paramsSchema 填写。结果里的 generatedAt 必须转述给用户。"
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
    ]


async def execute_tool(name: str, arguments: Dict[str, Any], token_record: dict) -> Dict[str, Any]:
    """执行工具并返回给模型的结果。异常也要作为工具结果返回，而不是中断会话。"""
    try:
        if name == "sanyi_list_factors":
            return {"factors": registry.descriptors()}
        if name == "sanyi_evaluate_factor":
            factor_key = str(arguments.get("factorKey") or "").strip()
            if not factor_key:
                return {"error": "缺少 factorKey"}
            params = arguments.get("params") or {}
            if not isinstance(params, dict):
                return {"error": "params 必须是对象"}
            result = await registry.evaluate(
                token_record=token_record,
                factor_key=factor_key,
                params=params,
            )
            return result
        return {"error": "未知工具：%s" % name}
    except HTTPException as exc:
        return {"error": json.dumps(exc.detail, ensure_ascii=False)}
    except Exception as exc:  # noqa: BLE001 - Agent 上下文需要结构化错误
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
